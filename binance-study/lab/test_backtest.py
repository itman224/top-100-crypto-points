import unittest
import numpy as np
import pandas as pd
from backtest import score, analyze_frame
class BacktestTests(unittest.TestCase):
 def test_strict_direction_and_cost(self):
  r=score([1,1,0,-1],[.01,.02,.03,-.02],1)
  self.assertEqual((r['tp'],r['fp'],r['fn']),(1,1,1));self.assertAlmostEqual(r['net_return_sum'],.028)
 def test_exact_threshold(self):
  idx=pd.date_range('2026-04-01',periods=3,freq='5min',tz='UTC')
  f=pd.DataFrame({'open':[100.,100.,100.], 'close':[100.,101.,101.000001]},index=idx)
  def compute(d):return pd.DataFrame({'rsi_reversal_14':[1]*len(d)},index=d.index)
  r=analyze_frame('T','5m',f,compute)
  m=next(x['metrics'] for x in r if x['strategy']=='rsi_reversal_14' and x['direction']=='LONG')
  self.assertEqual((m['tp'],m['fp']),(1,1))
  f['close']=[100.,99.,98.999999]
  def negative(d):return pd.DataFrame({'rsi_reversal_14':[-1]*len(d)},index=d.index)
  r=analyze_frame('T','5m',f,negative)
  m=next(x['metrics'] for x in r if x['strategy']=='rsi_reversal_14' and x['direction']=='SHORT')
  self.assertEqual((m['tp'],m['fp']),(1,1))
 def test_next_bar_and_gap(self):
  idx=pd.to_datetime(['2026-04-01T00:00Z','2026-04-01T00:05Z','2026-04-01T00:15Z','2026-04-01T00:20Z'])
  f=pd.DataFrame({'open':[100]*4,'close':[102]*4},index=idx)
  def compute(d):return pd.DataFrame({'rsi_reversal_14':[1]*len(d)},index=d.index)
  r=analyze_frame('T','5m',f,compute)
  relevant=[x for x in r if x['strategy']=='rsi_reversal_14' and x['direction']=='LONG']
  self.assertEqual(sum(x['metrics']['trades'] for x in relevant),2)
  self.assertEqual(sum(x['metrics']['valid_samples'] for x in relevant),2)
if __name__=='__main__':unittest.main()
