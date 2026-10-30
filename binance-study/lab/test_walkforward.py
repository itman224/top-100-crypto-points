import json,tempfile,unittest
from pathlib import Path
from walkforward import analyze,bh,sign_test
class WalkForwardTests(unittest.TestCase):
 def test_bh(self):
  q=bh([0.01,0.04,0.03,None]);self.assertEqual(q[3],None);self.assertAlmostEqual(q[0],.03);self.assertAlmostEqual(q[1],.04)
 def test_sign(self):
  self.assertEqual(sign_test([1,1,1]),.125);self.assertIsNone(sign_test([0,0]))
 def test_train_select_and_no_holdout(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'rows.jsonl'; rows=[]
   for name,train,test in [('a',1.,-1.),('b',.5,20.)]:
    for date,val in [('2026-05-01',train),('2026-07-01',test),('2026-09-01',9999.)]:
     rows.append(dict(symbol='BTCUSDT',timeframe='1h',strategy=name,direction='LONG',date=date,trades=10,net_return_sum=val,net_squared_sum=100.))
   p.write_text(''.join(json.dumps(r)+'\n' for r in rows)); r=analyze(p,top=1,minimum_trades=1,minimum_symbols=1)
   july=[x for x in r['results'] if x['fold']=='wf_july'];self.assertEqual(len(july),1);self.assertEqual(july[0]['strategy'],'a');self.assertEqual(july[0]['test']['net_return_sum'],-1.)
 def test_duplicate_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'rows';r=dict(symbol='A',timeframe='5m',strategy='s',direction='LONG',date='2026-05-01',trades=1,net_return_sum=.1,net_squared_sum=.01);p.write_text((json.dumps(r)+'\n')*2)
   with self.assertRaisesRegex(ValueError,'duplicate grain'):analyze(p,minimum_symbols=1)
if __name__=='__main__':unittest.main()
