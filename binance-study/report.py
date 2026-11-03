#!/usr/bin/env python3
"""Summarize and independently validate Binance study outputs.

The report is derived only from files present under --output. Missing inputs are
reported as missing/incomplete; this command does not infer measurements.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
import sys
from typing import Any

FRAMES_MS = {'5m': 5 * 60_000, '15m': 15 * 60_000,
             '1h': 60 * 60_000, '4h': 4 * 60 * 60_000,
             '1d': 24 * 60 * 60_000}
EXPECTED_START = dt.date(2026, 4, 1)
EXPECTED_END = dt.date(2026, 10, 27)
REQUESTED_UNIVERSE_SIZE = 100
MOVEMENT_BINS = ((1, 2), (2, 5), (5, 10), (10, 20), (20, math.inf))


def read_json(path: Path) -> Any:
    with path.open(encoding='utf-8') as f:
        return json.load(f)


def parse_time(value: Any) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError('timestamp is not a string')
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp has no timezone')
    return parsed.astimezone(dt.timezone.utc)


def month_bounds(start: dt.date, end: dt.date):
    cur = dt.date(start.year, start.month, 1)
    stop = dt.date(end.year, end.month, 1)
    while cur <= stop:
        nxt = dt.date(cur.year + (cur.month == 12), 1 if cur.month == 12 else cur.month + 1, 1)
        yield cur, max(start, cur), min(end, nxt - dt.timedelta(days=1))
        cur = nxt


def checkpoint_partition(day: dt.date) -> str:
    """Keep the separately processed final day distinct from the October chunk."""
    return day.isoformat() if day == dt.date(2026, 10, 27) else day.strftime('%Y-%m')


def expected_checkpoint_partitions() -> dict[str, tuple[str, str]]:
    partitions = {first.strftime('%Y-%m'): (start.isoformat(), end.isoformat())
                  for first, start, end in month_bounds(EXPECTED_START, dt.date(2026, 10, 26))}
    partitions['2026-10-27'] = ('2026-10-27', '2026-10-27')
    return partitions


def quantile(values: list[float], q: float) -> float | None:
    """Linearly interpolated quantile (same convention as common analytics tools)."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def archive_cadence(url: str) -> str:
    return 'monthly' if '/monthly/' in url else 'daily' if '/daily/' in url else 'other'


