#!/usr/bin/env python3
"""Exploratory expanding-window selection from daily, per-symbol trade aggregates.

Input JSONL: symbol,timeframe,strategy,direction,date,trades,net_return_sum,
net_squared_sum, and optionally tp,fp,fn,tn. Each row is a mutually exclusive
UTC date/symbol/strategy/timeframe/direction. The old Sep-Oct holdout was seen;
this program intentionally does not use it for parameter search or imply it is
an untouched final test. Combinations must be computed before this program,
never inferred by adding results of separate strategies.
"""
from __future__ import annotations
import argparse, collections, datetime as dt, gzip, hashlib, json, math, os
from pathlib import Path

FOLDS=(('wf_july','2026-04-01','2026-07-01','2026-08-01'),
       ('wf_august','2026-04-01','2026-08-01','2026-09-01'))
KEYS=('trades','net_return_sum','net_squared_sum','tp','fp','fn','tn')

def sign_test(values):
    """One-sided exact sign test; independent equal-probability signs assumed."""
    v=[x for x in values if x!=0]; n=len(v)
    if not n:return None
    k=sum(x>0 for x in v)
    return sum(math.comb(n,i) for i in range(k,n+1))/(2**n)

def bh(pvalues):
    """Benjamini-Hochberg adjusted p-values, None preserved."""
    valid=sorted((p,i) for i,p in enumerate(pvalues) if p is not None)
    out=[None]*len(pvalues); n=len(valid); prev=1.0
    for rank in range(n,0,-1):
        p,i=valid[rank-1];prev=min(prev,p*n/rank);out[i]=min(1.,prev)
    return out

