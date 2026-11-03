import tempfile, unittest
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
from hf_source import HFArchive
class HfSourceTests(unittest.TestCase):
 def test_preserve_timestamps_and_filter(self):
  schema=pa.schema([('open_time',pa.timestamp('ms')),('open',pa.string()),('high',pa.string()),('low',pa.string()),('close',pa.string()),('volume',pa.string()),('close_time',pa.timestamp('ms')),('quote_volume',pa.string())])
  t=pa.Table.from_arrays([pa.array([0,300000],type=pa.timestamp('ms')),pa.array(['1','2']),pa.array(['2','3']),pa.array(['1','2']),pa.array(['2','3']),pa.array(['4','5']),pa.array([299999,599999],type=pa.timestamp('ms')),pa.array(['5','6'])],schema=schema)
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.parquet';pq.write_table(t,p);a=HFArchive(p,{}); rows=list(a.iter_rows(300000,600000))
   self.assertEqual(rows[0][:8],['300000','2','3','2','3','5','599999','6']);self.assertEqual(len(rows),1)
 def test_missing_columns(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.parquet';pq.write_table(pa.table({'open':[1]}),p)
   with self.assertRaises(ValueError): HFArchive(p,{})
if __name__=='__main__': unittest.main()
