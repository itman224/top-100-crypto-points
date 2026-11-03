import unittest
import numpy as np,pandas as pd
import benchmark_v2 as b
class BenchmarkTests(unittest.TestCase):
 def setUp(self):
  ix=pd.date_range('2026-04-01',periods=160,freq='5min',tz='UTC'); close=pd.Series(100+np.sin(np.arange(len(ix))/7)+np.arange(len(ix))*.01,index=ix)
  self.f=pd.DataFrame({'open':close,'high':close+1,'low':close-1,'close':close,'volume':np.arange(len(ix))+5},index=ix)
 def test_causality_and_direction(self):
  for name in b.SPECS:
   first=b.signal(name,self.f);future=self.f.copy();future.iloc[130:]*=2;second=b.signal(name,future)
   pd.testing.assert_series_equal(first.iloc[:130],second.iloc[:130])
   self.assertTrue(set(first.dropna().unique()).issubset({-1.,0.,1.}))
 def test_score_gap_and_month(self):
  frame=self.f.drop(self.f.index[100]);rows,errors=b.run_frame('BTCUSDT','5m',frame)
  self.assertTrue(rows);self.assertTrue(all(r['month']=='2026-04' for r in rows));self.assertEqual(len({r['segment_start'] for r in rows}),2)
  for r in rows:
   m=r['metrics'];self.assertEqual(m['tp']+m['fp']+m['tn']+m['fn'],m['valid_samples'])
if __name__=='__main__':unittest.main()