def metrics(records):
    totals={k:sum(r[k] for r in records) for k in KEYS}; n=totals['trades']; s=totals['net_return_sum']; sq=totals['net_squared_sum']
    mean=s/n if n else None; var=max(0.,(sq-s*s/n)/(n-1)) if n>1 else 0.; sd=math.sqrt(var)
    tp,fp,fn,tn=(totals[k] for k in ('tp','fp','fn','tn')); precision=tp/(tp+fp) if tp+fp else None; recall=tp/(tp+fn) if tp+fn else None
    totals.update({'symbols':len(records),'symbols_with_trades':sum(r['trades']>0 for r in records),'mean_net_return':mean,'sharpe_unannualized_per_trade':mean/sd if mean is not None and sd else None,'precision':precision,'recall':recall,'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,'false_positive_rate':fp/(fp+tn) if fp+tn else None})
    return totals

def analyze_rows(rows, input_sha256, top=3, minimum_trades=30, minimum_symbols=10):
    # Aggregate each independent symbol across the train/test segments of each
    # fold. Dates after Aug 31 are intentionally outside this experiment.
    table=collections.defaultdict(lambda:{k:0 for k in KEYS}); seen=set(); input_rows=0
    for row in rows:
            input_rows+=1
            for name in ('symbol','timeframe','strategy','direction','date','trades','net_return_sum','net_squared_sum'):
                if name not in row:raise ValueError(f'missing {name} on row {input_rows}')
            date=dt.date.fromisoformat(row['date']).isoformat()
            key=(row['symbol'],row['timeframe'],row['strategy'],row['direction'],date)
            if key in seen:raise ValueError(f'duplicate grain {key}')
            seen.add(key)
            if row['direction'] not in ('LONG','SHORT'):raise ValueError('invalid direction')
            if row['timeframe'] not in ('5m','15m','1h','4h','1d'):raise ValueError('invalid timeframe')
            nums={k:row.get(k,0) for k in KEYS}
            if any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in nums.values()):raise ValueError('invalid metric')
            if nums['trades']<0 or nums['trades']!=int(nums['trades']) or nums['net_squared_sum']<0:raise ValueError('invalid trade moments')
            for fold,begin,cut,end in FOLDS:
                phase='train' if begin<=date<cut else 'test' if cut<=date<end else None
                if phase:
                    bucket=table[(fold,phase,*key[:4])]
                    for k,v in nums.items():bucket[k]+=v
    # Group symbol aggregates for selection and evaluation.
    groups=collections.defaultdict(list)
    for (fold,phase,symbol,tf,strategy,side),values in table.items():
        if values['trades'] or sum(values[k] for k in ('tp','fp','fn','tn')):
            groups[(fold,phase,tf,strategy,side)].append(values)
    candidates=collections.defaultdict(list)
    for (fold,phase,tf,strategy,side),rows in groups.items():
        if phase!='train' or strategy.startswith('baseline_'):continue
        m=metrics(rows)
        if m['trades']>=minimum_trades and m['symbols_with_trades']>=minimum_symbols and m['mean_net_return'] is not None:
            candidates[(fold,tf,side)].append((m['mean_net_return'],strategy,m))
    output=[]
    for (fold,tf,side),items in sorted(candidates.items()):
        for _,strategy,train in sorted(items,key=lambda v:(-v[0],v[1]))[:top]:
            rows=groups.get((fold,'test',tf,strategy,side),[]);test=metrics(rows)
            # Symbol-level mean return signs provide an illustrative, not
            # independently distributed, cross-sectional stability test.
            means=[r['net_return_sum']/r['trades'] for r in rows if r['trades']]
            p=sign_test(means)
            output.append({'fold':fold,'timeframe':tf,'strategy':strategy,'direction':side,'train':train,'test':test,
                           'test_symbols_with_trades':len(means),'positive_test_symbols':sum(v>0 for v in means),
                           'positive_symbol_sign_test_p':p})
    adjusted=bh([r['positive_symbol_sign_test_p'] for r in output])
    for row,q in zip(output,adjusted):row['positive_symbol_sign_test_bh_q']=q
    return {'schema_version':'1.0','status':'exploratory','input_sha256':input_sha256,'input_rows':input_rows,
      'folds':[{'fold':n,'train_start':a,'train_end_exclusive':b,'test_start':b,'test_end_exclusive':c} for n,a,b,c in FOLDS],
      'selection':{'top_per_timeframe_direction':top,'minimum_train_trades':minimum_trades,'minimum_train_symbols':minimum_symbols,'rank':'training mean net return, no test data'},
      'limitations':['September–October holdout has been previously inspected and is not used here; no untouched final holdout is available.',
        'An expanding window creates overlapping training data; fold outcomes are not independent.',
        'Sign test assumes independent symbols; crypto symbols are correlated, so p-values and BH q-values are exploratory and can be anti-conservative.',
        'Per-trade Sharpe is unannualized and is not a shared-portfolio statistic. Drawdown and regime comparisons require detailed trade or daily-equity data.',
        'Combinations are evaluated only when separately computed as actual strategies in input; independent signal results cannot be added to form a combination.'],
      'results':output}

def analyze(path, top=3, minimum_trades=30, minimum_symbols=10):
    digest=hashlib.sha256()
    with open(path,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
    def rows():
        with open(path,'rt') as f:
            for line in f:
                if line.strip():yield json.loads(line)
    return analyze_rows(rows(),digest.hexdigest(),top,minimum_trades,minimum_symbols)

def analyze_v2(directory,top=3,minimum_trades=30,minimum_symbols=10,universe=None):
    """Read v2 monthly source files without materializing a giant CSV."""
    from benchmark_v2 import VERSION, experiment_hash
    files=sorted(Path(directory).glob('*.json.gz'))
    if not files:raise ValueError('no benchmark-v2 symbol files found')
    universe=Path(universe) if universe else Path(__file__).resolve().parent.parent/'output/universe.json'
    requested={v['symbol'] for v in json.loads(universe.read_text())['symbols']}
    completed=set(); flagged=[]
    digest=hashlib.sha256()
    def rows():
        for file in files:
            digest.update(file.name.encode())
            with open(file,'rb') as f:
                for b in iter(lambda:f.read(1024*1024),b''):digest.update(b)
            with gzip.open(file,'rt') as f: obj=json.load(f)
            symbol=obj.get('symbol')
            if symbol not in requested or file.name!=f'{symbol}.json.gz' or symbol in completed:
                raise ValueError(f'foreign or duplicate symbol in {file.name}')
            if obj.get('version')!=VERSION or obj.get('experiment_hash')!=experiment_hash():
                raise ValueError(f'stale or incompatible benchmark file {file.name}')
            if obj.get('status') not in ('processed','processed_with_errors'):
                raise ValueError(f'invalid processing status {file.name}')
            completed.add(symbol)
            if obj.get('errors') or obj.get('status')!='processed':flagged.append(symbol)
            for interval in obj.get('input_quality',{}).get('missing_ranges',[]):
                try:
                    lo,hi=interval if isinstance(interval,list) else (interval['start'],interval['end'])
                    a=dt.datetime.fromisoformat(lo.replace('Z','+00:00')); b=dt.datetime.fromisoformat(hi.replace('Z','+00:00'))
                    if a < dt.datetime(2026,10,28,tzinfo=dt.timezone.utc) and b > dt.datetime(2026,4,1,tzinfo=dt.timezone.utc):
                        flagged.append(symbol);break
                except (ValueError,TypeError,KeyError) as exc:raise ValueError(f'bad missing interval in {file.name}') from exc
            monthly=collections.defaultdict(lambda:{k:0 for k in KEYS}); segments=set()
            for r in obj['records']:
                if r.get('symbol')!=symbol:raise ValueError(f'foreign record in {file.name}')
                m=r['metrics']
                key=(r['symbol'],r['timeframe'],r['strategy'],r['direction'],r['month'])
                segment=(*key,r.get('segment_start'))
                if segment in segments:raise ValueError(f'duplicate segment {segment}')
                segments.add(segment)
                for k in KEYS:monthly[key][k]+=m.get(k,0)
            for key,m in monthly.items():
                symbol,timeframe,strategy,direction,month=key
                yield {'symbol':symbol,'timeframe':timeframe,'strategy':strategy,'direction':direction,'date':month+'-01',**m}
    result=analyze_rows(rows(),None,top,minimum_trades,minimum_symbols)
    result['input_sha256']=digest.hexdigest();result['input_files']=len(files)
    result['input_grain']='symbol/timeframe/strategy/direction/month'
    result['requested_symbols']=len(requested);result['completed_symbols']=len(completed)
    result['missing_symbols']=sorted(requested-completed);result['symbols_with_errors_or_study_gaps']=sorted(set(flagged))
    if result['missing_symbols'] or flagged:result['status']='exploratory_partial'
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__); source=p.add_mutually_exclusive_group(required=True);source.add_argument('--daily',type=Path);source.add_argument('--v2-dir',type=Path);p.add_argument('--output',type=Path,default=Path(__file__).parent/'results/walkforward.json');p.add_argument('--top',type=int,default=3);p.add_argument('--minimum-trades',type=int,default=30);p.add_argument('--minimum-symbols',type=int,default=10);args=p.parse_args()
    if args.top<1 or args.minimum_trades<1 or args.minimum_symbols<1: p.error('thresholds must be positive')
    result=(analyze(args.daily,args.top,args.minimum_trades,args.minimum_symbols) if args.daily else analyze_v2(args.v2_dir,args.top,args.minimum_trades,args.minimum_symbols));args.output.parent.mkdir(parents=True,exist_ok=True)
    tmp=args.output.with_suffix('.tmp');tmp.write_text(json.dumps(result,indent=2,allow_nan=False)+'\n');os.replace(tmp,args.output)
    print(f"{result['input_rows']} daily rows; {len(result['results'])} exploratory fold results")
if __name__=='__main__':main()
