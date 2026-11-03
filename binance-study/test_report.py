import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import report
import datetime as dt

class ReportTests(unittest.TestCase):
    def test_final_day_is_its_own_checkpoint_partition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            symbol = 'BTCUSDT'
            (root / 'universe.json').write_text(json.dumps({'symbols': [symbol]}))
            events = root / 'events' / symbol
            events.mkdir(parents=True)
            event_file = events / '2026-10-27.jsonl.gz'
            with gzip.open(event_file, 'wt'):
                pass
            source_url = 'https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/5m/BTCUSDT-5m-2026-10-27.zip'
            quality = {
                'expected_5m_bars': 288, 'valid_5m_bars': 288, 'missing_5m_bars': 0,
                'duplicate_rows': 0, 'invalid_rows': 0, 'out_of_range_rows': 0,
                'missing_archives': 0, 'unavailable_archives': 0, 'downloaded_bytes': 11,
                'missing_5m_ranges': [],
                'complete_buckets': {frame: 288 * 300_000 // duration for frame, duration in report.FRAMES_MS.items()},
                'incomplete_buckets': {frame: 0 for frame in report.FRAMES_MS},
            }
            checkpoint = {
                'version': 1, 'symbol': symbol, 'month': '2026-10-27',
                'start': '2026-10-27', 'end': '2026-10-27',
                'event_file': str(event_file.relative_to(root)),
                'event_counts': {frame: 0 for frame in report.FRAMES_MS},
                'quality': quality,
                'sources': [{'url': source_url, 'source_id': hashlib.sha256(source_url.encode()).hexdigest(),
                             'status': 'available', 'bytes': 1}],
            }
            cp_dir = root / 'checkpoints' / symbol
            cp_dir.mkdir(parents=True)
            (cp_dir / '2026-10-27.json').write_text(json.dumps(checkpoint))

            summary, validation = report.build_report(root)

            self.assertEqual(summary['scope']['end'], '2026-10-27')
            self.assertEqual(summary['inputs']['checkpoints'], 1)
            self.assertFalse(any(c.get('code') == 'checkpoint_date_coverage_mismatch' for c in validation['checks']))
            self.assertFalse(any(c.get('code') == 'expected_bar_coverage_mismatch' for c in validation['checks']))
            untracked = [c for c in validation['checks'] if c.get('code') == 'untracked_downloaded_bytes']
            self.assertEqual(len(untracked), 1)
            self.assertEqual(untracked[0]['untracked_bytes'], 10)
            self.assertEqual(summary['archive_coverage']['untracked_download_bytes'], 10)

    def test_provenance_distinguishes_third_party(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'checkpoints' / 'BTCUSDT').mkdir(parents=True)
            (root / 'checkpoints' / 'BTCUSDT' / '2026-04.json').write_text(json.dumps({'symbol':'BTCUSDT','month':'2026-04','version':1,'selected_source':'huggingface','verification_status':'third_party_pending_official_validation','quality':{'missing_5m_bars':0},'sources':[]}))
            summary, validation = report.build_report(root)
            self.assertEqual(summary['source_provenance']['symbol_month_counts']['third_party_pending_official_validation'],1)
            self.assertTrue(any(c.get('code')=='third_party_official_validation_pending' for c in validation['checks']))
    def test_unselected_outputs_do_not_count_as_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'universe.json').write_text(json.dumps({'symbols': [{'symbol':'SELECTEDUSDT'}]}))
            (root / 'events' / 'OTHERUSDT').mkdir(parents=True)
            with gzip.open(root / 'events' / 'OTHERUSDT' / '2026-04.jsonl.gz', 'wt') as f:
                f.write(json.dumps({'symbol':'OTHERUSDT'}) + '\n')
            with gzip.open(root / 'source_manifest.json.gz', 'wt') as f:
                json.dump({'sources':[], 'analysis_windows':[]}, f)
            summary, validation = report.build_report(root)
            self.assertEqual(summary['inputs']['events'], 0)
            self.assertEqual(summary['inputs']['symbols_with_checkpoints'], 0)
            self.assertFalse(any(c.get('code') == 'invalid_event' for c in validation['checks']))

if __name__ == '__main__':
    unittest.main()
