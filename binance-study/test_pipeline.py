import datetime as dt
import io
import unittest
import unittest.mock
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
import pipeline


def archive(rows):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('data.csv', '\n'.join(','.join(map(str, r)) for r in rows))
    return data.getvalue()


def bar(t, close):
    unit = 1000 if t >= 10**14 else 1
    return [t, close, close, close, close, 10, t + 300000 * unit - 1, 1000]


class PipelineTests(unittest.TestCase):
    def test_strict_threshold_gap_and_microseconds(self):
        d = dt.date(2026, 4, 1)
        t = int(dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        rows = [bar(t * 1000, 100), [ (t + 300000)*1000, 100, 101, 100, 101, 10, (t + 600000)*1000 - 1, 1000], [(t + 600000)*1000, 100, 103, 100, 103, 10, (t + 900000)*1000 - 1, 1000], bar((t + 1200000)*1000, 110)]
        events, q, previous, counts = pipeline.calculate('BTCUSDT', d, d, [('test-url', archive(rows))])
        five = [e for e in events if e['timeframe'] == '5m']
        self.assertEqual(len(five), 1)
        self.assertAlmostEqual(five[0]['change_pct'], (103 / 100 - 1) * 100)
        self.assertEqual(q['missing_5m_bars'], 284)
        self.assertEqual(q['incomplete_buckets']['15m'], 95)
        self.assertEqual(counts['5m'], 1)
        self.assertEqual(five[0]['close_time'], '2026-04-01T00:15:00Z')

    def test_daily_fallback_and_resume(self):
        d = dt.date(2026, 4, 1)
        calls = []
        t = int(dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        def fetch(url):
            calls.append(url)
            return archive([bar(t + i * 300000, 100) for i in range(288)])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline.run_symbol('BTCUSDT', root, d, d, fetch, hf_fallback=False)
            pipeline.run_symbol('BTCUSDT', root, d, d, fetch, hf_fallback=False)
            self.assertEqual(len(calls), 1)
            self.assertTrue((root / 'events/BTCUSDT/2026-04.jsonl.gz').exists())

    def test_partial_checkpoint_not_reused_for_larger_range(self):
        start = dt.date(2026, 4, 1)
        calls = []
        t = int(dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        def fetch(url):
            calls.append(url)
            return archive([bar(t, 100)])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline.run_symbol('BTCUSDT', root, start, start, fetch, hf_fallback=False)
            pipeline.run_symbol('BTCUSDT', root, start, dt.date(2026, 4, 2), fetch, hf_fallback=False)
            self.assertEqual(len(calls), 2)
            import json
            checkpoint = json.loads((root / 'checkpoints/BTCUSDT/2026-04.json').read_text())
            self.assertEqual(checkpoint['end'], '2026-04-02')
            self.assertEqual(checkpoint['quality']['expected_5m_bars'], 576)

    def test_october_extension_preserves_prior_checkpoint_and_retries_day(self):
        import json
        start = dt.date(2026, 10, 1)
        old_end = dt.date(2026, 10, 26)
        day = dt.date(2026, 10, 27)
        t = int(dt.datetime(2026, 10, 27, tzinfo=dt.timezone.utc).timestamp() * 1000)
        data = archive([bar(t + i * 300000, 100) for i in range(288)])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cp = root / 'checkpoints/BTCUSDT/2026-10.json'
            out = root / 'events/BTCUSDT/2026-10.jsonl.gz'
            cp.parent.mkdir(parents=True)
            out.parent.mkdir(parents=True)
            cp.write_text(json.dumps({'version': pipeline.VERSION, 'start': start.isoformat(),
                'end': old_end.isoformat(), 'quality': {'missing_5m_bars': 0}, 'previous': {}}))
            out.write_bytes(b'preserved')
            before = cp.read_bytes(), out.read_bytes()
            calls = []
            def missing(url):
                calls.append(url)
                return None
            pipeline.run_symbol('BTCUSDT', root, start, day, missing, hf_fallback=False)
            daily_cp = root / 'checkpoints/BTCUSDT/2026-10-27.json'
            self.assertEqual(json.loads(daily_cp.read_text())['quality']['missing_5m_bars'], 288)
            self.assertEqual((cp.read_bytes(), out.read_bytes()), before)
            def fetch(url):
                calls.append(url)
                return data
            pipeline.run_symbol('BTCUSDT', root, start, day, fetch, hf_fallback=False)
            pipeline.run_symbol('BTCUSDT', root, start, day, fetch, hf_fallback=False)
            self.assertEqual(len(calls), 2)
            self.assertTrue(all('/daily/' in url and '2026-10-27.zip' in url for url in calls))
            self.assertEqual(json.loads(daily_cp.read_text())['quality']['missing_5m_bars'], 0)
            self.assertEqual((cp.read_bytes(), out.read_bytes()), before)

    def test_monthly_missing_falls_back_daily(self):
        d = dt.date(2026, 4, 1)
        calls = []
        def fetch(url):
            calls.append(url)
            return None
        blobs, sources = pipeline.acquire('BTCUSDT', d, dt.date(2026, 4, 30), True, fetch)
        self.assertEqual(list(blobs), [])
        self.assertEqual(len(calls), 31)
        self.assertEqual(len(sources), 31)

    def test_transient_month_failure_falls_back_and_retry_missing(self):
        d = dt.date(2026, 4, 1)
        t = int(dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        calls = []
        def fetch(url):
            calls.append(url)
            if '/monthly/' in url:
                raise OSError('503')
            return archive([bar(t + i * 300000, 100) for i in range(288)])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline.run_symbol('BTCUSDT', root, d, d, lambda url: None, hf_fallback=False)
            pipeline.run_symbol('BTCUSDT', root, d, d, fetch, hf_fallback=False)
            self.assertEqual(len(calls), 2)
            import json
            saved = json.loads((root / 'checkpoints/BTCUSDT/2026-04.json').read_text())
            self.assertEqual(saved['quality']['missing_5m_bars'], 0)
            self.assertEqual(saved['sources'][0]['status'], 'unavailable')

    def test_prior_checkpoint_close_time_revalidation_preserves_event_file(self):
        import json
        d = dt.date(2026, 4, 1)
        t = int(dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        data = archive([bar(t+i*300000, 100) for i in range(288)])
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline.run_symbol('BTCUSDT', root, d, d, lambda _: data, hf_fallback=False)
            event_file=root/'events/BTCUSDT/2026-04.jsonl.gz'
            before=event_file.read_bytes()
            checkpoint=root/'checkpoints/BTCUSDT/2026-04.json'
            original=json.loads(checkpoint.read_text()); original.pop('close_time_validation')
            checkpoint.write_text(json.dumps(original))
            pipeline.run_symbol('BTCUSDT', root, d, d, lambda _: data, hf_fallback=False, revalidate_close_times=True)
            self.assertEqual(json.loads(checkpoint.read_text())['close_time_validation']['status'], 'verified')
            self.assertEqual(before, event_file.read_bytes())

    def test_hf_fallback_and_provenance(self):
        import sys, types, contextlib, json, hashlib
        d = dt.date(2026, 4, 1)
        t = int(dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        url = 'https://huggingface.co/pinned/test.parquet'
        class MockArchive:
            source = {'url': url, 'source_id': hashlib.sha256(url.encode()).hexdigest(), 'source_type': 'hf_parquet', 'status': 'available', 'dataset_revision': 'a'*40, 'bytes': 10}
            def iter_rows(self, start, end):
                return iter([[str(x) for x in bar(t+i*300000, 100)] for i in range(288)])
        @contextlib.contextmanager
        def opener(symbol, interval):
            yield MockArchive()
        with TemporaryDirectory() as tmp, unittest.mock.patch.dict(sys.modules, {'hf_source': types.SimpleNamespace(open_archive=opener)}):
            root=Path(tmp)
            pipeline.run_symbol('BTCUSDT', root, d, d, lambda _: None)
            saved=json.loads((root/'checkpoints/BTCUSDT/2026-04.json').read_text())
            self.assertEqual(saved['selected_source'], 'huggingface')
            self.assertEqual(saved['verification_status'], 'third_party_pending_official_validation')
            self.assertEqual(saved['quality']['valid_5m_bars'], 288)
            self.assertEqual(saved['sources'][-1]['dataset_revision'], 'a'*40)

    def test_reject_submillisecond_open_misalignment(self):
        d = dt.date(2026, 4, 1)
        t = int(dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000000)
        row = bar(t + 1, 100)
        _, quality, _, _ = pipeline.calculate('BTCUSDT', d, d, [('url', archive([row]))])
        self.assertEqual(quality['invalid_rows'], 1)
        self.assertEqual(quality['valid_5m_bars'], 0)

    def test_reject_bad_close_timestamp(self):
        d = dt.date(2026, 4, 1)
        t = int(dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        row = bar(t, 100)
        row[6] -= 1
        events, quality, _, _ = pipeline.calculate('BTCUSDT', d, d, [('url', archive([row]))])
        self.assertEqual(quality['invalid_rows'], 1)
        self.assertEqual(quality['valid_5m_bars'], 0)
        self.assertFalse(events)

    def test_negative_threshold_and_preserve_better_partial(self):
        d = dt.date(2026, 4, 1)
        t = int(dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc).timestamp() * 1000)
        rows = [[t, 100, 100, 99, 99, 1, t + 299999, 100],
                [t + 300000, 100, 100, 98, 98, 1, t + 599999, 100]]
        events, _, _, _ = pipeline.calculate('BTCUSDT', d, d, [('url', archive(rows))])
        five = [e for e in events if e['timeframe'] == '5m']
        self.assertEqual(len(five), 1)
        self.assertEqual(five[0]['direction'], 'down')
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pipeline.run_symbol('BTCUSDT', root, d, d, lambda url: archive(rows), hf_fallback=False)
            pipeline.run_symbol('BTCUSDT', root, d, d, lambda url: None, hf_fallback=False)
            import json
            saved = json.loads((root / 'checkpoints/BTCUSDT/2026-04.json').read_text())
            self.assertEqual(saved['quality']['valid_5m_bars'], 2)

if __name__ == '__main__':
    unittest.main()
