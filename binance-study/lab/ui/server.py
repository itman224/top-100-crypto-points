#!/usr/bin/env python3
"""Local, read-only research dashboard API. No raw archives are exposed."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from pathlib import Path
import gzip,json,re,argparse,hashlib
ROOT=Path(__file__).resolve().parents[2]
LAB=ROOT/'lab'
OUT=ROOT/'output'
UI=Path(__file__).resolve().parent

def read(path,default=None):
 try:
  with path.open(encoding='utf8') as f:return json.load(f)
 except (OSError,ValueError):return default

def research_v2():
 """Only expose persisted research artefacts; absence is pending, never zero evidence."""
 root=LAB/'results'/'benchmark_v2'
 coverage=read(LAB/'coverage_report.json')
 ledger=read(root/'execution_ledger.json')
 rankings=read(root/'rankings.json')
 walkforward=read(LAB/'results'/'walkforward.json')
 return {'coverage':coverage,'ledger':ledger,'rankings':rankings,'walkforward':walkforward,
         'paths':{'coverage':'lab/coverage_report.json','ledger':'lab/results/benchmark_v2/execution_ledger.json',
                  'rankings':'lab/results/benchmark_v2/rankings.json','walkforward':'lab/results/walkforward.json'}}

def benchmark_v2_symbol(q):
 symbol=q.get('symbol',[''])[0].upper()
 if not re.fullmatch(r'[A-Z0-9]{2,25}',symbol):return {'status':'invalid','error':'Invalid symbol','items':[]}
 tf=q.get('timeframe',[''])[0]; side=q.get('direction',[''])[0].upper(); month=q.get('month',[''])[0]
 try: offset=max(0,int(q.get('offset',['0'])[0])); limit=max(1,min(100,int(q.get('limit',['50'])[0])))
 except ValueError: return {'status':'invalid','error':'Invalid pagination','items':[]}
 p=LAB/'results'/'benchmark_v2'/'symbols'/f'{symbol}.json.gz'
 if not p.is_file():return {'status':'pending','symbol':symbol,'items':[]}
 try:
  with gzip.open(p,'rt',encoding='utf8') as f: obj=json.load(f)
  if not isinstance(obj,dict) or obj.get('symbol')!=symbol or not isinstance(obj.get('records'),list): raise ValueError('Invalid symbol checkpoint')
  rows=[x for x in obj['records'] if isinstance(x,dict) and (not tf or x.get('timeframe')==tf) and (not side or x.get('direction')==side) and (not month or x.get('month')==month)]
  return {'status':obj.get('status','unknown'),'symbol':symbol,'items':rows[offset:offset+limit],
          'total':len(rows),'offset':offset,'limit':limit,'experiment_hash':obj.get('experiment_hash'),
          'duration_seconds':obj.get('duration_seconds'),'errors':obj.get('errors',[]),'warnings':obj.get('warnings',[]),
          'input_quality':obj.get('input_quality')}
 except (OSError,ValueError,EOFError):return {'status':'invalid','symbol':symbol,'error':'Checkpoint could not be verified','items':[]}

def attach_features(symbol, items):
 """Join one bounded page of events to actual per-symbol feature records."""
 ids={e.get('event_id') for e in items if e.get('event_id')}
 matches={}
 path=LAB/'results'/'event_features'/f'{symbol}.jsonl.gz'
 if ids and path.is_file():
  try:
   with gzip.open(path,'rt',encoding='utf8') as f:
    for line in f:
     row=json.loads(line); key=row.get('event_id')
     if key in ids and row.get('symbol')==symbol:
      matches[key]=row
      if len(matches)==len(ids):break
  except (OSError,ValueError):
   pass
 for event in items:
  row=matches.get(event.get('event_id'))
  if row and row.get('event_open_time')==event.get('open_time') and row.get('decision_time','')<=event.get('open_time',''):
   event['prior_features']=row
  else:
   event['prior_features']=None
 return items

def events(q):
 symbol=q.get('symbol',['BTCUSDT'])[0].upper()
 if not re.fullmatch(r'[A-Z0-9]{2,25}',symbol):return {'items':[],'error':'Invalid symbol'}
 tf=q.get('timeframe',[''])[0]; direction=q.get('direction',[''])[0].upper()
 start=q.get('start',[''])[0]; end=q.get('end',[''])[0]
 try: limit=max(1,min(int(q.get('limit',['100'])[0]),200))
 except ValueError: limit=100
 items=[]; scanned=0
 for path in sorted((OUT/'events'/symbol).glob('*.jsonl.gz')):
  try:
   with gzip.open(path,'rt',encoding='utf8') as f:
    for line in f:
     e=json.loads(line); scanned+=1
     if tf and e.get('timeframe')!=tf:continue
     if direction and str(e.get('direction','')).upper()!=direction:continue
     date=e.get('open_time','')[:10]
     if start and date<start:continue
     if end and date>end:continue
     items.append(e)
     if len(items)>=limit:return {'items':attach_features(symbol,items),'limit':limit,'truncated':True,'scanned':scanned}
  except (OSError,ValueError):continue
 return {'items':attach_features(symbol,items),'limit':limit,'truncated':False,'scanned':scanned}

class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  u=urlparse(self.path)
  if u.path=='/':return self.sendfile(UI/'index.html','text/html; charset=utf-8')
  if u.path=='/style.css':return self.sendfile(UI/'style.css','text/css; charset=utf-8')
  if u.path=='/app.js':return self.sendfile(UI/'app.js','text/javascript; charset=utf-8')
  if u.path=='/api/research-v2':return self.json(research_v2())
  if u.path=='/api/benchmark-v2':return self.json(benchmark_v2_symbol(parse_qs(u.query)))
  if u.path=='/api/overview':
   summary=read(OUT/'summary.json',{}); validation=read(OUT/'validation.json',{}); universe=read(OUT/'universe.json',{})
   return self.json({'summary':summary,'validation':validation,'universe':universe})
  if u.path=='/api/events':return self.json(events(parse_qs(u.query)))
  if u.path=='/api/results':
   candidates=[LAB/'output'/'results.json',LAB/'results'/'results.json',LAB/'results.json',LAB/'output'/'benchmark.json',LAB/'results'/'benchmark.json',LAB/'results'/'insights.json']
   found=[]
   for p in candidates:
    if p.exists():
     obj=read(p)
     if isinstance(obj,dict) and (obj.get('methodology',{}).get('version') == 'one-candle-v3' or (p.name == 'insights.json' and isinstance(obj.get('questions'),list))):found.append({'path':str(p.relative_to(ROOT)),'data':obj})
   if not found:
    records=[]; processed=[]
    for p in sorted((LAB/'results'/'symbols').glob('*.json')):
     obj=read(p,{})
     if isinstance(obj.get('records'),list) and obj.get('version') == 'one-candle-v3':
      records.extend(obj['records']);processed.append(obj.get('symbol',p.stem))
    if records:found.append({'path':'lab/results/symbols/*.json','data':{'benchmarks':records,'processed_symbols':processed,'scope':'per-symbol; no aggregate ranking'}})
   return self.json({'files':found,'status':'available' if found else 'pending'})
  if u.path=='/api/risk':
   p=LAB/'results'/'risk_summary.json'; data=read(p)
   if not isinstance(data,dict):return self.json({'status':'pending'})
   selection=read(LAB/'results'/'results.json',{});picks=[(x.get('strategy'),x.get('timeframe'),x.get('direction')) for x in selection.get('strategies',[]) if x.get('validation_pass')]
   selection_hash=hashlib.sha256(json.dumps(picks,sort_keys=True).encode()).hexdigest() if selection else None
   return self.json({'status':data.get('status','unknown'),'matches_current_selection':selection_hash is not None and selection_hash==data.get('selection_hash'),'path':'lab/results/risk_summary.json','updated_utc':__import__('datetime').datetime.fromtimestamp(p.stat().st_mtime,__import__('datetime').timezone.utc).isoformat(),'data':data})
  if u.path=='/api/catalog':
   candidates=[LAB/'output'/'indicator_catalog.json',LAB/'indicator_catalog.json',LAB/'results'/'indicator_catalog.json']
   for p in candidates:
    if p.exists():return self.json({'path':str(p.relative_to(ROOT)),'data':read(p)})
   return self.json({'status':'pending'})
  self.send_error(404)
 def json(self,obj):
  data=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode();self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
 def sendfile(self,p,typ):
  try:data=p.read_bytes()
  except OSError:return self.send_error(404)
  self.send_response(200);self.send_header('Content-Type',typ);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

def main():
 p=argparse.ArgumentParser();p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=8765);a=p.parse_args();print(f'Quant Lab: http://{a.host}:{a.port}',flush=True);ThreadingHTTPServer((a.host,a.port),Handler).serve_forever()
if __name__=='__main__':main()
