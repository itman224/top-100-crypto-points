#!/usr/bin/env python3
"""Execute one-symbol benchmark batches serially with a durable ledger.

No scheduler is installed by this script. External timers may invoke it; the
inherited advisory lock prevents overlapping workers even if the parent dies.
"""
from __future__ import annotations
import argparse, fcntl, gzip, hashlib, json, os, resource, shutil, signal, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
OUT=ROOT/'results'/'benchmark_v2'
LEDGER=OUT/'execution_ledger.json'
LOCK=OUT/'runner.lock'
VENV=ROOT/'.venv'/'bin'/'python'
INPUTS=('benchmark_v2.py','data.py','backtest.py','indicators.py','coverage_report.json')
MEMORY_LIMIT=5*1024**3
stop=False
child=None

def now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def atomic_json(path, data):
    tmp=path.with_name(path.name+'.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(data,f,indent=2,sort_keys=True,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
def fingerprint():
    h=hashlib.sha256()
    for name in INPUTS:
        p=ROOT/name
        if p.exists(): h.update(name.encode());h.update(p.read_bytes())
    h.update((PROJECT/'output/universe.json').read_bytes())
    return h.hexdigest()
def engine_hash():
    h=hashlib.sha256()
    for name in ('benchmark_v2.py','data.py','backtest.py'): h.update((ROOT/name).read_bytes())
    return h.hexdigest()
def other_workers():
    found=[]
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name)==os.getpid(): continue
        try:
            args=(proc/'cmdline').read_bytes().split(b'\0')
            if any(Path(os.fsdecode(a)).name=='benchmark_v2.py' for a in args if a): found.append(int(proc.name))
        except OSError: pass
    return found
