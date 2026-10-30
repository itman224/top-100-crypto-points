"""Bounded single-symbol Binance archive loader for quantitative research.

Frames use UTC candle OPEN timestamps. Every returned aggregated candle has all
required valid and unique 5m constituents. Raw ZIPs are held only one at a
time in memory and discarded, and no raw data is persisted.
"""
from __future__ import annotations
import calendar, csv, hashlib, io, json, math, os, time, urllib.error, urllib.request, zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

BASE='https://data.binance.vision/data/futures/um'
FRAMES={'5m': 1, '15m': 3, '1h': 12, '4h': 48, '1d': 288}
UTC=timezone.utc

def _download(url, retries=3):
    for n in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'quant-research-lab/1.0'}),timeout=45) as r:
                return r.read(), 'available', None
        except urllib.error.HTTPError as e:
            if e.code==404: return None,'missing',str(e)
            err=str(e)
        except (OSError,TimeoutError) as e:
            err=str(e)
        if n+1<retries: time.sleep(0.5*2**n)
    return None,'unavailable',err

def _archive(symbol, kind, label):
    return f'{BASE}/{kind}/klines/{symbol}/5m/{symbol}-5m-{label}.zip'

def _read_zip(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names=[n for n in z.namelist() if n.endswith('.csv')]
        if len(names)!=1: raise ValueError('archive must contain exactly one CSV')
        with z.open(names[0]) as f:
            yield from csv.reader(io.TextIOWrapper(f,encoding='utf-8-sig',newline=''))

def _iter_sources(symbol, start, end, fetch=_download):
    month=start.replace(day=1)
    while month<=end:
        last=month.replace(day=calendar.monthrange(month.year,month.month)[1])
        lo=max(month,start); hi=min(last,end)
        url=_archive(symbol,'monthly',month.strftime('%Y-%m'))
        blob,status,error=fetch(url)
        info={'url':url,'source_id':hashlib.sha256(url.encode()).hexdigest(),'status':status,'bytes':len(blob) if blob else 0,'error':error}
        yield info,blob
        if blob is None:
            day=lo
            while day<=hi:
                url=_archive(symbol,'daily',day.isoformat())
                blob,status,error=fetch(url)
                info={'url':url,'source_id':hashlib.sha256(url.encode()).hexdigest(),'status':status,'bytes':len(blob) if blob else 0,'error':error}
                yield info,blob
                # One transient service outage is not evidence of missing data.
                if status=='unavailable': break
                day+=timedelta(days=1)
        month=last+timedelta(days=1)

def _parse_row(row):
    raw=int(row[0]); scale=1000 if raw>=10**14 else 1
    if raw%(300000*scale) or int(row[6]) != raw + 300000*scale-1: raise ValueError('timestamp')
    t=raw//scale
    o,h,l,c,v=map(float,row[1:6]); q=float(row[7])
    if not all(math.isfinite(x) for x in (o,h,l,c,v,q)) or min(o,h,l,c)<=0 or v<0 or q<0 or h<max(o,c,l) or l>min(o,c,h): raise ValueError('ohlcv')
    return t,(o,h,l,c,v,q)

def aggregate(rows, start_ms, end_ms):
    """Aggregate keyed milliseconds; omit any bucket missing any constituent."""
    fields=['open','high','low','close','volume','quote_volume']
    if rows:
        index=pd.to_datetime(sorted(rows),unit='ms',utc=True)
        frame=pd.DataFrame([rows[int(t.value//10**6)] for t in index],columns=fields,index=index,dtype='float64')
    else:
        frame=pd.DataFrame(columns=fields,index=pd.DatetimeIndex([],tz='UTC'),dtype='float64')
    out={'5m':frame}
    for name,n in FRAMES.items():
        if n==1: continue
        grouped=frame.resample(f'{n*5}min',origin='epoch',label='left',closed='left')
        candles=grouped.agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum','quote_volume':'sum'})
        counts=grouped['open'].count()
        out[name]=candles.loc[counts.eq(n)]
    return out

def load_symbol(symbol, start='2026-04-01', end='2026-10-27', warmup_days=250, fetch=_download, quality_dir=None):
    """Return frames and quality for one symbol; start/end are inclusive UTC days.

    Checkpoint is compact provenance only, not market data. Repeating a symbol
    needs remote archives again; completed analysis should be resumed at the
    research-result layer. Missing/duplicate 5m bars invalidate larger bars.
    """
    begin=date.fromisoformat(str(start))-timedelta(days=warmup_days); finish=date.fromisoformat(str(end))
    start_ms=int(datetime.combine(begin,datetime.min.time(),UTC).timestamp()*1000)
    end_ms=int(datetime.combine(finish+timedelta(days=1),datetime.min.time(),UTC).timestamp()*1000)
    rows={}; conflicts=set(); invalid=0; duplicate=0; sources=[]
    for source,blob in _iter_sources(symbol,begin,finish,fetch):
        sources.append(source)
        if blob is None: continue
        try:
            for row in _read_zip(blob):
                if not row or row[0].lower() in ('open_time','opentime'): continue
                try:
                    t, values=_parse_row(row)
                    if not start_ms<=t<end_ms: continue
                    if t in rows or t in conflicts:
                        duplicate+=1; rows.pop(t,None); conflicts.add(t)
                    else: rows[t]=values
                except (ValueError,IndexError,OverflowError): invalid+=1
        except (OSError,zipfile.BadZipFile,ValueError) as e:
            source['status']='invalid'; source['error']=str(e)
    expected=(end_ms-start_ms)//300000
    missing_ranges=[]; gap=None
    for t in range(start_ms,end_ms,300000):
        if t not in rows and gap is None: gap=t
        if t in rows and gap is not None:
            missing_ranges.append([pd.Timestamp(gap,unit='ms',tz='UTC').isoformat(),pd.Timestamp(t,unit='ms',tz='UTC').isoformat()]);gap=None
    if gap is not None: missing_ranges.append([pd.Timestamp(gap,unit='ms',tz='UTC').isoformat(),pd.Timestamp(end_ms,unit='ms',tz='UTC').isoformat()])
    frames=aggregate(rows,start_ms,end_ms)
    quality={'symbol':symbol,'start_utc':begin.isoformat(),'end_inclusive_utc':finish.isoformat(),'expected_5m':expected,'valid_5m':len(rows),'missing_5m':expected-len(rows),'invalid_rows':invalid,'duplicate_rows':duplicate,'missing_ranges':missing_ranges,'sources':sources,'downloaded_bytes':sum(s['bytes'] for s in sources),'frame_counts':{k:len(v) for k,v in frames.items()}}
    if quality_dir:
        p=Path(quality_dir);p.mkdir(parents=True,exist_ok=True); target=p/f'{symbol}.json'; tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(quality,indent=2));os.replace(tmp,target)
    return {**frames,'quality':quality}
