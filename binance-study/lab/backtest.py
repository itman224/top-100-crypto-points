#!/usr/bin/env python3
"""Causal one-candle benchmark. Signals are computed at close and traded next open.

Experiments are independent per symbol/timeframe (not a shared-capital portfolio).
The target is the traded bar's body, not intrabar excursions. No TP/SL is used.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys, time
from pathlib import Path
from decimal import Decimal
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
sys.path.insert(0, str(ROOT))
SPLITS = {'train':('2026-04-01','2026-07-01'), 'validation':('2026-07-01','2026-09-01'), 'holdout':('2026-09-01','2026-10-28')}
DURATIONS = {'5m':300,'15m':900,'1h':3600,'4h':14400,'1d':86400}
DIRECTIONALS = ['rsi_reversal_14','ema_trend_20_50','macd_momentum_12_26_9','bb_reversal_20_2','donchian_breakout_20']
COST = 0.001 # 0.04% fee + 0.01% slippage per side
VERSION = 'one-candle-v3'
def experiment_hash():
    h=hashlib.sha256()
    for name in ('backtest.py','indicators.py','data.py'):
        h.update((ROOT/name).read_bytes())
    return h.hexdigest()

def finite_div(a,b):
    return float(a/b) if b else None

def score(signal, change, side, cost=COST, actual_override=None):
    """Complete eligible bars only; exact strict target, one direction at a time."""
    signal=np.asarray(signal); change=np.asarray(change, dtype=float)
    valid=np.isfinite(signal) & np.isfinite(change)
    signal=signal[valid]; change=change[valid]
    predicted=signal==side; actual=change*side > .01 if actual_override is None else np.asarray(actual_override,dtype=bool)[valid]
    tp=int((predicted&actual).sum()); fp=int((predicted&~actual).sum())
    fn=int((~predicted&actual).sum()); tn=int((~predicted&~actual).sum())
    gross=side*change[predicted]; net=gross-cost
    wins=int((net>0).sum()); gp=float(net[net>0].sum()); loss=float(-net[net<0].sum())
    n=len(net); mean=float(net.mean()) if n else None
    std=float(net.std(ddof=1)) if n>1 else 0
    downside=net[net<0]; downside_std=float(np.sqrt((np.minimum(net,0)**2).mean())) if n else 0
    wealth=np.cumprod(1+net) if n else np.array([])
    drawdown=float(np.min(wealth/np.maximum.accumulate(np.r_[1.,wealth])[1:]-1)) if n else None
    positive=gross[gross>0]; negative=gross[gross<0]
    precision=finite_div(tp,tp+fp); recall=finite_div(tp,tp+fn)
    return {'valid_samples':int(valid.sum()),'boundary_or_invalid_excluded':int(len(valid)-valid.sum()),'tp':tp,'fp':fp,'fn':fn,'tn':tn,'event_base_rate':finite_div(tp+fn,len(signal)),
      'precision':precision,'recall':recall,'detection_rate':recall,'f1':finite_div(2*tp,2*tp+fp+fn),'false_positive_rate':finite_div(fp,fp+tn),
      'predictive_lift':finite_div(precision,(tp+fn)/len(signal)) if precision is not None and len(signal) else None,
      'trades':n,'wins':wins,'net_positive_sum':gp,'net_negative_abs_sum':loss,'net_squared_sum':float(np.sum(net**2)),'downside_squared_sum':float(np.sum(np.minimum(net,0)**2)),'win_rate':finite_div(wins,n),'gross_return_sum':float(gross.sum()),'net_return_sum':float(net.sum()),
      'gross_average_return':float(gross.mean()) if n else None,'net_average_return':mean,'expectancy':mean,'profit_factor':finite_div(gp,loss),
      'sharpe_per_trade':finite_div(mean,std) if mean is not None else None,'sortino_per_trade':finite_div(mean,downside_std) if mean is not None else None,
      'max_drawdown_compounded':drawdown,'risk_reward_gross':finite_div(float(positive.mean()),float(-negative.mean())) if len(positive) and len(negative) else None,
      'net_profit_fraction_sum':float(net.sum()),'net_profit_compounded':float(wealth[-1]-1) if n else None,
      'mfe':None,'mae':None}

def strategy_signals(s):
    from indicators import SIGNALS
    result={name:s[name] for name in SIGNALS if name in s.columns}
    if 'trend_strength_14' in s:
        for name in ('ema_trend_20_50','macd_momentum_12_26_9','donchian_breakout_20'):
            if name in result: result[name+'__adx25']=result[name].where(s['trend_strength_14'].notna(),np.nan).where(s['trend_strength_14']!=0,0)
    if 'volume_surge_20' in s:
        for name in ('donchian_breakout_20','macd_momentum_12_26_9'):
            if name in result: result[name+'__volume_surge']=result[name].where(s['volume_surge_20'].notna(),np.nan).where(s['volume_surge_20']!=0,0)
    if all(x in result for x in ('ema_trend_20_50','macd_momentum_12_26_9')):
        a,b=result['ema_trend_20_50'],result['macd_momentum_12_26_9']
        result['ema_macd_agreement']=pd.Series(np.where(a==b,a,0),index=a.index).where(a.notna()&b.notna())
    if all(x in result for x in ('ema_trend_20_50','rsi_reversal_14')):
        a,b=result['ema_trend_20_50'],result['rsi_reversal_14']
        result['ema_rsi_agreement']=pd.Series(np.where(a==b,a,0),index=a.index).where(a.notna()&b.notna())
    result['baseline_always_long']=pd.Series(1.,index=s.index)
    result['baseline_always_short']=pd.Series(-1.,index=s.index)
    return result

def analyze_frame(symbol,timeframe,df,compute):
    if df.empty: return []
    df=df.sort_index(); sec=DURATIONS[timeframe]; ns=df.index.asi8
    assert df.index.is_unique
    # Compute each contiguous run separately; no rolling calculations across missing bars.
    boundaries=np.flatnonzero(np.r_[True,np.diff(ns)!=sec*1_000_000_000,len(ns)>0])
    chunks=[]
    for begin,end in zip(boundaries[:-1],boundaries[1:]):
        part=df.iloc[begin:end]
        if len(part)<2: continue
        signals=compute(part); strategies=strategy_signals(signals)
        change=part['close']/part['open']-1
        up=(change.to_numpy() > .01); down=(change.to_numpy() < -.01)
        # Re-evaluate boundary-near ratios using decimal representations of available prices.
        near=np.flatnonzero(np.abs(np.abs(change.to_numpy())-.01)<1e-10)
        for i in near:
            op=Decimal(str(part['open'].iloc[i])); cl=Decimal(str(part['close'].iloc[i]))
            up[i]=cl > op*Decimal('1.01'); down[i]=cl < op*Decimal('0.99')
        for name,values in strategies.items():
            shifted=values.shift(1)
            for split,(start,end_date) in SPLITS.items():
                mask=(part.index>=pd.Timestamp(start,tz='UTC')) & (part.index<pd.Timestamp(end_date,tz='UTC'))
                if not mask.any(): continue
                for side in (1,-1):
                    m=score(shifted[mask],change[mask],side,actual_override=(up if side==1 else down)[mask])
                    if m['valid_samples']:
                        chunks.append({'symbol':symbol,'timeframe':timeframe,'split':split,'strategy':name,'direction':'LONG' if side==1 else 'SHORT','metrics':m})
    # Multiple contiguous runs remain separate, so do not misrepresent compounded drawdown.
    for i,x in enumerate(chunks): x['segment']=i
    return chunks

def process_symbol(symbol,load_symbol,compute,out):
    target=out/'symbols'/f'{symbol}.json'; target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        existing=json.loads(target.read_text());
        if existing.get('version')==VERSION and existing.get('experiment_hash')==experiment_hash(): print(f'{symbol}: checkpoint',flush=True); return
    started=time.monotonic(); data=load_symbol(symbol)
    records=[]
    frames=data.get('frames',data)
    for tf in DURATIONS:
        if tf in frames: records.extend(analyze_frame(symbol,tf,frames[tf],compute))
    obj={'version':VERSION,'experiment_hash':experiment_hash(),'symbol':symbol,'records':records,'data_quality':data.get('quality',{}),'duration_seconds':time.monotonic()-started}
    tmp=target.with_suffix('.tmp'); tmp.write_text(json.dumps(obj,allow_nan=False,separators=(',',':'))); os.replace(tmp,target)
    print(f'{symbol}: {len(records)} segment results',flush=True)

def main():
    p=argparse.ArgumentParser();p.add_argument('--universe',default=str(PROJECT/'output/universe.json'));p.add_argument('--output',default=str(ROOT/'results'));p.add_argument('--symbol',action='append');p.add_argument('--limit',type=int);a=p.parse_args()
    from data import load_symbol
    from indicators import compute_signals
    universe=json.loads(Path(a.universe).read_text());symbols=a.symbol or [s['symbol'] for s in universe['symbols']]
    if a.limit: symbols=symbols[:a.limit]
    out=Path(a.output)
    for symbol in symbols:
        try: process_symbol(symbol,load_symbol,compute_signals,out)
        except Exception as exc:
            print(f'{symbol}: FAILED {type(exc).__name__}: {exc}',file=sys.stderr,flush=True)
            (out/'failures').mkdir(parents=True,exist_ok=True);(out/'failures'/f'{symbol}.json').write_text(json.dumps({'symbol':symbol,'error':str(exc)}))
if __name__=='__main__':main()
