"""Build merged, archive-backed 250-candle pre-event windows without downloading archives."""
import argparse, calendar, gzip, hashlib, json, sys, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE='https://data.binance.vision/'
PREFIX='data/futures/um/'
DURATIONS={'5m':timedelta(minutes=5),'15m':timedelta(minutes=15),'1h':timedelta(hours=1),'4h':timedelta(hours=4),'1d':timedelta(days=1)}

def parse_timestamp(s):
    return datetime.fromisoformat(str(s).replace('Z','+00:00')).astimezone(timezone.utc)

def iso(t): return t.isoformat().replace('+00:00','Z')

def window_start(open_time,timeframe,preceding=250): return open_time-DURATIONS[timeframe]*preceding

def source_url(symbol,typ,stamp):
    return BASE+PREFIX+f'{typ}/klines/{symbol}/5m/{symbol}-5m-{stamp}.zip'

def sid(url): return hashlib.sha256(url.encode()).hexdigest()

def iter_events(events_dir):
    for p in sorted(Path(events_dir).rglob('*.jsonl.gz')):
        with gzip.open(p,'rt',encoding='utf-8') as f:
            for line in f:
                if line.strip(): yield json.loads(line)

def checkpoints(root):
    known={}
    for p in sorted(Path(root).glob('*/*.json')):
        with p.open() as f: cp=json.load(f)
        for s in cp.get('sources',[]):
            if s.get('url'):
                # A downloaded archive is evidence of accessibility. Missing is
                # only recorded by the ingestion fetcher after HTTP 404.
                known[s['url']]={'status': {'available':'verified','missing':'missing'}.get(s.get('status'),'inferred'), 'symbol':cp.get('symbol') or p.parent.name, 'source_type':s.get('source_type', 'binance_archive'), 'dataset_revision':s.get('dataset_revision')}
    return known

class Resolver:
    def __init__(self,known,probe=None):
        self.known={url: value['status'] for url,value in known.items()}; self.probe=probe or self.head; self.offline=False
    @staticmethod
    def head(url):
        try:
            req=urllib.request.Request(url,method='HEAD',headers={'User-Agent':'binance-study-manifest/1'})
            with urllib.request.urlopen(req,timeout=8) as r: return 'verified' if r.status==200 else 'inferred'
        except urllib.error.HTTPError as e:
            return 'missing' if e.code==404 else 'inferred'
        except (OSError,TimeoutError): return 'inferred'
    def status(self,url):
        if url not in self.known:
            # A transient failure is not evidence the archive is absent. Avoid
            # hundreds of repeated requests while the network is unavailable.
            status='inferred' if self.offline else self.probe(url)
            if status=='inferred': self.offline=True
            self.known[url]=status
        return self.known[url]

def months(start,end):
    d=start.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    while d<end:
        yield d
        d=(d.replace(day=28)+timedelta(days=4)).replace(day=1)

