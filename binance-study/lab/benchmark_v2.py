#!/usr/bin/env python3
"""Fixed-hypothesis, monthly per-symbol benchmark (v2).

Each function is selected explicitly, not inferred from its category. All inputs
are completed candles. Signals lag one bar; no split is used for parameter
selection. The formerly inspected 2026 holdout is exploratory. Missing and
unsupported functions remain explicit. One symbol lives in memory at a time.
"""
import argparse,calendar,gzip,hashlib,inspect,json,os,sys,time,warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import numpy as np,pandas as pd,pandas_ta_classic as ta
ROOT=Path(__file__).resolve().parent; PROJECT=ROOT.parent
sys.path.insert(0,str(ROOT))
from data import load_symbol
from backtest import score,DURATIONS
from decimal import Decimal

# Prespecified executable directional hypotheses. Overlay: price vs smooth
# level; signed oscillator: sign as trend; bounded oscillator: conventional
# reversal thresholds. Band: countertrend outside lower/upper band.
OVERLAYS='alma dema ema fwma hma kama linreg midpoint midprice pwma rma sinwma sma swma t3 tema trima tsf vidya vwma wma zlma'.split()
ZERO='ao apo cci cmo coppock mom roc rocp slope'.split()
BOUNDED={'rsi':(30,70),'rsx':(30,70),'uo':(30,70),'mfi':(20,80),'willr':(-80,-20)}
BANDS={'bbands':('BBL_','BBU_'),'kc':('KCL','KCU')}
# Multi-component outputs: named oscillator component only; omit signal lines.
MULTI={'fisher':'FISHERT_', 'ppo':'PPO_', 'trix':'TRIX_', 'tsi':'TSI_','stoch':'STOCHk_', 'stochf':'STOCHFk_', 'stochrsi':'STOCHRSIk_'}
MULTI_BOUNDED={'stoch':(20,80),'stochf':(20,80),'stochrsi':(20,80)}
SPECS={**{n:('overlay',None) for n in OVERLAYS},**{n:('zero',None) for n in ZERO},**{n:('bounded',v) for n,v in BOUNDED.items()},**{n:('band',v) for n,v in BANDS.items()},**{n:('multi_bounded' if n in MULTI_BOUNDED else 'multi_zero',MULTI_BOUNDED.get(n)) for n in MULTI}}
# Explicit volume-led, directional flow hypotheses (not raw volume sign).
SPECS.update({'cmf':('zero',None),'efi':('zero',None),'adosc':('zero',None),'obv':('change10',None),'pvt':('change10',None),'ad':('change10',None),'wad':('change10',None)})
VERSION='benchmark-v2-monthly-1'

def experiment_hash():
 h=hashlib.sha256()
 for name in ('benchmark_v2.py','data.py','backtest.py'): h.update((ROOT/name).read_bytes())
 return h.hexdigest()

def invoke(name, frame):
 fn=getattr(ta,name); sig=inspect.signature(fn)
 cols={'open_':'open','open':'open','high':'high','low':'low','close':'close','volume':'volume'}
 kwargs={p:frame[c] for p,c in cols.items() if p in sig.parameters}
 if 'talib' in sig.parameters: kwargs['talib']=False
 # Only unshifted, explicitly causal implementations are permitted.
 if 'offset' in sig.parameters: kwargs['offset']=0
 with warnings.catch_warnings():
  warnings.simplefilter('ignore')
  return fn(**kwargs)

def signal(name,frame):
 kind,args=SPECS[name]; x=invoke(name,frame)
 if x is None: raise ValueError('indicator returned no output (insufficient warmup)')
 if kind=='band':
  if not isinstance(x,pd.DataFrame): raise ValueError('band output was not a dataframe')
  lo=[c for c in x.columns if str(c).startswith(args[0])];hi=[c for c in x.columns if str(c).startswith(args[1])]
  if len(lo)!=1 or len(hi)!=1: raise ValueError('band components ambiguous')
  a=x[lo[0]];b=x[hi[0]];valid=a.notna()&b.notna();up=frame.close<a;down=frame.close>b
 else:
  if isinstance(x,pd.DataFrame):
   prefix=MULTI.get(name)
   matches=[c for c in x.columns if prefix and str(c).startswith(prefix)]
   if len(matches)!=1: raise ValueError('directional component ambiguous')
   x=x[matches[0]]
  if not isinstance(x,pd.Series): raise ValueError('indicator is not a series')
  if not x.index.equals(frame.index): raise ValueError('indicator index changed')
  valid=x.notna() & np.isfinite(x)
  if kind=='overlay': up=frame.close>x;down=frame.close<x
  elif kind in ('zero','multi_zero'):up=x>0;down=x<0
  elif kind in ('bounded','multi_bounded'):up=x<args[0];down=x>args[1]
  elif kind=='change10': valid=valid & x.shift(10).notna();up=x>x.shift(10);down=x<x.shift(10)
  else: raise ValueError('unrecognized hypothesis')
 out=pd.Series(np.nan,index=frame.index,dtype='float64');out.loc[valid]=0;out.loc[valid & up]=1;out.loc[valid & down]=-1
 return out