def rss(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'): return int(line.split()[1])*1024
    except (OSError,ValueError,IndexError): pass
    return 0
def temp_bytes():
    size=0
    for p in (OUT/'tmp').rglob('*'):
        try:
            if p.is_file(): size+=p.stat().st_size
        except OSError: pass
    return size
def refresh(d):
    items=list(d['symbols'].values());counts={k:sum(x['status']==k for x in items) for k in ('completed','partial','running','pending','failed')};counts['total']=len(items);d['counts']=counts
    elapsed=[x['duration_seconds'] for x in items if x['status']=='completed' and x.get('duration_seconds') is not None]
    d['eta_seconds']=round(sum(elapsed)/len(elapsed)*(counts['pending']+counts['failed']),1) if len(elapsed)>=3 else None
    d['eta_note']='Estimated from completed batches for pending/failed symbols; excludes partial batches requiring repairs, future stages and additional network downtime.'
    d['updated_utc']=now();d['resource_observation']['disk_free_bytes']=shutil.disk_usage(PROJECT).free
    atomic_json(LEDGER,d)
def inspect_checkpoint(symbol):
    checkpoint=OUT/'symbols'/f'{symbol}.json.gz'
    try:
        with gzip.open(checkpoint,'rt',encoding='utf-8') as f: obj=json.load(f)
        if obj.get('symbol')!=symbol: raise ValueError('checkpoint symbol mismatch')
        if obj.get('status') not in ('processed','processed_with_errors') or obj.get('experiment_hash')!=engine_hash(): raise ValueError('checkpoint is stale or not processed')
        specs=obj.get('registered_hypotheses')
        if not isinstance(specs,dict) or not specs: raise ValueError('missing registered hypotheses')
        records=obj.get('records');errors=obj.get('errors')
        if not isinstance(records,list) or not isinstance(errors,list): raise ValueError('invalid records or errors')
        expected={(name,tf,side) for name in specs for tf in ('5m','15m','1h','4h','1d') for side in ('LONG','SHORT')}
        observed={(r.get('indicator'),r.get('timeframe'),r.get('direction')) for r in records if r.get('metrics',{}).get('valid_samples',0)>0}
        missing=sorted(expected-observed)
        study_start=datetime.fromisoformat('2026-04-01T00:00:00+00:00');study_end=datetime.fromisoformat('2026-10-28T00:00:00+00:00')
        quality=obj.get('input_quality');
        if not isinstance(quality,dict) or not isinstance(quality.get('missing_ranges'),list): raise ValueError('missing input quality and coverage ranges')
        ranges=quality['missing_ranges']; study_gaps=[]
        for a,b in ranges:
            begin=datetime.fromisoformat(a.replace('Z','+00:00'));end=datetime.fromisoformat(b.replace('Z','+00:00'))
            if begin<study_end and end>study_start: study_gaps.append([a,b])
        incomplete=bool(errors or missing or study_gaps or obj['status']!='processed')
        return {'status':'partial' if incomplete else 'completed', 'last_error': ('Indicator errors, missing hypothesis results or study-period gaps; review before claiming coverage.' if incomplete else None),
                'checkpoint':str(checkpoint.relative_to(PROJECT)), 'registered_hypotheses':len(specs),'expected_indicator_timeframe_directions':len(expected),'observed_indicator_timeframe_directions':len(observed & expected),
                'missing_indicator_timeframe_directions':len(missing),'missing_examples':missing[:20], 'indicator_segment_error_count':len(errors),'study_missing_ranges':study_gaps}
    except (OSError,ValueError,AttributeError,TypeError,KeyError) as exc:
        return {'status':'pending','last_error':f'invalid checkpoint: {exc}'}

def handle(sig,frame):
    global stop
    stop=True
    if child and child.poll() is None:
        try: os.killpg(child.pid,signal.SIGTERM)
        except ProcessLookupError: pass

def main():
    global child
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--max-batches',type=int,default=0,help='limit attempted symbols (0: all)');p.add_argument('--dry-run',action='store_true'); args=p.parse_args()
    if args.max_batches<0:p.error('--max-batches must be nonnegative')
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'tmp').mkdir(exist_ok=True);(OUT/'logs').mkdir(exist_ok=True)
    with LOCK.open('w') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: print('Another benchmark worker holds the lock.',file=sys.stderr);return 2
        symbols=[x['symbol'] for x in json.loads((PROJECT/'output/universe.json').read_text())['symbols']]
        if len(symbols)!=len(set(symbols)):raise ValueError('Duplicate universe symbols')
        fp=fingerprint()
        try: d=json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
        except (OSError,ValueError):raise SystemExit('Ledger is invalid; preserve and inspect it before retrying.')
        old=d.get('symbols',{}) if d.get('fingerprint')==fp else {}
        items={}
        for symbol in symbols:
            item=old.get(symbol,{}).copy(); item.setdefault('attempts',0)
            if item.get('status') in ('completed','partial'): item.update(inspect_checkpoint(symbol))
            elif item.get('status')=='running':item['status']='pending';item['last_error']='Prior run interrupted; pending retry.'
            elif item.get('status') not in ('completed','partial','failed','pending'):item['status']='pending'
            items[symbol]=item
        d={'schema_version':'1.0','fingerprint':fp,'created_utc':d.get('created_utc',now()) if old else now(),
           'updated_utc':now(),'elapsed_seconds':d.get('elapsed_seconds',0) if old else 0,'symbols':items,
           'resource_observation':d.get('resource_observation',{}) if old else {},
           'scheduler_status':{'persistent_scheduler_active':False,'note':'This runner does not install a scheduler. Configure an external timer; verify its real state independently.'},
           'resume_command':'cd '+str(PROJECT)+' && lab/.venv/bin/python lab/batch_runner_v2.py',
           'checkpoint_directory':str(OUT/'symbols'),'worker_limit':1,
           'scope_note':'Batches cover registered v2 hypotheses only, not all catalogued library capabilities; partial batches require review and are retried only after input/code changes.'}
        for key,val in {'peak_child_rss_bytes':0,'peak_temporary_disk_bytes':0,'memory_limit_bytes':MEMORY_LIMIT}.items(): d['resource_observation'].setdefault(key,val)
        refresh(d)
        if args.dry_run:print(json.dumps(d['counts']));return 0
        if other_workers():raise SystemExit(f'Another benchmark_v2.py worker is active: {other_workers()}')
        if not (ROOT/'benchmark_v2.py').is_file():raise SystemExit('benchmark_v2.py is not ready; no batch was started.')
        if not VENV.exists():raise SystemExit('Missing lab/.venv/bin/python')
        signal.signal(signal.SIGINT,handle);signal.signal(signal.SIGTERM,handle)
        started=time.monotonic();attempted=0
        for symbol,item in items.items():
            if stop or (args.max_batches and attempted>=args.max_batches): break
            if item['status'] in ('completed','partial'):continue
            attempted+=1;item.update(status='running',attempts=item['attempts']+1,started_utc=now(),last_error=None);refresh(d)
            t0=time.monotonic(); log=OUT/'logs'/f'{symbol}.log';env=os.environ.copy();env['TMPDIR']=str(OUT/'tmp');env['PYTHONPATH']=str(ROOT)
            with log.open('ab') as f:
                child=subprocess.Popen([str(VENV),'-u',str(ROOT/'benchmark_v2.py'),'--symbol',symbol],cwd=PROJECT,env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=True,pass_fds=(lock.fileno(),))
                exceeded=False
                while child.poll() is None:
                    used=rss(child.pid);d['resource_observation']['peak_child_rss_bytes']=max(used,d['resource_observation']['peak_child_rss_bytes']);d['resource_observation']['peak_temporary_disk_bytes']=max(temp_bytes(),d['resource_observation']['peak_temporary_disk_bytes'])
                    if used>MEMORY_LIMIT:
                        exceeded=True;os.killpg(child.pid,signal.SIGTERM)
                    try:child.wait(timeout=2)
                    except subprocess.TimeoutExpired:pass
                code=child.returncode; child=None
            item['duration_seconds']=round(time.monotonic()-t0,3);item['finished_utc']=now()
            item['status']='failed';item['last_error']=f'worker returned {code}; inspect {log}'
            checkpoint=OUT/'symbols'/f'{symbol}.json.gz'
            if code==0 and checkpoint.exists() and not exceeded and not stop:
                result=inspect_checkpoint(symbol)
                item.update(result)
                if item['status']=='pending': item['status']='failed'
            elif exceeded:item['last_error']='Stopped worker after observed RSS exceeded 5 GiB.'
            elif stop:item['status']='pending';item['last_error']='Interrupted; checkpoint retained for retry.'
            d['elapsed_seconds']=round(d['elapsed_seconds']+item['duration_seconds'],3);refresh(d)
            print(f'{symbol}: {item["status"]} ({item["duration_seconds"]}s)',flush=True)
        d['last_invocation_wall_seconds']=round(time.monotonic()-started,3);refresh(d)
        return 130 if stop else (1 if d['counts']['failed'] else 0)
if __name__=='__main__':raise SystemExit(main())
