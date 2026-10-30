import io,sys,zipfile,unittest
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from data import aggregate,_parse_row,load_symbol
class DataTests(unittest.TestCase):
 def test_validates_close(self):
  row=['0','100','102','99','101','2','299999','200']
  self.assertEqual(_parse_row(row)[0],0)
  row[6]='300000'
  with self.assertRaises(ValueError):_parse_row(row)
 def test_gap_invalidates_bucket(self):
  row=(100.,102.,99.,101.,2.,200.)
  rows={0:row,300000:row,600000:row,1200000:row}
  f=aggregate(rows,0,1500000)
  self.assertEqual(len(f['5m']),4);self.assertEqual(len(f['15m']),1)
  self.assertEqual(f['15m'].iloc[0]['volume'],6)
 def test_one_day_and_duplicate_rejected(self):
  begin=int(datetime(2026,4,1,tzinfo=timezone.utc).timestamp()*1000)
  line=lambda t:f'{t},100,102,99,101,2,{t+299999},200,1,0,0,0\n'
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:z.writestr('a.csv',line(begin)+line(begin)+line(begin+300000))
  def fetch(url): return b.getvalue(),'available',None
  result=load_symbol('BTCUSDT','2026-04-01','2026-04-01',0,fetch)
  self.assertEqual(result['quality']['duplicate_rows'],1)
  self.assertEqual(result['quality']['valid_5m'],1)
  self.assertEqual(len(result['15m']),0)
if __name__=='__main__':unittest.main()
