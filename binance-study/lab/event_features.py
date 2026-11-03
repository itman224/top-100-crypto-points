#!/usr/bin/env python3
"""Annotate stored events using only the prior completed candle's signals.

One symbol at a time, independent of backtest checkpoints. Signal state restarts
at every missing candle. Event absence from the retrieved market data and a
missing prior candle are distinguished. No future bars enter signal formation.
"""
from __future__ import annotations
import argparse, gzip, hashlib, json, os
from importlib.metadata import version
from pathlib import Path
import numpy as np
import pandas as pd
from data import load_symbol
from indicators import compute_signals

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
SECONDS={'5m':300,'15m':900,'1h':3600,'4h':14400,'1d':86400}

def signal_frames(frame, timeframe, compute=compute_signals):
    """Causal features for each complete frame; restart after any time gap."""
    frame=frame.sort_index()
    if not frame.index.is_unique: raise ValueError('duplicate candle timestamps')
    if frame.empty: return pd.DataFrame(index=frame.index)
    t=frame.index.asi8
    starts=np.flatnonzero(np.r_[True,np.diff(t)!=SECONDS[timeframe]*1_000_000_000])
    ends=np.r_[starts[1:],len(frame)]
    return pd.concat([compute(frame.iloc[start:end]) for start,end in zip(starts,ends)])

def annotate(event, frame, signals):
    stamp=pd.Timestamp(event['open_time'])
    if stamp.tzinfo is None: raise ValueError('event timestamp requires timezone')
    stamp=stamp.tz_convert('UTC')
    previous=stamp-pd.Timedelta(seconds=SECONDS[event['timeframe']])
    result={'event_id':event['event_id'],'symbol':event['symbol'],'timeframe':event['timeframe'],
            'event_open_time':stamp.isoformat().replace('+00:00','Z'),
            'decision_time':stamp.isoformat().replace('+00:00','Z'),
            'indicator_candle_open_time':previous.isoformat().replace('+00:00','Z'),
            'event_direction':event['direction'],'event_source_ids':event.get('source_ids',[]),
            'indicator_signals':{},'quality':'complete'}
    if stamp not in frame.index:
        result['quality']='event_candle_unavailable'
    if previous not in frame.index:
        result['quality']='prior_candle_unavailable' if stamp in frame.index else 'event_and_prior_candle_unavailable'
        return result
    if previous not in signals.index:
        result['quality']='indicator_state_unavailable'
        return result
    row=signals.loc[previous]
    # null is missing warmup; zero is a real neutral/disabled reading.
    result['indicator_signals']={k:(None if pd.isna(v) else int(v)) for k,v in row.items()}
    if any(v is None for v in result['indicator_signals'].values()):
        result['quality']='warmup_incomplete' if result['quality']=='complete' else result['quality']+';warmup_incomplete'
    return result

def _valid_output(path, expected_count):
    try:
        with gzip.open(path,'rt',encoding='utf-8') as f:
            count=0
            for line in f:
                if not isinstance(json.loads(line),dict): return False
                count+=1
        return count==expected_count
    except (OSError,EOFError,ValueError,UnicodeError):
        return False

def annotate_symbol(symbol, loaded=None, output=None, events=None):
    output=Path(output) if output else ROOT/'results'/'event_features'
    events=Path(events) if events else PROJECT/'output'/'events'
    symbol=symbol.upper()
    if not symbol.isalnum(): raise ValueError('invalid symbol')
    sources=sorted((events/symbol).glob('*.jsonl.gz'))
    if not sources: raise ValueError('no source event files')
    h=hashlib.sha256()
    for name in ('event_features.py','data.py','indicators.py'): h.update((ROOT/name).read_bytes())
    file_hashes={str(p.relative_to(PROJECT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    output.mkdir(parents=True,exist_ok=True); dest=output/f'{symbol}.jsonl.gz'; target=output/f'{symbol}.meta.json'
    if target.exists() and dest.exists():
        try:
            previous=json.loads(target.read_text())
            qc=previous.get('quality_counts',{})
            statuses=[x.get('status') for x in previous.get('market_data_quality',{}).get('sources',[])]
            if (previous.get('experiment_hash')==h.hexdigest() and previous.get('event_file_sha256')==file_hashes
                and previous.get('pandas_ta_classic_version')==version('pandas-ta-classic')
                and not any('unavailable' in k for k in qc) and 'unavailable' not in statuses
                and _valid_output(dest, previous['event_count'])):
                print(f'{symbol}: checkpoint ({previous["event_count"]} events)',flush=True); return
        except (OSError,ValueError,KeyError): pass
    if loaded is None: loaded=load_symbol(symbol)
    signal_map={tf:signal_frames(loaded[tf],tf) for tf in SECONDS}
    temp=dest.with_suffix('.tmp'); counts={}; total=0
    try:
        with gzip.open(temp,'wt',encoding='utf-8') as out:
            for source in sources:
                with gzip.open(source,'rt',encoding='utf-8') as f:
                    for line in f:
                        event=json.loads(line);tf=event['timeframe']
                        if event['symbol']!=symbol or tf not in SECONDS: raise ValueError('invalid event')
                        obj=annotate(event,loaded[tf],signal_map[tf]);out.write(json.dumps(obj,separators=(',',':'),allow_nan=False)+'\n')
                        counts[obj['quality']]=counts.get(obj['quality'],0)+1;total+=1
        os.replace(temp,dest)
    finally:
        temp.unlink(missing_ok=True)
    metadata={'symbol':symbol,'event_count':total,'quality_counts':counts,'source_event_files':[str(p.relative_to(PROJECT)) for p in sources],
              'feature_definition':'signals at close of immediately preceding complete candle, decision at event open; gap resets; no future data',
              'experiment_hash':h.hexdigest(),'pandas_ta_classic_version':version('pandas-ta-classic'),
              'event_file_sha256':file_hashes,'market_data_quality':loaded['quality']}
    tmp=target.with_suffix('.tmp'); tmp.write_text(json.dumps(metadata,indent=2));os.replace(tmp,target)
    print(f'{symbol}: {total} events; quality {counts}',flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    choice=p.add_mutually_exclusive_group(required=True);choice.add_argument('--symbol');choice.add_argument('--all-universe',action='store_true')
    p.add_argument('--universe',type=Path,default=PROJECT/'output'/'universe.json');p.add_argument('--output',type=Path,default=ROOT/'results'/'event_features');p.add_argument('--events',type=Path,default=PROJECT/'output'/'events');args=p.parse_args()
    symbols=[args.symbol] if args.symbol else [x['symbol'] for x in json.loads(args.universe.read_text())['symbols']]
    failures=[]
    for symbol in symbols:
        try: annotate_symbol(symbol, output=args.output, events=args.events)
        except Exception as e:
            print(f'{symbol}: FAILED {type(e).__name__}: {e}',flush=True);failures.append(symbol)
    if failures: raise SystemExit(1)
if __name__=='__main__': main()
