#!/usr/bin/env python3
"""Sequential, resumable Binance USD-M futures kline archive event extractor."""
from __future__ import annotations
import argparse
import calendar
import csv
import datetime as dt
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import math
import io
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from contextlib import ExitStack

UTC = dt.timezone.utc
START = dt.date(2026, 4, 1)
END = dt.date(2026, 10, 27)  # inclusive
EXTENSION_DAY = dt.date(2026, 10, 27)
BASE = 'https://data.binance.vision/data/futures/um'
INTERVALS = {'5m': 1, '15m': 3, '1h': 12, '4h': 48, '1d': 288}
VERSION = 1


def iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000, UTC).isoformat().replace('+00:00', 'Z')


def timestamp_ms(value):
    value = int(value)
    return value // 1000 if value >= 10**14 else value


def fetch(url, attempts=3):
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'binance-study/1.0'})
            with urllib.request.urlopen(req, timeout=90) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == attempts - 1:
                raise
        except (OSError, TimeoutError):
            if attempt == attempts - 1:
                raise
        time.sleep(2 ** attempt)


def archive_url(symbol, cadence, stamp):
    return f'{BASE}/{cadence}/klines/{symbol}/5m/{symbol}-5m-{stamp}.zip'


def read_archive(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = [n for n in archive.namelist() if not n.endswith('/')]
        if len(names) != 1:
            raise ValueError('Expected one CSV file in archive')
        with archive.open(names[0]) as member:
            yield from csv.reader(io.TextIOWrapper(member, encoding='utf-8-sig', newline=''))


def month_ranges(start=START, end=END):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        first = dt.date(year, month, 1)
        last = dt.date(year, month, calendar.monthrange(year, month)[1])
        yield max(first, start), min(last, end), first == max(first, start) and last == min(last, end)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def processing_ranges(start, end):
    """Keep the original October checkpoint separate from the added day."""
    for first, last, full in month_ranges(start, end):
        if first <= EXTENSION_DAY <= last:
            if first < EXTENSION_DAY:
                yield first, EXTENSION_DAY - dt.timedelta(days=1), False
            yield EXTENSION_DAY, EXTENSION_DAY, False
            if last > EXTENSION_DAY:
                yield EXTENSION_DAY + dt.timedelta(days=1), last, False
        else:
            yield first, last, full


def acquire(symbol, start, end, full_month, fetcher=fetch, monthly_only=False):
    sources = []
    def get(url):
        try:
            data = fetcher(url)
            status = 'available' if data is not None else 'missing'
            error = None
        except (OSError, TimeoutError) as exc:
            data, status, error = None, 'unavailable', str(exc)
        item = {'url': url, 'source_id': hashlib.sha256(url.encode()).hexdigest(),
                'status': status, 'bytes': len(data) if data else 0}
        if error:
            item['error'] = error
        sources.append(item)
        return data
    def archives():
        # The extension is a distinct daily checkpoint: never download the
        # earlier October bars again when resuming a completed prior run.
        extension = start == end == EXTENSION_DAY
        if not extension:
            url = archive_url(symbol, 'monthly', start.strftime('%Y-%m'))
            data = get(url)
            if data is not None:
                yield url, data
                return
            if monthly_only:
                return
        day = start
        while day <= end:
            url = archive_url(symbol, 'daily', day.isoformat())
            data = get(url)
            if data is not None:
                yield url, data
            elif sources[-1]['status'] == 'unavailable':
                # A transient service outage is not evidence of missing daily files.
                # Preserve the uncovered interval and allow a later checkpoint retry.
                break
            day += dt.timedelta(days=1)
    return archives(), sources


def calculate(symbol, start, end, blobs, previous=None):
    start_ms = int(dt.datetime.combine(start, dt.time.min, UTC).timestamp() * 1000)
    end_ms = int(dt.datetime.combine(end + dt.timedelta(days=1), dt.time.min, UTC).timestamp() * 1000)
    rows = {}
    quality = Counter()
    for url, blob in blobs:
        source_id = hashlib.sha256(url.encode()).hexdigest()
        for row in (read_archive(blob) if isinstance(blob, bytes) else blob):
            if row and row[0].lower() in ('open_time', 'opentime'):
                continue
            try:
                raw_timestamp = int(row[0])
                timestamp = timestamp_ms(raw_timestamp)
                unit = 1000 if raw_timestamp >= 10**14 else 1
                if raw_timestamp % (300000 * unit):
                    raise ValueError('misaligned candle open timestamp')
                if int(row[6]) != raw_timestamp + 300000 * unit - 1:
                    raise ValueError('invalid candle close timestamp')
                op, hi, lo, close, volume = map(Decimal, row[1:6])
                quote_volume = Decimal(row[7])
                if timestamp % 300000 or not all(math.isfinite(x) for x in (op, hi, lo, close, volume, quote_volume)) or min(op, hi, lo, close) <= 0 or volume < 0 or quote_volume < 0 or hi < max(op, close, lo) or lo > min(op, close, hi):
                    raise ValueError('invalid bar')
                if not start_ms <= timestamp < end_ms:
                    quality['out_of_range_rows'] += 1
                    continue
                if timestamp in rows:
                    quality['duplicate_rows'] += 1
                    continue
                rows[timestamp] = (op, hi, lo, close, volume, quote_volume, source_id)
            except (ValueError, IndexError, OverflowError, InvalidOperation):
                quality['invalid_rows'] += 1
    expected = (end_ms - start_ms) // 300000
    quality['expected_5m_bars'] = expected
    quality['valid_5m_bars'] = len(rows)
    quality['missing_5m_bars'] = expected - len(rows)
    ranges = []
    run = None
    for timestamp in range(start_ms, end_ms, 300000):
        if timestamp not in rows and run is None:
            run = timestamp
        elif timestamp in rows and run is not None:
            ranges.append({'start': iso(run), 'end': iso(timestamp)})
            run = None
    if run is not None:
        ranges.append({'start': iso(run), 'end': iso(end_ms)})
    quality['missing_5m_ranges'] = ranges
    quality['complete_buckets'] = {k: 0 for k in INTERVALS}

    previous = dict(previous or {})
    events = []
    counts = Counter()
    incomplete = Counter()
    for frame, n in INTERVALS.items():
        duration = n * 300000
        for t in range(start_ms, end_ms, duration):
            bars = [rows.get(t + i * 300000) for i in range(n)]
            if any(bar is None for bar in bars):
                incomplete[frame] += 1
                continue
            quality['complete_buckets'][frame] += 1
            op, hi, lo, close, vol = bars[0][0], max(b[1] for b in bars), min(b[2] for b in bars), bars[-1][3], sum(b[4] for b in bars)
            change = (close / op - 1) * 100
            if close > op * Decimal('1.01') or close < op * Decimal('0.99'):
                event_id = hashlib.sha256(f'{symbol}|{frame}|{t}'.encode()).hexdigest()
                events.append({'event_id': event_id, 'market': 'USD-M futures', 'symbol': symbol, 'timeframe': frame, 'open_time': iso(t), 'close_time': iso(t + duration), 'open': float(op), 'high': float(hi), 'low': float(lo), 'close': float(close), 'base_volume': float(vol), 'quote_volume': float(sum(b[5] for b in bars)), 'change_pct': float(change), 'direction': 'up' if change > 0 else 'down', 'source_ids': sorted({b[6] for b in bars}), 'data_quality': 'complete'})
                counts[frame] += 1
    events.sort(key=lambda x: (x['open_time'], x['timeframe']))
    quality['incomplete_buckets'] = {k: incomplete[k] for k in INTERVALS}
    return events, dict(quality), previous, {k: counts[k] for k in INTERVALS}


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, sort_keys=True, allow_nan=False)
        f.write('\n')
    os.replace(temp, path)


