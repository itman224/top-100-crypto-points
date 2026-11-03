import gzip, json, tempfile, unittest
from pathlib import Path
from datetime import datetime,timezone
from manifest import build_manifest,source_url,sid,window_start

class ManifestTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.e=self.root/'events'/'BTCUSDT'; self.e.mkdir(parents=True)
  self.url=source_url('BTCUSDT','monthly','2026-06'); self.id=sid(self.url)
  c=self.root/'checkpoints'/'BTCUSDT'; c.mkdir(parents=True)
  (c/'2026-06.json').write_text(json.dumps({'sources':[{'url':self.url,'status':'available'}]}))
 def tearDown(self): self.tmp.cleanup()
 def write(self,rows):
  with gzip.open(self.e/'2026-06.jsonl.gz','wt') as f:
   for x in rows: f.write(json.dumps(x)+'\n')
 def event(self,i,time,close,tf='15m'):
  return {'event_id':i,'symbol':'BTCUSDT','timeframe':tf,'open_time':time,'close_time':close,'source_ids':[self.id]}
 def test_window_and_merge(self):
  self.write([self.event('a','2026-06-15T12:00:00Z','2026-06-15T12:15:00Z'),self.event('b','2026-06-15T12:15:00Z','2026-06-15T12:30:00Z')])
  m=build_manifest(self.e.parent,probe=lambda _: 'verified'); self.assertEqual(len(m['analysis_windows']),1)
  w=m['analysis_windows'][0]; self.assertEqual(w['start_utc'],'2026-06-12T21:30:00Z'); self.assertEqual(w['end_utc'],'2026-06-15T12:30:00Z'); self.assertEqual(set(w['event_ids']),{'a','b'}); self.assertIn(self.id,w['source_ids']); self.assertEqual(len(m['sources']),1)
 def test_missing_month_daily_fallback(self):
  self.write([self.event('a','2026-06-01T00:00:00Z','2026-06-01T00:15:00Z')])
  def probe(u): return 'missing' if '/monthly/' in u else 'verified'
  m=build_manifest(self.e.parent,probe=probe); self.assertTrue(any('/daily/' in x['url'] for x in m['sources'])); self.assertTrue(all(x['verified'] for x in m['sources']))
 def test_inferred_not_missing(self):
  self.write([self.event('a','2026-06-01T00:00:00Z','2026-06-01T00:15:00Z')]); m=build_manifest(self.e.parent,probe=lambda _: 'inferred'); self.assertTrue(any(s['inferred'] and not s['missing'] and not s['verified'] for s in m['sources']))
 def test_per_event_no_future(self):
  self.write([self.event('a','2026-06-15T12:00:00Z','2026-06-15T12:15:00Z')]); m=build_manifest(self.e.parent,probe=lambda _: 'verified'); self.assertEqual(m['events'][0]['window_end'],'2026-06-15T12:15:00Z')
 def test_universe_filter(self):
  self.write([self.event('a','2026-06-15T12:00:00Z','2026-06-15T12:15:00Z')]); (self.root/'universe.json').write_text(json.dumps({'symbols':[{'symbol':'ETHUSDT'}]})); self.assertEqual(build_manifest(self.e.parent)['events'],[])
 def test_hf_event_source_provenance(self):
  url='https://huggingface.co/datasets/example/resolve/abcdef/BTCUSDT/BTCUSDT_5m.parquet'; source_id=sid(url)
  cp=self.root/'checkpoints'/'BTCUSDT'/'2026-06.json'
  cp.write_text(json.dumps({'symbol':'BTCUSDT','sources':[{'url':url,'status':'available','source_type':'hf_parquet','dataset_revision':'abcdef'}]}))
  event=self.event('a','2026-06-15T12:00:00Z','2026-06-15T12:15:00Z'); event['source_ids']=[source_id]; self.write([event])
  m=build_manifest(self.e.parent,probe=lambda _: 'inferred'); source=next(x for x in m['sources'] if x['source_id']==source_id)
  self.assertEqual(source['symbol'],'BTCUSDT'); self.assertEqual(source['dataset_revision'],'abcdef'); self.assertEqual(source['official_verification'],'pending'); self.assertIn(source_id,m['analysis_windows'][0]['source_ids'])
if __name__=='__main__': unittest.main()
