#!/usr/bin/env python3
"""Pool confusion matrices and trade moments; select before holdout is inspected."""
import json, math, os, statistics
from collections import defaultdict
from pathlib import Path
from datetime import datetime,timezone
from backtest import VERSION, finite_div, COST, experiment_hash

ROOT=Path(__file__).resolve().parent

def pool(records):
    r={k:sum(x['metrics'].get(k,0) for x in records) for k in ('valid_samples','tp','fp','fn','tn','trades','wins','gross_return_sum','net_return_sum','net_positive_sum','net_negative_abs_sum','net_squared_sum','downside_squared_sum')}
    n=r['trades']; t=r['tp']; f=r['fp']; fn=r['fn']; tn=r['tn']; precision=finite_div(t,t+f); recall=finite_div(t,t+fn)
    mean=finite_div(r['net_return_sum'],n); variance=(r['net_squared_sum']-r['net_return_sum']**2/n)/(n-1) if n>1 else 0
    std=math.sqrt(max(0,variance)); downside=math.sqrt(r['downside_squared_sum']/n) if n else 0
    dd=[x['metrics']['max_drawdown_compounded'] for x in records if x['metrics']['max_drawdown_compounded'] is not None]
    r.update({'symbols':len({x['symbol'] for x in records}),'precision':precision,'recall':recall,'detection_rate':recall,'f1':finite_div(2*t,2*t+f+fn),'false_positive_rate':finite_div(f,f+tn),'event_base_rate':finite_div(t+fn,t+fn+f+tn),'predictive_lift':finite_div(precision,(t+fn)/(t+fn+f+tn)) if precision is not None and (t+fn) else None,
      'win_rate':finite_div(r['wins'],n),'gross_average_return':finite_div(r['gross_return_sum'],n),'net_average_return':mean,'expectancy':mean,'net_profit_fraction_sum':r['net_return_sum'],'profit_factor':finite_div(r['net_positive_sum'],r['net_negative_abs_sum']),'sharpe_per_trade':finite_div(mean,std) if mean is not None else None,'sortino_per_trade':finite_div(mean,downside) if mean is not None else None,'worst_symbol_segment_drawdown':min(dd) if dd else None})
    return r

def main():
    results=ROOT/'results'; files=sorted((results/'symbols').glob('*.json')); groups=defaultdict(list); symbols=[]; stale=[]
    for path in files:
        obj=json.loads(path.read_text());
        if obj.get('version')!=VERSION or obj.get('experiment_hash')!=experiment_hash(): stale.append(path.name);continue
        symbols.append(obj['symbol'])
        for row in obj['records']: groups[(row['strategy'],row['timeframe'],row['direction'],row['split'])].append(row)
    study_missing={}
    for path in files:
        obj=json.loads(path.read_text())
        if obj.get('version')!=VERSION or obj.get('experiment_hash')!=experiment_hash(): continue
        missing=0
        for interval in obj.get('data_quality',{}).get('missing_ranges',[]):
            try:
                first,last=(interval if isinstance(interval,list) else (interval['start'],interval['end']))
                start=datetime.fromisoformat(first.replace('Z','+00:00')); end=datetime.fromisoformat(last.replace('Z','+00:00'))
                lo=datetime(2026,4,1,tzinfo=timezone.utc); hi=datetime(2026,10,28,tzinfo=timezone.utc)
                missing+=max(0,int((min(end,hi)-max(start,lo)).total_seconds()//300))
            except (KeyError,ValueError,TypeError): pass
        study_missing[obj['symbol']]=missing
    out=[]
    for (strategy,timeframe,direction,split),rows in sorted(groups.items()):
        out.append({'strategy':strategy,'timeframe':timeframe,'direction':direction,'split':split,'metrics':pool(rows)})
    # Freeze candidates from train only, assess validation without holdout tuning.
    candidates=defaultdict(list)
    for x in out:
        m=x['metrics']
        if x['split']=='train' and not x['strategy'].startswith('baseline_') and m['trades']>=30 and m['net_average_return'] is not None and m['net_average_return']>0:
            candidates[(x['timeframe'],x['direction'])].append(x)
    frozen=[]
    lookup={(x['strategy'],x['timeframe'],x['direction'],x['split']):x for x in out}
    for key,arr in candidates.items():
        for x in sorted(arr,key=lambda v:v['metrics']['net_average_return'],reverse=True)[:3]:
            validation=lookup.get((x['strategy'],x['timeframe'],x['direction'],'validation'))
            frozen.append({'strategy':x['strategy'],'timeframe':x['timeframe'],'direction':x['direction'],'selection':'top three train net mean with >=30 train trades, before holdout','train':x['metrics'],'validation':validation['metrics'] if validation else None,'validation_pass':bool(validation and validation['metrics']['trades']>=30 and (validation['metrics']['net_average_return'] or 0)>0)})
    # Validation is the last selection gate; holdout is never used for selection.
    for x in frozen:
        h=lookup.get((x['strategy'],x['timeframe'],x['direction'],'holdout')) if x['validation_pass'] else None
        x['holdout']=h['metrics'] if h else None
        x['holdout_pass']=bool(h and h['metrics']['trades']>=30 and (h['metrics']['net_average_return'] or 0)>0 and (h['metrics']['profit_factor'] or 0)>1)
    obj={'schema_version':'1','status':'partial' if len(symbols)<100 or any(study_missing.values()) else 'complete','study_missing_5m_by_symbol':{k:v for k,v in study_missing.items() if v},'symbols_processed':len(symbols),'requested_symbols':100,'symbols':symbols,'stale_checkpoints':stale,'leaderboard':out,'strategies':frozen,'insights':[],
       'methodology':{'version':VERSION,'splits':{'train':'2026-04-01 through 2026-06-30','validation':'2026-07-01 through 2026-08-31','holdout':'2026-09-01 through 2026-10-27'},'signal':'Computed at completed candle close; shifted one bar to execution at next open.','entry_exit':'Open to close of one completed next candle; no TP/SL. Signal only after preceding candle close. No shared portfolio or simultaneous position sizing model.', 'cost_roundtrip_fraction':COST,'fee':'0.04% per side','slippage':'0.01% per side','funding':'Not modeled; historical funding unavailable','metrics':'Confusion counts and trade moments pooled; Sharpe/Sortino are unannualized per trade, drawdown is worst symbol-segment, not portfolio.', 'qualification_note':'Exploratory threshold: train, validation and holdout each require >=30 trades and positive mean net return; holdout also profit factor >1. Does not correct for multiple comparisons or establish robustness.', 'selection':'Top 3 indicators per timeframe/direction by positive net mean on train with >=30 trades; validation with >=30 trades and positive net mean is required before holdout is shown. Holdout never used for selection.', 'boundary':'Float input uses Decimal reconstruction for near 1% body boundary; this cannot recover precision absent from upstream floats.'}}
    p=results/'results.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(obj,indent=2,allow_nan=False));os.replace(tmp,p)
    print(f'{len(symbols)} symbols; {len(out)} benchmark rows; {len(frozen)} train-selected candidates')
if __name__=='__main__':main()
