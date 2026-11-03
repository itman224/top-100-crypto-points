import unittest
from unittest.mock import patch
from contextlib import contextmanager
from universe import quote_volume_from_hf, MONTH_START_MS

class FakeArchive:
    source = {'source_type':'hf_parquet','dataset_revision':'a'*40}
    def iter_rows(self, start, end):
        for i in range(30):
            t = MONTH_START_MS+i*86400000
            yield [str(t),'1','2','1','2','3',str(t+86399999),'4.25']
@contextmanager
def fake(*args):
    yield FakeArchive()
class UniverseTests(unittest.TestCase):
    def test_fallback_volume_and_provenance(self):
        with patch('hf_source.open_archive', fake):
            result=quote_volume_from_hf('BTCUSDT')
        self.assertEqual(result['volume'],'127.50')
        self.assertEqual(result['observed_days'],30)
        self.assertFalse(result['official_verified'])
        self.assertEqual(result['source']['dataset_revision'],'a'*40)
if __name__=='__main__': unittest.main()
