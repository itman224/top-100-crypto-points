import unittest
import tempfile, gzip, hashlib, json
from pathlib import Path
from unittest.mock import patch
from importlib.metadata import version
import event_features as ef
import numpy as np
import pandas as pd
from event_features import annotate,signal_frames
class TestEventFeatures(unittest.TestCase):
 def test_prior_only_and_gap(self):
  idx=pd.DatetimeIndex(['2026-04-01T00:00Z','2026-04-01T00:05Z','2026-04-01T00:15Z'])
  frame=pd.DataFrame({'close':[1,2,999]},index=idx)
  def compute(part): return pd.DataFrame({'x':part.close},index=part.index)
  signals=signal_frames(frame,'5m',compute)
  event={'event_id':'x','symbol':'BTCUSDT','timeframe':'5m','direction':'up','open_time':'2026-04-01T00:05:00Z'}
  self.assertEqual(annotate(event,frame,signals)['indicator_signals']['x'],1)
  event['open_time']='2026-04-01T00:15:00Z'
  self.assertEqual(annotate(event,frame,signals)['quality'],'prior_candle_unavailable')
 def test_neutral_distinct_missing(self):
  idx=pd.date_range('2026-04-01',periods=2,freq='5min',tz='UTC'); frame=pd.DataFrame({'x':[1,2]},index=idx)
  signals=pd.DataFrame({'warmup':[np.nan,1],'neutral':[0,1]},index=idx)
  event={'event_id':'x','symbol':'BTCUSDT','timeframe':'5m','direction':'up','open_time':'2026-04-01T00:05:00Z'}
  x=annotate(event,frame,signals);self.assertEqual(x['quality'],'warmup_incomplete');self.assertIsNone(x['indicator_signals']['warmup']);self.assertEqual(x['indicator_signals']['neutral'],0)
class TestCheckpoint(unittest.TestCase):
 def test_reuses_valid_metadata_without_loading(self):
  with tempfile.TemporaryDirectory(dir=ef.PROJECT) as tmp:
   base=Path(tmp); source=base/'events'/'BTCUSDT'; source.mkdir(parents=True); event=source/'2026-04.jsonl.gz'
   with gzip.open(event,'wt') as f: f.write('')
   output=base/'features';output.mkdir()
   with gzip.open(output/'BTCUSDT.jsonl.gz','wt') as f: f.write('')
   h=hashlib.sha256()
   for name in ('event_features.py','data.py','indicators.py'): h.update((ef.ROOT/name).read_bytes())
   meta={'experiment_hash':h.hexdigest(),'event_file_sha256':{str(event.relative_to(ef.PROJECT)):hashlib.sha256(event.read_bytes()).hexdigest()},'pandas_ta_classic_version':version('pandas-ta-classic'),'event_count':0,'quality_counts':{},'market_data_quality':{'sources':[]}}
   (output/'BTCUSDT.meta.json').write_text(json.dumps(meta))
   with patch.object(ef,'load_symbol',side_effect=AssertionError('unexpected load')):
    ef.annotate_symbol('BTCUSDT',output=output,events=base/'events')
if __name__=='__main__':unittest.main()
