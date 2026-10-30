#!/usr/bin/env python3
"""Independently revalidate stored pilot checkpoints' source 5m close timestamps."""
import csv, datetime as dt, io, json, os, pathlib, urllib.request, zipfile
ROOT=pathlib.Path('output'); DEST=ROOT/'pilot_close_time_validation.json'
result=json.loads(DEST.read_text()) if DEST.exists() else {}
for cp in sorted((ROOT/'checkpoints').glob('*/*.json')):
    data=json.loads(cp.read_text()); symbol=data['symbol']; month=data['month']; key=symbol+'/'+month
    if symbol not in ('BTCUSDT','ETHUSDT','SOLUSDT') or result.get(key,{}).get('status')=='verified': continue
    start=int(dt.datetime.fromisoformat(data['start']).replace(tzinfo=dt.timezone.utc).timestamp()*1000)
    end=int((dt.datetime.fromisoformat(data['end']).replace(tzinfo=dt.timezone.utc)+dt.timedelta(days=1)).timestamp()*1000)
    seen=set(); invalid=[]; errors=[]; files=0
    for source in data['sources']:
        if source.get('status')!='available': continue
        try:
            with urllib.request.urlopen(source['url'],timeout=30) as resp: blob=resp.read()
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                with z.open(z.namelist()[0]) as raw:
                    for row in csv.reader(io.TextIOWrapper(raw)):
                        try: o,c=int(row[0]),int(row[6])
                        except (ValueError,IndexError): continue
                        unit=1000 if o>=10**14 else 1
                        if o%unit or c%unit!=unit-1: invalid.append('precision'); continue
                        o=o//unit; c=c//unit
                        if not start<=o<end: continue
                        if o%300000 or c!=o+299999 or o in seen: invalid.append(o)
                        seen.add(o)
            files+=1
        except Exception as exc:
            errors.append({'url':source['url'],'error':str(exc)})
    missing=(end-start)//300000-len(seen)
    result[key]={'status':'verified' if not errors and not invalid and missing==0 else 'incomplete','source_files_read':files,'rows':len(seen),'missing':missing,'invalid_count':len(invalid),'errors':errors}
    tmp=DEST.with_suffix('.tmp'); tmp.write_text(json.dumps(result,indent=2)+'\n');os.replace(tmp,DEST)
    print(key,result[key]['status'],len(seen),missing,len(invalid),len(errors),flush=True)