def load_event_file(path: Path, checks: list[dict]) -> list[dict]:
    events = []
    opener = gzip.open if path.suffix == '.gz' else open
    try:
        with opener(path, 'rt', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError('event row is not an object')
                    row['_report_file'] = str(path)
                    row['_report_line'] = line_no
                    events.append(row)
                except (json.JSONDecodeError, ValueError) as exc:
                    checks.append({'severity': 'error', 'code': 'invalid_event_json',
                                   'file': str(path), 'line': line_no, 'detail': str(exc)})
    except (OSError, EOFError) as exc:
        checks.append({'severity': 'error', 'code': 'unreadable_event_file',
                       'file': str(path), 'detail': str(exc)})
    return events


def validate_event(row: dict) -> list[str]:
    errors = []
    frame = row.get('timeframe')
    if frame not in FRAMES_MS:
        return ['unknown timeframe']
    try:
        opened, closed = parse_time(row.get('open_time')), parse_time(row.get('close_time'))
        duration = FRAMES_MS[frame]
        open_ms = int(opened.timestamp() * 1000)
        close_ms = int(closed.timestamp() * 1000)
        if close_ms - open_ms != duration:
            errors.append('close_time does not match timeframe duration')
        if open_ms % duration:
            errors.append('open_time is not timeframe-aligned')
    except (ValueError, OverflowError, OSError):
        errors.append('invalid open_time or close_time')
    nums = {}
    for field in ('open', 'high', 'low', 'close'):
        try:
            val = float(row[field])
            if not math.isfinite(val):
                raise ValueError()
            nums[field] = val
        except (KeyError, TypeError, ValueError):
            errors.append(f'{field} is missing or non-finite')
    volume_field = 'base_volume' if 'base_volume' in row else 'volume'
    try:
        nums[volume_field] = float(row[volume_field])
        if not math.isfinite(nums[volume_field]):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        errors.append('base_volume is missing or non-finite')
    if all(k in nums for k in ('open', 'high', 'low', 'close', volume_field)):
        if min(nums['open'], nums['high'], nums['low'], nums['close']) <= 0:
            errors.append('price is not positive')
        if nums['high'] < max(nums['open'], nums['close'], nums['low']) or nums['low'] > min(nums['open'], nums['close'], nums['high']):
            errors.append('OHLC bounds are inconsistent')
        if nums[volume_field] < 0:
            errors.append('volume is negative')
    if 'quote_volume' in row:
        try:
            quote_volume = float(row['quote_volume'])
            if not math.isfinite(quote_volume) or quote_volume < 0:
                raise ValueError()
        except (TypeError, ValueError):
            errors.append('quote_volume is invalid')
    else:
        errors.append('quote_volume is missing')
    change_field = 'change_pct' if 'change_pct' in row else 'return_pct'
    try:
        nums[change_field] = float(row[change_field])
        if not math.isfinite(nums[change_field]):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        errors.append('change_pct is missing or non-finite')
    if 'direction' in row and change_field in nums:
        expected = 'up' if nums[change_field] > 0 else 'down' if nums[change_field] < 0 else 'flat'
        if row['direction'] != expected:
            errors.append('direction disagrees with change_pct')
        if abs(nums[change_field]) <= 1:
            errors.append('change_pct does not cross the event threshold')
    if all(k in nums for k in ('open', 'close', change_field)) and nums['open'] > 0:
        calculated = (nums['close'] / nums['open'] - 1) * 100
        if not math.isclose(nums[change_field], calculated, rel_tol=1e-9, abs_tol=1e-9):
            errors.append('change_pct disagrees with close/open')
    if 'symbol' not in row or not isinstance(row.get('symbol'), str) or not row['symbol']:
        errors.append('symbol is missing')
    if not isinstance(row.get('event_id'), str) or not row.get('event_id'):
        errors.append('event_id is missing')
    if not isinstance(row.get('source_ids'), list):
        errors.append('source_ids is missing or is not a list')
    elif any(not isinstance(source_id, str) or len(source_id) != 64 or any(c not in '0123456789abcdef' for c in source_id) for source_id in row['source_ids']):
        errors.append('source_ids contains an invalid SHA256 identifier')
    if not isinstance(row.get('market'), str) or not row.get('market'):
        errors.append('market is missing')
    if row.get('data_quality') != 'complete':
        errors.append('data_quality is not complete')
    return errors


def build_report(output: Path, universe_path: Path | None = None,
                 requested_universe_size: int = REQUESTED_UNIVERSE_SIZE) -> tuple[dict, dict]:
    checks: list[dict] = []
    checkpoints: dict[tuple[str, str], dict] = {}
    checkpoint_paths = sorted((output / 'checkpoints').glob('*/*.json'))
    for path in checkpoint_paths:
        try:
            cp = read_json(path)
            symbol, month = cp.get('symbol'), cp.get('month')
            if not symbol or not month:
                raise ValueError('checkpoint lacks symbol or month')
            key = (symbol, month)
            if key in checkpoints:
                checks.append({'severity': 'error', 'code': 'duplicate_checkpoint', 'symbol': symbol, 'month': month})
            checkpoints[key] = cp
            if cp.get('version') != 1:
                checks.append({'severity': 'error', 'code': 'unsupported_checkpoint_version', 'file': str(path), 'version': cp.get('version')})
            if path.parent.name != symbol or path.stem != month:
                checks.append({'severity': 'error', 'code': 'checkpoint_path_mismatch', 'file': str(path), 'symbol': symbol, 'month': month})
            expected_dates = expected_checkpoint_partitions()
            if month not in expected_dates or (cp.get('start'), cp.get('end')) != expected_dates.get(month):
                checks.append({'severity': 'error', 'code': 'checkpoint_date_coverage_mismatch', 'symbol': symbol, 'month': month, 'start': cp.get('start'), 'end': cp.get('end')})
        except (OSError, json.JSONDecodeError, ValueError, AttributeError) as exc:
            checks.append({'severity': 'error', 'code': 'invalid_checkpoint', 'file': str(path), 'detail': str(exc)})

    expected_symbols = None
    universe_file = universe_path or (output / 'universe.json')
    if universe_file.exists():
        try:
            data = read_json(universe_file)
            items = data.get('symbols', []) if isinstance(data, dict) else data
            expected_symbols = sorted({x.get('symbol') if isinstance(x, dict) else x for x in items if (x.get('symbol') if isinstance(x, dict) else x)})
        except (OSError, json.JSONDecodeError, AttributeError, TypeError) as exc:
            checks.append({'severity': 'error', 'code': 'invalid_universe', 'file': str(universe_file), 'detail': str(exc)})
    else:
        checks.append({'severity': 'warning', 'code': 'universe_missing', 'file': str(universe_file)})

    if expected_symbols is not None:
        selected = set(expected_symbols)
        checkpoints = {key: cp for key, cp in checkpoints.items() if key[0] in selected}
        checkpoint_paths = [p for p in checkpoint_paths if p.parent.name in selected]
    symbols = expected_symbols or sorted({s for s, _ in checkpoints})
    expected_months = list(expected_checkpoint_partitions())
    missing_checkpoints = []
    for symbol in symbols:
        for month in expected_months:
            if (symbol, month) not in checkpoints:
                missing_checkpoints.append({'symbol': symbol, 'month': month})
    if missing_checkpoints:
        checks.append({'severity': 'warning', 'code': 'checkpoint_coverage_incomplete', 'missing': missing_checkpoints})

    observed_by_month = defaultdict(Counter)
    rows_by_month = defaultdict(list)
    all_events = []
    event_files = sorted((output / 'events').glob('*/*.jsonl.gz')) + sorted((output / 'events').glob('*/*.jsonl'))
    if expected_symbols is not None:
        event_files = [p for p in event_files if p.parent.name in set(expected_symbols)]
    for path in event_files:
        all_events.extend(load_event_file(path, checks))
    keys = set()
    event_id_keys = set()
    event_counts = Counter()
    for row in all_events:
        symbol, frame = row.get('symbol'), row.get('timeframe')
        if symbol and frame:
            event_counts[(symbol, frame)] += 1
        identity = (symbol, frame, row.get('open_time'))
        if identity in keys:
            checks.append({'severity': 'error', 'code': 'duplicate_event', 'symbol': symbol, 'timeframe': frame, 'open_time': row.get('open_time')})
        keys.add(identity)
        event_id = row.get('event_id')
        if event_id in event_id_keys:
            checks.append({'severity': 'error', 'code': 'duplicate_event_id', 'event_id': event_id})
        event_id_keys.add(event_id)
        errs = validate_event(row)
        if errs:
            checks.append({'severity': 'error', 'code': 'invalid_event', 'file': row.get('_report_file'), 'line': row.get('_report_line'), 'detail': errs})
        try:
            opened_date = parse_time(row['open_time']).date()
            partition = checkpoint_partition(opened_date)
            observed_by_month[(symbol, partition)][frame] += 1
            rows_by_month[(symbol, partition)].append(row)
        except (KeyError, ValueError):
            pass

    # Reconcile event payloads against checkpoint claims and files.
    for (symbol, month), cp in checkpoints.items():
        expected_path = output / cp.get('event_file', '')
        if not expected_path.is_file():
            checks.append({'severity': 'error', 'code': 'event_file_missing', 'symbol': symbol, 'month': month, 'file': cp.get('event_file')})
        claimed = cp.get('event_counts')
        if not isinstance(claimed, dict):
            checks.append({'severity': 'error', 'code': 'event_counts_missing', 'symbol': symbol, 'month': month})
            continue
        for frame in FRAMES_MS:
            actual = observed_by_month[(symbol, month)][frame]
            if claimed.get(frame) != actual:
                checks.append({'severity': 'error', 'code': 'event_count_mismatch', 'symbol': symbol, 'month': month, 'timeframe': frame, 'claimed': claimed.get(frame), 'observed': actual})
        q = cp.get('quality')
        if not isinstance(q, dict):
            checks.append({'severity': 'error', 'code': 'quality_missing', 'symbol': symbol, 'month': month})
        else:
            for field in ('expected_5m_bars', 'valid_5m_bars', 'missing_5m_bars', 'duplicate_rows', 'invalid_rows', 'out_of_range_rows', 'missing_archives', 'downloaded_bytes'):
                # The pipeline serializes Counter values sparsely, so omitted
                # row-quality counters mean zero; coverage/download counters
                # must always be present.
                optional_zero = field in ('duplicate_rows', 'invalid_rows', 'out_of_range_rows')
                value = q.get(field, 0 if optional_zero else None)
                if not isinstance(value, int) or value < 0:
                    checks.append({'severity': 'error', 'code': 'invalid_quality_counter', 'symbol': symbol, 'month': month, 'field': field, 'value': q.get(field)})
            if all(isinstance(q.get(k), int) for k in ('expected_5m_bars', 'valid_5m_bars', 'missing_5m_bars')) and q['valid_5m_bars'] + q['missing_5m_bars'] != q['expected_5m_bars']:
                checks.append({'severity': 'error', 'code': 'bar_coverage_arithmetic_mismatch', 'symbol': symbol, 'month': month, 'quality': {k: q[k] for k in ('expected_5m_bars', 'valid_5m_bars', 'missing_5m_bars')}})
            start, end = cp.get('start'), cp.get('end')
            try:
                days = (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days + 1
                calendar_expected = days * 288
                if q.get('expected_5m_bars') != calendar_expected:
                    checks.append({'severity': 'error', 'code': 'expected_bar_coverage_mismatch', 'symbol': symbol, 'month': month, 'expected_from_dates': calendar_expected, 'quality_expected': q.get('expected_5m_bars')})
            except (TypeError, ValueError):
                pass
            if isinstance(q.get('missing_5m_bars'), int) and q['missing_5m_bars'] > 0:
                checks.append({'severity': 'warning', 'code': 'incomplete_bar_coverage', 'symbol': symbol, 'month': month, 'missing_5m_bars': q['missing_5m_bars'], 'expected_5m_bars': q.get('expected_5m_bars')})
            if q.get('duplicate_rows', 0) > 0:
                checks.append({'severity': 'warning', 'code': 'duplicate_input_rows', 'symbol': symbol, 'month': month, 'duplicate_rows': q['duplicate_rows']})
            if q.get('invalid_rows', 0) > 0:
                checks.append({'severity': 'warning', 'code': 'invalid_input_rows', 'symbol': symbol, 'month': month, 'invalid_rows': q['invalid_rows']})
            sources = cp.get('sources')
            if not isinstance(sources, list) or not sources or any(s.get('status') not in ('available', 'missing', 'unavailable') for s in sources if isinstance(s, dict)) or any(not isinstance(s, dict) for s in sources):
                checks.append({'severity': 'error', 'code': 'invalid_source_status', 'symbol': symbol, 'month': month})
            elif sum(s['status'] == 'missing' for s in sources) != q.get('missing_archives'):
                checks.append({'severity': 'error', 'code': 'source_status_count_mismatch', 'symbol': symbol, 'month': month, 'quality_missing_archives': q.get('missing_archives'), 'observed_missing_sources': sum(s['status'] == 'missing' for s in sources)})
            elif any(not isinstance(s.get('url'), str) or s.get('source_id') != hashlib.sha256(s['url'].encode()).hexdigest() or not isinstance(s.get('bytes'), int) or s['bytes'] < 0 for s in sources):
                checks.append({'severity': 'error', 'code': 'invalid_source_record', 'symbol': symbol, 'month': month})
            elif sum(s['status'] == 'unavailable' for s in sources) != q.get('unavailable_archives', 0):
                checks.append({'severity': 'error', 'code': 'source_unavailable_count_mismatch', 'symbol': symbol, 'month': month})
            if (isinstance(sources, list)
                    and all(isinstance(s, dict) and isinstance(s.get('bytes'), int) and s['bytes'] >= 0 for s in sources)
                    and isinstance(q.get('downloaded_bytes'), int)):
                source_bytes = sum(s['bytes'] for s in sources)
                quality_bytes = q['downloaded_bytes']
                if quality_bytes < source_bytes:
                    checks.append({'severity': 'error', 'code': 'downloaded_bytes_mismatch', 'symbol': symbol, 'month': month,
                                   'quality_downloaded_bytes': quality_bytes, 'source_downloaded_bytes': source_bytes})
                elif quality_bytes > source_bytes:
                    checks.append({'severity': 'warning', 'code': 'untracked_downloaded_bytes', 'symbol': symbol, 'month': month,
                                   'untracked_bytes': quality_bytes - source_bytes,
                                   'detail': 'Checkpoint quality records transferred bytes absent from its source ledger.'})
            if isinstance(q.get('incomplete_buckets'), dict):
                claimed_incomplete = q['incomplete_buckets']
                for frame, n in FRAMES_MS.items():
                    expected_buckets = q.get('expected_5m_bars', 0) * 300_000 // n
                    complete = q.get('complete_buckets', {}).get(frame) if isinstance(q.get('complete_buckets'), dict) else None
                    incomplete = claimed_incomplete.get(frame)
                    if not isinstance(complete, int) or not isinstance(incomplete, int) or complete + incomplete != expected_buckets:
                        checks.append({'severity': 'error', 'code': 'bucket_coverage_mismatch', 'symbol': symbol, 'month': month, 'timeframe': frame, 'complete': complete, 'incomplete': incomplete, 'expected': expected_buckets})
            available_source_ids = {s.get('source_id') for s in sources if isinstance(s, dict) and s.get('status') == 'available'} if isinstance(sources, list) else set()
            for row in rows_by_month[(symbol, month)]:
                refs = row.get('source_ids')
                if isinstance(refs, list) and (not refs or any(ref not in available_source_ids for ref in refs)):
                    checks.append({'severity': 'error', 'code': 'event_source_reference_mismatch', 'symbol': symbol, 'month': month, 'event_id': row.get('event_id')})
            ranges = q.get('missing_5m_ranges')
            if not isinstance(ranges, list):
                checks.append({'severity': 'error', 'code': 'missing_bar_ranges_invalid', 'symbol': symbol, 'month': month})
            else:
                try:
                    intervals = [(parse_time(item['start']), parse_time(item['end'])) for item in ranges]
                    start_dt = dt.datetime.combine(dt.date.fromisoformat(start), dt.time.min, dt.timezone.utc)
                    end_dt = dt.datetime.combine(dt.date.fromisoformat(end) + dt.timedelta(days=1), dt.time.min, dt.timezone.utc)
                    total_missing = 0
                    prev_end = start_dt
                    for a, b in intervals:
                        seconds = (b - a).total_seconds()
                        if a < start_dt or b > end_dt or b <= a or seconds % 300 or a < prev_end:
                            raise ValueError('range out of bounds, overlapping, or misaligned')
                        total_missing += int(seconds // 300)
                        prev_end = b
                    if total_missing != q.get('missing_5m_bars'):
                        raise ValueError('range total disagrees with missing_5m_bars')
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    checks.append({'severity': 'error', 'code': 'missing_bar_ranges_invalid', 'symbol': symbol, 'month': month, 'detail': str(exc)})

    manifest_path = output / 'source_manifest.json.gz'
    manifest_summary = {'status': 'missing', 'sources': None, 'events': None,
                        'remote_sources': None}
    if manifest_path.exists():
        try:
            with gzip.open(manifest_path, 'rt', encoding='utf-8') as f:
                manifest = json.load(f)
            sources, windows = manifest.get('sources'), manifest.get('analysis_windows')
            if not isinstance(sources, list) or not isinstance(windows, list):
                raise ValueError('manifest must have sources and merged analysis_windows')
            ids, paths = set(), set()
            source_by_id = {}
            statuses = Counter()
            for src in sources:
                if not isinstance(src, dict) or not isinstance(src.get('source_id'), str) or not isinstance(src.get('url'), str):
                    checks.append({'severity': 'error', 'code': 'invalid_manifest_source'}); continue
                sid, url = src['source_id'], src['url']
                if sid in ids or url in paths:
                    checks.append({'severity': 'error', 'code': 'duplicate_manifest_source', 'source_id': sid})
                ids.add(sid); paths.add(url)
                source_by_id[sid] = src
                if sid != hashlib.sha256(url.encode()).hexdigest():
                    checks.append({'severity': 'error', 'code': 'manifest_source_id_mismatch', 'url': url})
                status = src.get('status')
                if status not in ('verified', 'inferred', 'missing'):
                    checks.append({'severity': 'error', 'code': 'invalid_manifest_source_status', 'url': url, 'status': status})
                statuses[status] += 1
            observed_by_id = {e.get('event_id'): e for e in all_events}
            covered, previous_end = set(), {}
            for window in sorted(windows, key=lambda w: (w.get('symbol',''), w.get('start_utc',''))):
                try:
                    symbol = window['symbol']
                    a, b = parse_time(window['start_utc']), parse_time(window['end_utc'])
                    if b <= a or (symbol in previous_end and a <= previous_end[symbol]):
                        raise ValueError('invalid or overlapping/adjacent unmerged window')
                    previous_end[symbol] = b
                    refs, event_refs = window['source_ids'], window['event_ids']
                    if not isinstance(refs, list) or any(x not in ids for x in refs):
                        raise ValueError('unknown source reference')
                    if any(source_by_id[x].get('symbol') != symbol for x in refs):
                        raise ValueError('window references source from another symbol')
                    if not isinstance(event_refs, list):
                        raise ValueError('invalid event references')
                    for event_id in event_refs:
                        row = observed_by_id.get(event_id)
                        if row is None or event_id in covered or row.get('symbol') != symbol:
                            raise ValueError('unknown, duplicate or wrong-symbol event reference')
                        opened, closed = parse_time(row['open_time']), parse_time(row['close_time'])
                        duration = dt.timedelta(milliseconds=FRAMES_MS[row['timeframe']])
                        if a > opened - 250 * duration or b < closed:
                            raise ValueError('window does not contain required 250-candle lookback')
                        if not set(row.get('source_ids', [])).issubset(refs):
                            raise ValueError('window lacks event acquisition source')
                        covered.add(event_id)
                except (KeyError, TypeError, ValueError, OverflowError) as exc:
                    checks.append({'severity': 'error', 'code': 'invalid_manifest_window', 'detail': str(exc)})
            if covered != set(observed_by_id):
                checks.append({'severity': 'error', 'code': 'manifest_event_coverage_mismatch', 'missing': len(set(observed_by_id)-covered), 'extra': len(covered-set(observed_by_id))})
            manifest_summary = {'status': 'valid' if not any(c['severity'] == 'error' and c.get('code', '').startswith(('manifest_', 'duplicate_manifest', 'invalid_manifest')) for c in checks) else 'invalid',
                                'sources': len(sources), 'events': len(covered), 'windows': len(windows),
                                'remote_sources': dict(statuses)}
        except (OSError, EOFError, json.JSONDecodeError, ValueError, AttributeError) as exc:
            checks.append({'severity': 'error', 'code': 'invalid_manifest', 'file': str(manifest_path), 'detail': str(exc)})
            manifest_summary = {'status': 'invalid', 'sources': None, 'events': None, 'remote_sources': None}
    else:
        checks.append({'severity': 'error', 'code': 'manifest_missing', 'file': str(manifest_path)})

    requested_symbols = len(expected_symbols) if expected_symbols is not None else requested_universe_size
    observed_symbols = sorted({s for s, _ in checkpoints})
    expected_month_count = len(expected_months)
    requested_symbol_months = requested_symbols * expected_month_count
    complete_symbols = [s for s in observed_symbols if all((s, month) in checkpoints and checkpoints[(s, month)].get('quality', {}).get('missing_5m_bars') == 0 for month in expected_months)]

    # Availability of a third-party file is not official validation. Each
    # checkpoint is classified independently, including legacy official data
    # whose CSV close timestamps were not checked by the original processor.
    provenance_counts = Counter()
    provenance_months = []
    pilot_checks = {}
    pilot_path = output / 'pilot_close_time_validation.json'
    if pilot_path.exists():
        try:
            pilot_checks = json.loads(pilot_path.read_text())
        except (OSError, ValueError):
            checks.append({'severity':'warning', 'code':'invalid_pilot_close_time_validation', 'file':str(pilot_path)})
    for (symbol, month), cp in sorted(checkpoints.items()):
        kind = cp.get('selected_source', 'binance')
        declared = cp.get('verification_status')
        if kind == 'huggingface':
            verification = 'third_party_cross_validated' if declared in ('third_party_cross_validated', 'third_party_cross_validated_against_binance') else 'third_party_pending_official_validation'
        elif kind == 'no_data':
            verification = 'no_data'
        else:
            verification = 'official_archive'
        close_check = cp.get('close_time_validation', {}).get('status', 'not_recorded') if isinstance(cp.get('close_time_validation', {}), dict) else 'not_recorded'
        pilot_check = pilot_checks.get(f'{symbol}/{month}', {})
        if close_check != 'verified' and isinstance(pilot_check, dict) and pilot_check.get('status') == 'verified' and pilot_check.get('invalid_count') == 0 and pilot_check.get('missing') == 0 and pilot_check.get('rows') == cp.get('quality', {}).get('expected_5m_bars'):
            close_check = 'verified'
        provenance_counts[verification] += 1
        provenance_months.append({'symbol': symbol, 'month': month, 'source': kind,
                                  'verification_status': verification,
                                  'close_timestamp_validation': close_check,
                                  'close_timestamp_validation_source': str(pilot_path) if isinstance(pilot_check, dict) and pilot_check.get('status') == 'verified' else 'checkpoint',
                                  'missing_5m_bars': cp.get('quality', {}).get('missing_5m_bars'),
                                  'sources': [s for s in cp.get('sources', []) if s.get('status') == 'available']})
    pending_close_validation = sum(x['close_timestamp_validation'] not in ('verified', 'third_party_source_only') for x in provenance_months)
    if pending_close_validation:
        checks.append({'severity': 'warning', 'code': 'close_timestamp_validation_pending', 'symbol_months': pending_close_validation})
    if provenance_counts['third_party_pending_official_validation']:
        checks.append({'severity': 'warning', 'code': 'third_party_official_validation_pending', 'symbol_months': provenance_counts['third_party_pending_official_validation']})

    # Complete candle aggregates come from the pipeline's explicit complete and
    # incomplete bucket counters, reconciled above against the calendar window.
    candle_counts = defaultdict(Counter)
    for (symbol, _month), cp in checkpoints.items():
        q = cp.get('quality', {})
        if not isinstance(q, dict):
            continue
        for frame in FRAMES_MS:
            complete = q.get('complete_buckets', {}).get(frame, 0) if isinstance(q.get('complete_buckets'), dict) else 0
            incomplete = q.get('incomplete_buckets', {}).get(frame, 0) if isinstance(q.get('incomplete_buckets'), dict) else 0
            candle_counts[(symbol, frame)]['complete'] += complete if isinstance(complete, int) else 0
            candle_counts[(symbol, frame)]['incomplete'] += incomplete if isinstance(incomplete, int) else 0
            candle_counts[(symbol, frame)]['expected'] += (q.get('expected_5m_bars', 0) * 300_000 // FRAMES_MS[frame]) if isinstance(q.get('expected_5m_bars'), int) else 0

    duration_ms = int((dt.datetime.combine(EXPECTED_END + dt.timedelta(days=1), dt.time.min, dt.timezone.utc) -
                       dt.datetime.combine(EXPECTED_START, dt.time.min, dt.timezone.utc)).total_seconds() * 1000)
    candles_by_timeframe = {}
    for frame, frame_ms in FRAMES_MS.items():
        complete = sum(candle_counts[(symbol, frame)]['complete'] for symbol in observed_symbols)
        incomplete = sum(candle_counts[(symbol, frame)]['incomplete'] for symbol in observed_symbols)
        observed_expected = sum(candle_counts[(symbol, frame)]['expected'] for symbol in observed_symbols)
        requested_expected = duration_ms // frame_ms * requested_symbols
        candles_by_timeframe[frame] = {
            'validated_complete': complete,
            'incomplete': incomplete,
            'expected_for_processed_symbols': observed_expected,
            'coverage_for_processed_symbols': complete / observed_expected if observed_expected else None,
            'expected_for_requested_universe': requested_expected,
            'coverage_for_requested_universe': complete / requested_expected if requested_expected else None,
        }

    monthly = defaultdict(lambda: {'total': 0, 'by_timeframe': Counter(), 'by_symbol': Counter(), 'up': 0, 'down': 0})
    directions = Counter()
    abs_moves = []
    moves_by_symbol = defaultdict(list)
    per_symbol_event_counts = defaultdict(Counter)
    per_symbol_directions = defaultdict(Counter)
    event_rows = []
    for row in all_events:
        try:
            month = parse_time(row['open_time']).strftime('%Y-%m')
            change = float(row['change_pct'] if 'change_pct' in row else row['return_pct'])
        except (KeyError, TypeError, ValueError):
            continue
        symbol, frame = row.get('symbol'), row.get('timeframe')
        direction = row.get('direction')
        bucket = monthly[month]
        bucket['total'] += 1
        bucket['by_timeframe'][frame] += 1
        bucket['by_symbol'][symbol] += 1
        directions[direction] += 1
        if direction in ('up', 'down'):
            bucket[direction] += 1
        magnitude = abs(change)
        abs_moves.append(magnitude)
        moves_by_symbol[symbol].append(magnitude)
        per_symbol_event_counts[symbol][frame] += 1
        if direction in ('up', 'down'):
            per_symbol_directions[symbol][direction] += 1
        event_rows.append({'event_id': row.get('event_id'), 'symbol': symbol, 'timeframe': frame,
                           'open_time': row.get('open_time'), 'direction': direction, 'change_pct': change})

    movement_bins = []
    for low, high in MOVEMENT_BINS:
        label = f'>{low:g}% to {high:g}%' if math.isfinite(high) else f'>{low:g}%'
        count = sum(low < value <= high for value in abs_moves)
        movement_bins.append({'range': label, 'events': count})
    movement_distribution = {
        'population': 'absolute event change_pct; events only (thresholded stream)',
        'events': len(abs_moves), 'bins': movement_bins,
        'mean_abs_pct': statistics.fmean(abs_moves) if abs_moves else None,
        'median_abs_pct': quantile(abs_moves, .5), 'p90_abs_pct': quantile(abs_moves, .9),
        'p95_abs_pct': quantile(abs_moves, .95), 'max_abs_pct': max(abs_moves) if abs_moves else None,
    }
    largest_moves = sorted(event_rows, key=lambda row: abs(row['change_pct']), reverse=True)[:20]
    monthly_events = {month: {'total': values['total'], 'by_timeframe': {frame: values['by_timeframe'][frame] for frame in FRAMES_MS},
                              'by_symbol': dict(sorted(values['by_symbol'].items())), 'up': values['up'], 'down': values['down']}
                      for month, values in sorted(monthly.items())}

    per_symbol_totals = {}
    for symbol in symbols:
        per_symbol_totals[symbol] = {
            'total_events': sum(per_symbol_event_counts[symbol].values()),
            'events_by_timeframe': {frame: per_symbol_event_counts[symbol][frame] for frame in FRAMES_MS},
            'up_events': per_symbol_directions[symbol]['up'],
            'down_events': per_symbol_directions[symbol]['down'],
            'mean_abs_change_pct': statistics.fmean(moves_by_symbol[symbol]) if moves_by_symbol[symbol] else None,
            'median_abs_change_pct': quantile(moves_by_symbol[symbol], .5),
            'largest_abs_change_pct': max(moves_by_symbol[symbol]) if moves_by_symbol[symbol] else None,
            'candles_by_timeframe': {frame: dict(candle_counts[(symbol, frame)]) for frame in FRAMES_MS},
            'complete_months': sum((symbol, month) in checkpoints for month in expected_months),
        }

    archive_attempts = Counter()
    archive_by_cadence = defaultdict(Counter)
    archive_download_bytes = 0
    untracked_download_bytes = 0
    for cp in checkpoints.values():
        source_bytes = 0
        for source in cp.get('sources', []):
            if not isinstance(source, dict):
                continue
            cadence = archive_cadence(str(source.get('url', '')))
            archive_attempts[source.get('status', 'unknown')] += 1
            archive_by_cadence[cadence][source.get('status', 'unknown')] += 1
            source_bytes += source.get('bytes', 0) if isinstance(source.get('bytes', 0), int) else 0
        quality = cp.get('quality')
        quality_bytes = quality.get('downloaded_bytes') if isinstance(quality, dict) else None
        if isinstance(quality_bytes, int) and quality_bytes >= 0:
            archive_download_bytes += quality_bytes
            untracked_download_bytes += max(quality_bytes - source_bytes, 0)
        else:
            archive_download_bytes += source_bytes
    archive_coverage = {
        'pipeline_download_attempts': {'attempted': archive_attempts['available'] + archive_attempts['missing'] + archive_attempts['unavailable'],
                                       'unavailable': archive_attempts['unavailable'],
                                       'available': archive_attempts['available'], 'missing': archive_attempts['missing'],
                                       'available_fraction': archive_attempts['available'] / (archive_attempts['available'] + archive_attempts['missing'] + archive_attempts['unavailable']) if archive_attempts['available'] + archive_attempts['missing'] + archive_attempts['unavailable'] else None,
                                       'by_cadence': {cadence: dict(counts) for cadence, counts in sorted(archive_by_cadence.items())}},
        'manifest_remote_sources': manifest_summary.get('remote_sources'),
        'untracked_download_bytes': untracked_download_bytes,
        'note': 'Downloaded bytes use checkpoint quality totals; positive differences from source-ledger bytes are untracked transfers. Pipeline acquisition and manifest remote source statuses are distinct. Inferred paths have not been confirmed accessible.'
    }

    artifact_paths = list(checkpoint_paths) + event_files + ([manifest_path] if manifest_path.is_file() else [])
    artifact_bytes = sum(path.stat().st_size for path in artifact_paths if path.is_file())
    resource_stats = {'downloaded_archive_bytes': archive_download_bytes,
                      'generated_artifact_bytes': artifact_bytes,
                      'runtime_seconds': None, 'peak_memory_bytes': None,
                      'runtime_and_memory_source': 'not recorded in available run artifacts'}

    errors = sum(c['severity'] == 'error' for c in checks)
    warnings = sum(c['severity'] == 'warning' for c in checks)
    status = 'failed' if errors else 'incomplete' if warnings and (expected_symbols is None or missing_checkpoints) else 'passed_with_warnings' if warnings else 'passed'
    quality_totals = Counter()
    for cp in checkpoints.values():
        if isinstance(cp.get('quality'), dict):
            q = cp['quality']
            for field in ('duplicate_rows', 'invalid_rows', 'out_of_range_rows'):
                quality_totals[field] += q.get(field, 0)
            for field, value in q.items():
                if field not in ('duplicate_rows', 'invalid_rows', 'out_of_range_rows') and isinstance(value, int):
                    quality_totals[field] += value
    summary = {
        'schema_version': 2,
        'status': status,
        'scope': {'start': EXPECTED_START.isoformat(), 'end': EXPECTED_END.isoformat()},
        'inputs': {'universe': str(universe_file) if universe_file.exists() else None,
                   'symbols_expected': len(expected_symbols) if expected_symbols is not None else None,
                   'requested_universe_size': requested_symbols,
                   'symbols_with_checkpoints': len({s for s, _ in checkpoints}),
                   'symbols_fully_processed': len(complete_symbols),
                   'checkpoints': len(checkpoints), 'expected_symbol_months': requested_symbol_months,
                   'missing_checkpoints': max(requested_symbol_months - len(checkpoints), 0),
                   'known_missing_symbol_months': len(missing_checkpoints), 'event_files': len(event_files),
                   'events': len(all_events), 'manifest': manifest_summary},
        'event_counts': {symbol: {frame: event_counts[(symbol, frame)] for frame in FRAMES_MS} for symbol in symbols},
        'per_symbol_totals': per_symbol_totals,
        'monthly_events': monthly_events,
        'bull_bear_counts': {'bull_up_events': directions['up'], 'bear_down_events': directions['down'],
                             'other_direction_events': sum(v for k, v in directions.items() if k not in ('up', 'down')),
                             'by_timeframe': {frame: {
                                 'bull_up': sum(1 for row in event_rows if row['timeframe'] == frame and row['direction'] == 'up'),
                                 'bear_down': sum(1 for row in event_rows if row['timeframe'] == frame and row['direction'] == 'down')}
                                 for frame in FRAMES_MS}},
        'movement_distribution': movement_distribution,
        'largest_moves': largest_moves,
        'validated_candle_counts_by_timeframe': candles_by_timeframe,
        'quality_totals': dict(sorted(quality_totals.items())),
        'archive_coverage': archive_coverage,
        'source_provenance': {'symbol_month_counts': dict(provenance_counts), 'symbol_months': provenance_months,
                              'note': 'Official archive origin and independent cross-validation are distinct from completeness and close-timestamp validation.'},
        'resource_stats': resource_stats,
        'coverage': {
            'universe_symbols': {'processed': len(observed_symbols), 'fully_processed': len(complete_symbols),
                                 'requested': requested_symbols, 'fraction': len(complete_symbols) / requested_symbols if requested_symbols else None,
                                 'universe_source': 'universe.json' if expected_symbols is not None else 'requested-universe-size fallback'},
            'symbol_month_checkpoints': {'available': len(checkpoints), 'expected': requested_symbol_months,
                                         'fraction': len(checkpoints) / requested_symbol_months if requested_symbol_months else None},
            'five_minute_bars': {'expected_for_processed_symbols': quality_totals.get('expected_5m_bars', 0),
                                 'valid_for_processed_symbols': quality_totals.get('valid_5m_bars', 0),
                                 'missing_for_processed_symbols': quality_totals.get('missing_5m_bars', 0),
                                 'fraction_for_processed_symbols': (quality_totals.get('valid_5m_bars', 0) / quality_totals.get('expected_5m_bars')) if quality_totals.get('expected_5m_bars') else None,
                                 'expected_for_requested_universe': candles_by_timeframe['5m']['expected_for_requested_universe'],
                                 'valid_for_requested_universe': quality_totals.get('valid_5m_bars', 0),
                                 'fraction_for_requested_universe': candles_by_timeframe['5m']['coverage_for_requested_universe']},
        },
        'validation': {'errors': errors, 'warnings': warnings, 'checks': len(checks)},
    }
    validation = {'status': status, 'errors': errors, 'warnings': warnings, 'checks': checks}
    return summary, validation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output'), help='pipeline output directory')
    parser.add_argument('--universe', type=Path, help='universe JSON (defaults to OUTPUT/universe.json)')
    parser.add_argument('--requested-universe-size', type=int, default=REQUESTED_UNIVERSE_SIZE,
                        help='number of requested symbols when universe JSON is unavailable (default: 100)')
    parser.add_argument('--summary', type=Path, help='summary path (defaults to OUTPUT/summary.json)')
    parser.add_argument('--validation', type=Path, help='validation path (defaults to OUTPUT/validation.json)')
    args = parser.parse_args()
    if args.requested_universe_size < 1:
        parser.error('--requested-universe-size must be positive')
    summary, validation = build_report(args.output, args.universe, args.requested_universe_size)
    summary_path = args.summary or args.output / 'summary.json'
    validation_path = args.validation or args.output / 'validation.json'
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    validation_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    validation_path.write_text(json.dumps(validation, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    print(f"{summary['status']}: {summary['inputs']['events']} events; {validation['errors']} errors, {validation['warnings']} warnings")
    return 0 if validation['errors'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
