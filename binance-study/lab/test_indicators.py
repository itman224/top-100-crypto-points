import unittest
import numpy as np
import pandas as pd
from indicators import compute_signals,SIGNALS,FILTERS

class IndicatorTests(unittest.TestCase):
    def data(self,n=140):
        rng=np.random.default_rng(917); close=100+np.cumsum(rng.normal(0,.5,n)); high=close+.9; low=close-.9
        return pd.DataFrame({'open':close-.05,'high':high,'low':low,'close':close,'volume':100+np.arange(n)%17})
    def test_causality(self):
        data=self.data(); a=compute_signals(data); future=data.copy(); future.loc[100:,['open','high','low','close','volume']]*=2
        b=compute_signals(future); pd.testing.assert_frame_equal(a.iloc[:100],b.iloc[:100])
    def test_warmup_and_short_segments(self):
        a=compute_signals(self.data(10)); self.assertTrue(a.iloc[:7].isna().all().all())
        a=compute_signals(self.data()); self.assertEqual(set(a.columns),set(SIGNALS)|set(FILTERS))
        self.assertTrue(a.iloc[:7].isna().all().all()); self.assertTrue(set(a.stack().unique())<= {-1.,0.,1.})
    def test_prior_donchian_and_execution_delay(self):
        data=self.data(); i=100; data.loc[i,'close']=data.loc[i-20:i-1,'high'].max()+.1; data.loc[i,'high']=data.loc[i,'close']+.1
        a=compute_signals(data); self.assertEqual(a.loc[i,'donchian_breakout_20'],1.0)
        self.assertEqual(a['donchian_breakout_20'].shift(1).loc[i+1],1.0)
    def test_unsorted_index_rejected(self):
        with self.assertRaises(ValueError): compute_signals(self.data().iloc[::-1])
if __name__=='__main__': unittest.main()