def build_manifest(events_dir,archive_root=None,preceding=250,checkpoint_root=None,probe=None):
    events_dir=Path(events_dir)
    if preceding<0: raise ValueError('preceding must be non-negative')
    cp=Path(checkpoint_root) if checkpoint_root else events_dir.parent/'checkpoints'
    known=checkpoints(cp); resolver=Resolver(known,probe); sources={}; events=[]; intervals={}; event_sources={}
    universe_path=events_dir.parent/'universe.json'
    selected=None
    if universe_path.exists():
        u=json.loads(universe_path.read_text()); selected={x['symbol'] if isinstance(x,dict) else x for x in u['symbols']}
    def add(url,status=None):
        status=status or resolver.status(url)
        rel=url[len(BASE):] if url.startswith(BASE) else url
        path=rel[len(PREFIX):] if rel.startswith(PREFIX) else rel
        parts=path.split('/')
        typ=parts[0] if parts else 'unknown'
        meta=known.get(url,{})
        symbol=meta.get('symbol') or (parts[2] if len(parts)>2 else None)
        if meta.get('source_type') == 'hf_parquet': typ='huggingface_parquet'
        obj={'source_id':sid(url),'symbol':symbol,'interval':'5m','archive_type':typ,'url':url,'relative_path':rel,'path':path,'status':status,'verified':status=='verified','inferred':status=='inferred','missing':status=='missing'}
        if meta.get('source_type') == 'hf_parquet':
            obj.update({'source_type':'hf_parquet','dataset_revision':meta.get('dataset_revision'),'official_verification':'pending','coverage_note':'File availability is verified; coverage of a lookback interval is not implied.'})
        sources[url]=obj
        return obj['source_id']
    # Include all real event source URLs from checkpoint metadata, even when a
    # preferred monthly lookback archive happens to cover the same timestamp.
    id_to_url={sid(u):u for u in known}
    for raw in iter_events(events_dir):
        symbol=raw['symbol']
        if selected is not None and symbol not in selected: continue
        tf=raw['timeframe']; opened=parse_timestamp(raw['open_time']); closed=parse_timestamp(raw['close_time'])
        if tf not in DURATIONS or closed-opened!=DURATIONS[tf]: raise ValueError('invalid event timeframe or interval')
        start=window_start(opened,tf,preceding); event_id=raw['event_id']
        upstream=raw.get('source_ids',[])
        for s in upstream:
            if s not in id_to_url: raise ValueError(f'event {event_id} has unknown source {s}')
            add(id_to_url[s],known[id_to_url[s]]['status'])
        item={'event_id':event_id,'symbol':symbol,'market':raw.get('market'),'timeframe':tf,'open_time':iso(opened),'close_time':iso(closed),'window_start':iso(start),'window_end':iso(closed),'source_ids':upstream}
        events.append(item); event_sources[event_id]=upstream; intervals.setdefault(symbol,[]).append((start,closed,event_id))
    windows=[]
    for symbol in sorted(intervals):
        merged=[]
        for start,end,event_id in sorted(intervals[symbol]):
            if merged and start<=merged[-1][1]:
                merged[-1][1]=max(merged[-1][1],end); merged[-1][2].append(event_id)
            else: merged.append([start,end,[event_id]])
        for start,end,ids in merged:
            source_ids=[]
            for month in months(start,end):
                murl=source_url(symbol,'monthly',month.strftime('%Y-%m')); state=resolver.status(murl)
                if state!='missing': source_ids.append(add(murl,state)); continue
                m_end=(month.replace(day=28)+timedelta(days=4)).replace(day=1)
                day=max(start,month).replace(hour=0,minute=0,second=0,microsecond=0)
                while day<min(end,m_end):
                    u=source_url(symbol,'daily',day.strftime('%Y-%m-%d')); source_ids.append(add(u)); day+=timedelta(days=1)
            # Include actual event input archives as well as preferred lookback archives.
            for event_id in ids: source_ids.extend(event_sources[event_id])
            windows.append({'symbol':symbol,'start_utc':iso(start),'end_utc':iso(end),'source_ids':list(dict.fromkeys(source_ids)),'event_ids':ids})
    return {'schema_version':'1.0','version':1,'archive_base_url':BASE,'window_preceding_candles':preceding,'archive_interval':'5m','sources':sorted(sources.values(),key=lambda s:s['url']),'analysis_windows':windows,'events':events}

def write_manifest(manifest,output_path):
    p=Path(output_path); p.parent.mkdir(parents=True,exist_ok=True); temp=p.with_name(p.name+'.tmp')
    with temp.open('wb') as f:
        with gzip.GzipFile(fileobj=f,mode='wb',mtime=0) as g: g.write(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode())
    temp.replace(p)

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--events-dir',type=Path,default=Path('output/events')); p.add_argument('--checkpoint-root',type=Path); p.add_argument('--output',type=Path,default=Path('output/source_manifest.json.gz')); p.add_argument('--preceding',type=int,default=250); args=p.parse_args()
    try: write_manifest(build_manifest(args.events_dir,preceding=args.preceding,checkpoint_root=args.checkpoint_root),args.output)
    except (OSError,ValueError,KeyError) as e: print(f'manifest: {e}',file=sys.stderr); return 1
    return 0
if __name__=='__main__': raise SystemExit(main())