def run_symbol(symbol, root, start=START, end=END, fetcher=fetch, hf_fallback=True, revalidate_close_times=False):
    # One HF 5m archive is lazily opened per symbol and shared across months.
    with ExitStack() as stack:
        return _run_symbol(symbol, root, start, end, fetcher, hf_fallback, revalidate_close_times, stack)


def _run_symbol(symbol, root, start, end, fetcher, hf_fallback, revalidate_close_times, stack):
    previous = {}
    hf_archive = None
    hf_open_failed = False
    for first, last, full in processing_ranges(start, end):
        month = first.isoformat() if first == last == EXTENSION_DAY else first.strftime('%Y-%m')
        checkpoint = root / 'checkpoints' / symbol / (month + '.json')
        output = root / 'events' / symbol / (month + '.jsonl.gz')
        saved = None
        if checkpoint.exists() and output.exists():
            saved = json.loads(checkpoint.read_text())
            if (saved.get('version') == VERSION and saved.get('start') == first.isoformat()
                    and saved.get('end') == last.isoformat()
                    and saved.get('quality', {}).get('missing_5m_bars') == 0
                    and saved.get('quality', {}).get('invalid_rows', 0) == 0
                    and saved.get('quality', {}).get('duplicate_rows', 0) == 0):
                previous = saved['previous']
                if not revalidate_close_times or saved.get('close_time_validation', {}).get('status') == 'verified':
                    print(f'{symbol} {month}: checkpoint', flush=True)
                    continue
                # Revalidate only original official data. Never rewrite its events on a failed check.
                blobs, sources = acquire(symbol, first, last, full, fetcher)
                check_events, check_quality, _, _ = calculate(symbol, first, last, blobs)
                try:
                    with gzip.open(output, 'rt', encoding='utf-8') as f:
                        stored_events = [json.loads(line) for line in f if line.strip()]
                except (OSError, ValueError):
                    stored_events = None
                verified = (check_quality.get('missing_5m_bars') == 0 and
                            check_quality.get('invalid_rows', 0) == 0 and
                            check_quality.get('duplicate_rows', 0) == 0 and stored_events == check_events)
                saved['close_time_validation'] = {
                    'status': 'verified' if verified else 'pending',
                    'method': 're-read official 5m source close timestamps and compare events',
                    'checked_at': dt.datetime.now(UTC).isoformat(),
                    'sources': sources,
                    'reason': None if verified else 'official data unavailable, invalid, incomplete, or event mismatch',
                }
                atomic_json(checkpoint, saved)
                print(f'{symbol} {month}: close timestamp validation {saved["close_time_validation"]["status"]}', flush=True)
                continue
        # Try the official monthly source first. If absent/incomplete, evaluate a
        # complete alternate independently; never combine rows across providers.
        blobs, sources = acquire(symbol, first, last, full, fetcher, monthly_only=hf_fallback)
        events, quality, previous, counts = calculate(symbol, first, last, blobs, previous)
        selected_source = 'binance'
        daily_attempted = False
        if hf_fallback and quality['missing_5m_bars'] and sources and sources[0]['status'] == 'missing':
            # A confirmed missing monthly archive can still have published daily archives.
            daily_blobs, daily_sources = acquire(symbol, first, last, full, fetcher)
            daily_events, daily_quality, daily_previous, daily_counts = calculate(symbol, first, last, daily_blobs, previous)
            daily_attempted = True
            if daily_quality['valid_5m_bars'] >= quality['valid_5m_bars']:
                events, quality, previous, counts, sources = daily_events, daily_quality, daily_previous, daily_counts, daily_sources
        fallback_failure = None
        hf_bytes_this_month = 0
        if hf_fallback and quality['missing_5m_bars']:
            try:
                if hf_archive is None and not hf_open_failed:
                    import hf_source
                    hf_archive = stack.enter_context(hf_source.open_archive(symbol, '5m'))
                    hf_bytes_this_month = hf_archive.source.get('bytes', 0)
                if hf_archive is None:
                    raise RuntimeError('Hugging Face archive was unavailable earlier in this symbol')
                start_ms = int(dt.datetime.combine(first, dt.time.min, UTC).timestamp() * 1000)
                end_ms = int(dt.datetime.combine(last + dt.timedelta(days=1), dt.time.min, UTC).timestamp() * 1000)
                hf_events, hf_quality, hf_prev, hf_counts = calculate(
                    symbol, first, last,
                    [(hf_archive.source['url'], hf_archive.iter_rows(start_ms, end_ms))], previous)
                if (hf_quality['valid_5m_bars'] > quality['valid_5m_bars'] and
                        not hf_quality.get('invalid_rows', 0) and not hf_quality.get('duplicate_rows', 0)):
                    events, quality, previous, counts = hf_events, hf_quality, hf_prev, hf_counts
                    selected_source = 'huggingface'
                    sources.append(dict(hf_archive.source))
            except Exception as exc:
                fallback_failure = f'{type(exc).__name__}: {exc}'
                if hf_archive is None:
                    hf_open_failed = True
            if selected_source == 'binance' and quality['missing_5m_bars'] and not daily_attempted:
                # A failed/unusable HF archive does not prevent official daily fallback.
                blobs, daily_sources = acquire(symbol, first, last, full, fetcher)
                daily_events, daily_quality, daily_previous, daily_counts = calculate(symbol, first, last, blobs, previous)
                if daily_quality['valid_5m_bars'] > quality['valid_5m_bars']:
                    events, quality, previous, counts = daily_events, daily_quality, daily_previous, daily_counts
                    sources = daily_sources
        quality['fallback_failure'] = fallback_failure
        quality['downloaded_bytes'] = sum(s.get('bytes', 0) for s in sources if s.get('source_type') != 'hf_parquet') + hf_bytes_this_month
        quality['missing_archives'] = sum(s['status'] == 'missing' for s in sources)
        quality['unavailable_archives'] = sum(s['status'] == 'unavailable' for s in sources)
        if (saved and saved.get('start') == first.isoformat() and saved.get('end') == last.isoformat()
                and quality['valid_5m_bars'] < saved.get('quality', {}).get('valid_5m_bars', 0)):
            print(f'{symbol} {month}: retaining prior checkpoint with more valid bars', flush=True)
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix(output.suffix + '.tmp')
        with gzip.open(temp, 'wt', encoding='utf-8', newline='\n') as f:
            for event in events:
                f.write(json.dumps(event, separators=(',', ':'), allow_nan=False) + '\n')
        os.replace(temp, output)
        atomic_json(checkpoint, {'version': VERSION, 'symbol': symbol, 'month': month, 'start': first.isoformat(), 'end': last.isoformat(), 'sources': sources, 'selected_source': selected_source, 'verification_status': ('official_archive' if selected_source == 'binance' else 'third_party_pending_official_validation') if quality['valid_5m_bars'] else 'no_data', 'close_time_validation': {'status': ('verified' if not quality['missing_5m_bars'] and not quality.get('invalid_rows', 0) and not quality.get('duplicate_rows', 0) else 'incomplete') if selected_source == 'binance' else 'third_party_source_only'}, 'quality': quality, 'event_counts': counts, 'previous': previous, 'event_file': str(output.relative_to(root))})
        print(f'{symbol} {month}: {sum(counts.values())} events; {quality["missing_5m_bars"]} missing bars', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--universe', type=Path, default=Path('output/universe.json'))
    p.add_argument('--output', type=Path, default=Path('output'))
    p.add_argument('--symbol', action='append', help='Process only named symbol(s)')
    p.add_argument('--no-hf-fallback', action='store_true')
    p.add_argument('--revalidate-close-times', action='store_true', help='Re-read official sources for completed checkpoints without replacing their events')
    args = p.parse_args()
    universe = json.loads(args.universe.read_text())
    symbols = args.symbol or (universe['symbols'] if isinstance(universe, dict) else universe)
    for item in symbols:
        symbol = item['symbol'] if isinstance(item, dict) else item
        run_symbol(symbol, args.output, hf_fallback=not args.no_hf_fallback, revalidate_close_times=args.revalidate_close_times)

if __name__ == '__main__':
    main()
