import json,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parent
class CoverageTests(unittest.TestCase):
 def test_all_catalog_entries_have_individual_records(self):
  base=json.loads((ROOT/'indicator_catalog.json').read_text()); out=json.loads((ROOT/'coverage_report.json').read_text())
  for kind,key in [('indicator','indicators'),('candlestick_pattern','candlestick_patterns')]:
   self.assertEqual({r['name'] for r in out['capabilities'] if r['kind']==kind},{r['name'] for r in base[key]})
 def test_prior_results_not_confused_with_catalog_availability(self):
  out=json.loads((ROOT/'coverage_report.json').read_text()); self.assertEqual(out['summary']['prior_directional_standalones_in_results'],14)
  byname={r['name']:r for r in out['capabilities'] if r['kind']=='indicator'}
  self.assertEqual(byname['rsi']['prior_status'],'benchmarked');self.assertEqual(byname['adx']['prior_status'],'filter_in_benchmark');self.assertEqual(byname['ichimoku']['semantics'],'requires_causality_review')
  self.assertEqual(byname['sma']['prior_status'],'not_screened')
if __name__=='__main__':unittest.main()
