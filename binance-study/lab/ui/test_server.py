import unittest, tempfile, gzip, json
from pathlib import Path
import server
class TestDashboard(unittest.TestCase):
 def test_real_filtered_events(self):
  out=server.events({'symbol':['BTCUSDT'],'timeframe':['1d'],'direction':['DOWN'],'limit':['3']})
  self.assertEqual(len(out['items']),3)
  self.assertTrue(all(x['symbol']=='BTCUSDT' and x['timeframe']=='1d' and x['direction'].upper()=='DOWN' for x in out['items']))
 def test_path_traversal_rejected(self):
  self.assertIn('error',server.events({'symbol':['../BTCUSDT']}))
 def test_feature_join_and_future_guard(self):
  original=server.LAB
  try:
   with tempfile.TemporaryDirectory() as tmp:
    server.LAB=Path(tmp);d=server.LAB/'results'/'event_features';d.mkdir(parents=True)
    rows=[{'event_id':'ok','symbol':'BTCUSDT','event_open_time':'2026-04-01T00:00:00Z','decision_time':'2026-04-01T00:00:00Z','indicator_signals':{'rsi':1},'quality':'complete'}, {'event_id':'bad','symbol':'BTCUSDT','event_open_time':'2026-04-01T00:00:00Z','decision_time':'2026-04-01T00:05:00Z'}]
    with gzip.open(d/'BTCUSDT.jsonl.gz','wt') as f:
     for row in rows:f.write(json.dumps(row)+'\n')
    events=[{'event_id':k,'open_time':'2026-04-01T00:00:00Z'} for k in ('ok','bad','missing')]
    out=server.attach_features('BTCUSDT',events)
    self.assertEqual(out[0]['prior_features']['indicator_signals']['rsi'],1)
    self.assertIsNone(out[1]['prior_features']); self.assertIsNone(out[2]['prior_features'])
  finally:server.LAB=original
 def test_v2_pending_and_untrusted_symbol(self):
  original=server.LAB
  try:
   with tempfile.TemporaryDirectory() as tmp:
    server.LAB=Path(tmp)
    self.assertIsNone(server.research_v2()['ledger'])
    self.assertEqual(server.benchmark_v2_symbol({'symbol':['BTCUSDT']})['status'],'pending')
    self.assertEqual(server.benchmark_v2_symbol({'symbol':['../BTCUSDT']})['status'],'invalid')
  finally:server.LAB=original
 def test_v2_filtered_paginated_checkpoint(self):
  original=server.LAB
  try:
   with tempfile.TemporaryDirectory() as tmp:
    server.LAB=Path(tmp);d=server.LAB/'results'/'benchmark_v2'/'symbols';d.mkdir(parents=True)
    obj={'symbol':'BTCUSDT','status':'processed','records':[{'timeframe':'5m','direction':'LONG','month':'2026-04','indicator':str(i)} for i in range(120)]+[{'timeframe':'1h','direction':'SHORT','month':'2026-05'}]}
    with gzip.open(d/'BTCUSDT.json.gz','wt') as f: json.dump(obj,f)
    x=server.benchmark_v2_symbol({'symbol':['BTCUSDT'],'timeframe':['5m'],'direction':['LONG'],'month':['2026-04'],'offset':['50'],'limit':['50']})
    self.assertEqual(x['total'],120);self.assertEqual(len(x['items']),50);self.assertEqual(x['items'][0]['indicator'],'50')
    self.assertEqual(server.benchmark_v2_symbol({'symbol':['BTCUSDT'],'offset':['bad']})['status'],'invalid')
  finally:server.LAB=original
 def test_actual_summary(self):
  x=server.read(server.OUT/'summary.json',{})
  self.assertGreater(x['inputs']['events'],0)
if __name__=='__main__':unittest.main()
