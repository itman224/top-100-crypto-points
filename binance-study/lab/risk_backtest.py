#!/usr/bin/env python3
"""Exploratory one-bar fixed TP/SL sensitivity for train-selected candidates.

Signal at previous completed close; enter next open, stop -1%, take +2%,
otherwise exit at close; both levels hit -> stop first. MFE/MAE are extrema of
the *whole* trade candle, not necessarily achieved before the simulated exit.
Independent per-symbol/strategy positions, no shared-capital portfolio.
"""
import argparse, hashlib, json, os
from pathlib import Path
import numpy as np
import pandas as pd
from data import load_symbol
from indicators import compute_signals
from backtest import DURATIONS, SPLITS, strategy_signals, COST, experiment_hash

ROOT=Path(__file__).resolve().parent
VERSION='risk-one-bar-v1'

def simulate(op,high,low,close,side,sl=.01,tp=.02,cost=COST):
    if side not in (-1,1) or not (0<sl<1 and tp>0): raise ValueError('invalid side or levels')
    stop=op*(1-side*sl); take=op*(1+side*tp)
    hit_stop=low<=stop if side==1 else high>=stop
    hit_take=high>=take if side==1 else low<=take
    if hit_stop: gross=-sl; exit_reason='stop_ambiguous' if hit_take else 'stop'
    elif hit_take: gross=tp; exit_reason='take'
    else: gross=side*(close/op-1); exit_reason='close'
    mfe=(high/op-1) if side==1 else (1-low/op)
    mae=(1-low/op) if side==1 else (high/op-1)
    return gross,gross-cost,mfe,mae,exit_reason

def selected(path):
    result=json.loads(Path(path).read_text())
    return [(s['strategy'],s['timeframe'],s['direction']) for s in result['strategies'] if s.get('validation_pass')]

def run(symbol, picked, output, loaded=None):
    selection_hash=hashlib.sha256(json.dumps(picked,sort_keys=True).encode()).hexdigest()
    path=Path(output)/f'{symbol}.json'; path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        old=json.loads(path.read_text())
        if old.get('version')==VERSION and old.get('selection_hash')==selection_hash and old.get('experiment_hash')==experiment_hash():
            print(f'{symbol}: checkpoint',flush=True);return
    data=loaded if loaded is not None else load_symbol(symbol); records=[]
    for tf in DURATIONS:
        picks=[p for p in picked if p[1]==tf]
        if not picks: continue
        frame=data[tf].sort_index(); ns=frame.index.asi8
        if frame.empty: continue
        bounds=np.flatnonzero(np.r_[True,np.diff(ns)!=DURATIONS[tf]*1_000_000_000,True])
        for start,end in zip(bounds[:-1],bounds[1:]):
            part=frame.iloc[start:end]
            if len(part)<2:continue
            sigs=strategy_signals(compute_signals(part))
            for name,_,direction in picks:
                if name not in sigs: continue
                side=1 if direction=='LONG' else -1
                active=sigs[name].shift(1).to_numpy()==side
                for split,(a,b) in SPLITS.items():
                    mask=active & (part.index>=pd.Timestamp(a,tz='UTC')) & (part.index<pd.Timestamp(b,tz='UTC'))
                    trade=part.loc[mask,['open','high','low','close']]
                    if trade.empty: continue
                    vals=[simulate(*r,side) for r in trade.itertuples(index=False,name=None)]
                    gross=np.array([v[0] for v in vals]);net=np.array([v[1] for v in vals]); mfe=np.array([v[2] for v in vals]);mae=np.array([v[3] for v in vals])
                    wins=net[net>0].sum();loss=-net[net<0].sum()
                    records.append({'symbol':symbol,'strategy':name,'timeframe':tf,'direction':direction,'split':split,'trades':len(vals),'gross_mean':float(gross.mean()),'net_mean':float(net.mean()),'gross_sum':float(gross.sum()),'net_sum':float(net.sum()),'win_rate':float((net>0).mean()),'profit_factor':float(wins/loss) if loss else None,'mfe_full_bar_mean':float(mfe.mean()),'mae_full_bar_mean':float(mae.mean()),'stop_count':sum(v[4].startswith('stop') for v in vals),'take_count':sum(v[4]=='take' for v in vals),'ambiguous_count':sum(v[4]=='stop_ambiguous' for v in vals)})
    obj={'version':VERSION,'experiment_hash':experiment_hash(),'selection_hash':selection_hash,'selected_candidates':picked,'methodology':{'entry':'next completed candle open after signal close','exit':'same bar, fixed 1% stop, 2% take, else close','ambiguous':'stop first','cost_roundtrip_fraction':COST,'funding':'not modeled','capital':'independent trades; no shared portfolio','mfe_mae':'full-candle extremes; upper bounds, not necessarily realized before exit','selection':'train/validation candidates inherited from results.json; TP and SL fixed sensitivity, not optimized; holdout exploratory'},'symbol':symbol,'records':records,'data_quality':data['quality']}
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(obj,allow_nan=False,separators=(',',':')));os.replace(tmp,path)
    print(f'{symbol}: {len(records)} risk rows',flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--symbol',action='append',required=True);p.add_argument('--selection',default=str(ROOT/'results/results.json'));p.add_argument('--output',default=str(ROOT/'results/risk'));p.add_argument('--self-test',action='store_true');args=p.parse_args()
    if args.self_test:
        assert simulate(100,103,98,101,1)[4]=='stop_ambiguous'
        assert simulate(100,102,97,99,-1)[4]=='stop_ambiguous'
        assert abs(simulate(100,100.5,99.5,100.2,1)[0]-.002)<1e-10
        print('tests passed');return
    picks=selected(args.selection)
    for sym in args.symbol: run(sym,picks,args.output)
if __name__=='__main__':main()