def month_masks(idx):
 for year,month in ((2026,i) for i in range(4,11)):
  start=pd.Timestamp(year=year,month=month,day=1,tz='UTC'); end=(start+pd.offsets.MonthBegin(1))
  if month==10:end=pd.Timestamp('2026-10-28',tz='UTC')
  yield f'{year}-{month:02d}',(idx>=start)&(idx<end)

def run_frame(symbol,tf,frame):
 if frame.empty:return [],[]
 frame=frame.sort_index(); assert frame.index.is_unique
 split=np.flatnonzero(np.r_[True,np.diff(frame.index.asi8)!=DURATIONS[tf]*1_000_000_000,True]); records=[];errors=[]
 for begin,end in zip(split[:-1],split[1:]):
  f=frame.iloc[begin:end]
  if len(f)<2: continue
  change=f.close/f.open-1; up=(change.to_numpy()>.01);down=(change.to_numpy()<-.01)
  for i in np.flatnonzero(np.abs(np.abs(change.to_numpy())-.01)<1e-10):
   op=Decimal(str(f.open.iloc[i]));cl=Decimal(str(f.close.iloc[i]));up[i]=cl>op*Decimal('1.01');down[i]=cl<op*Decimal('.99')
  for name,(kind,args) in SPECS.items():
   try: s=signal(name,f).shift(1)
   except Exception as e:
    errors.append({'indicator':name,'timeframe':tf,'segment_start':str(f.index[0]),'error':f'{type(e).__name__}: {e}'});continue
   for month,mask in month_masks(f.index):
    if not mask.any():continue
    for side in (1,-1):
     m=score(s[mask],change[mask],side,actual_override=(up if side==1 else down)[mask])
     if m['valid_samples']:
      records.append({'symbol':symbol,'timeframe':tf,'indicator':name,'strategy':f'{name}__{kind}_default','hypothesis':kind,'parameters':'library defaults; talib=False; offset=0','direction':'LONG' if side==1 else 'SHORT','month':month,'segment_start':f.index[0].isoformat(),'metrics':m})
 return records,errors

def process(symbol,output,retry_partial=False):
 path=output/'symbols'/f'{symbol}.json.gz';path.parent.mkdir(parents=True,exist_ok=True)
 fingerprint=experiment_hash()
 if path.exists():
  try:
   with gzip.open(path,'rt') as f: prior=json.load(f)
   if prior.get('experiment_hash')==fingerprint and (prior.get('status')=='processed' or (prior.get('status')=='processed_with_errors' and not retry_partial)):
    print(f'{symbol}: checkpoint',flush=True);return
  except (OSError,ValueError):pass
 started=time.monotonic(); data=load_symbol(symbol);records=[];errors=[]
 for tf in DURATIONS:
  r,e=run_frame(symbol,tf,data[tf]);records+=r;errors+=e
 covered={(r['indicator'],r['timeframe']) for r in records}; expected={(name,tf) for name in SPECS for tf in DURATIONS}; missing_pairs=sorted(expected-covered)
 study_start=pd.Timestamp('2026-04-01',tz='UTC');study_end=pd.Timestamp('2026-10-28',tz='UTC')
 study_gaps=[gap for gap in data['quality'].get('missing_ranges',[]) if pd.Timestamp(gap[0])<study_end and pd.Timestamp(gap[1])>study_start]
 obj={'version':VERSION,'experiment_hash':fingerprint,'symbol':symbol,'status':'processed_with_errors' if errors or missing_pairs or study_gaps else 'processed','coverage':{'expected_indicator_timeframes':len(expected),'observed_indicator_timeframes':len(covered),'missing_indicator_timeframes':missing_pairs,'study_missing_ranges':study_gaps},'duration_seconds':time.monotonic()-started,'input_quality':data['quality'],'records':records,'errors':errors,'registered_hypotheses':SPECS,'warnings':['Unscreened studies are not implied to be inapplicable.','Originally exposed holdout is exploratory; not used for selection.','Per-symbol monthly trade statistics are not portfolio performance.','Near-threshold Decimal conversion cannot recover lost float precision.']}
 tmp=path.with_name(path.name+'.tmp')
 with gzip.open(tmp,'wt') as f: json.dump(obj,f,separators=(',',':'),allow_nan=False)
 os.replace(tmp,path)
 print(f'{symbol}: {len(records)} records, {len(errors)} indicator segment errors, {obj["duration_seconds"]:.1f}s',flush=True)

def main():
 p=argparse.ArgumentParser();group=p.add_mutually_exclusive_group(required=True);group.add_argument('--symbol',action='append');group.add_argument('--all',action='store_true');p.add_argument('--retry-partial',action='store_true',help='Reprocess partial checkpoints');p.add_argument('--universe',default=str(PROJECT/'output/universe.json'));p.add_argument('--output',type=Path,default=ROOT/'results/benchmark_v2');a=p.parse_args()
 syms=[x['symbol'] for x in json.loads(Path(a.universe).read_text())['symbols']] if a.all else a.symbol
 failed=[]
 for s in syms:
  try:process(s,a.output,retry_partial=a.retry_partial)
  except Exception as e:
   print(f'{s}: FAILED {type(e).__name__}: {e}',file=sys.stderr,flush=True);failed.append(s)
 if failed:raise SystemExit(1)
if __name__=='__main__':main()
