"""Bounded, revision-pinned Hugging Face Parquet kline fallback.

Parquet is third-party data. Availability does not imply Binance verification.
An archive is downloaded once to a temporary file and removed on context exit.
Rows returned are Binance-compatible CSV string fields, with actual timestamps.
"""
from __future__ import annotations
import contextlib
import hashlib
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REVISION = '0fac89cbb949a9020c55520350d486ddc6c18cfa'
BASE = 'https://huggingface.co/datasets/linxy/USDT-M_Perpetual_Futures/resolve'
MAX_BYTES = 2 * 1024**3
COLUMNS = ['open_time','open','high','low','close','volume','close_time','quote_volume','count','taker_buy_volume','taker_buy_quote_volume','ignore']

class HFArchive:
    def __init__(self, path, source):
        import pyarrow.parquet as pq
        import pyarrow as pa
        self.path, self.source = path, source
        self.parquet = pq.ParquetFile(path)
        schema = self.parquet.schema_arrow
        missing = set(COLUMNS[:8]) - set(schema.names)
        if missing:
            raise ValueError(f'Parquet missing required fields: {sorted(missing)}')
        for col in ('open_time', 'close_time'):
            typ = schema.field(col).type
            if not pa.types.is_timestamp(typ) or typ.unit not in ('s', 'ms', 'us', 'ns'):
                raise ValueError(f'Unsupported timestamp type for {col}: {typ}')
            if typ.tz not in (None, 'UTC', 'Etc/UTC'):
                raise ValueError(f'Unexpected timestamp timezone: {typ.tz}')

    def iter_rows(self, start_ms, end_ms):
        """Yield original rows in [start_ms, end_ms), preserving source timestamps.

        Naive HF timestamps are interpreted as UTC. Timestamps finer than a
        microsecond are rejected rather than silently rounded. Returns timestamps
        at millisecond precision where possible, otherwise microseconds.
        """
        import pyarrow as pa
        import pyarrow.compute as pc
        columns = [c for c in COLUMNS if c in self.parquet.schema_arrow.names]
        types = {k: self.parquet.schema_arrow.field(k).type.unit for k in ('open_time','close_time')}
        scale = {'s': 10**9, 'ms': 10**6, 'us': 1000, 'ns': 1}
        for batch in self.parquet.iter_batches(batch_size=8192, columns=columns, use_threads=False):
            times = pc.cast(batch.column(columns.index('open_time')), pa.int64()).to_pylist()
            closes = pc.cast(batch.column(columns.index('close_time')), pa.int64()).to_pylist()
            data = {c: batch.column(columns.index(c)).to_pylist() for c in columns if c not in ('open_time', 'close_time')}
            for i, (ot, ct) in enumerate(zip(times, closes)):
                if ot is None or ct is None: raise ValueError('Null source timestamp')
                ot_ns, ct_ns = ot * scale[types['open_time']], ct * scale[types['close_time']]
                if not start_ms * 10**6 <= ot_ns < end_ms * 10**6: continue
                if ot_ns % 1000 or ct_ns % 1000: raise ValueError('Source timestamp has sub-microsecond precision')
                divisor = 10**6 if ot_ns % 10**6 == 0 and ct_ns % 10**6 == 0 else 1000
                fields = []
                for name in COLUMNS:
                    if name == 'open_time': value = ot_ns // divisor
                    elif name == 'close_time': value = ct_ns // divisor
                    else: value = data[name][i] if name in data else ''
                    if value is None: raise ValueError(f'Null {name}')
                    fields.append(str(value))
                yield fields

@contextlib.contextmanager
def open_archive(symbol, interval, revision=REVISION, max_bytes=MAX_BYTES, attempts=3):
    """Download a single pinned Parquet file; close and delete on exit."""
    if not re.fullmatch(r'[A-Z0-9]+USDT', symbol) or interval not in ('1d','5m'):
        raise ValueError('Invalid symbol or interval')
    if not re.fullmatch(r'[0-9a-f]{40}', revision): raise ValueError('Revision must be immutable SHA')
    url = f'{BASE}/{revision}/{symbol}/{symbol}_{interval}.parquet'
    with tempfile.TemporaryDirectory(prefix='hf-kline-') as temp:
        path = Path(temp)/'archive.parquet'
        for attempt in range(attempts):
            try:
                req = urllib.request.Request(url, headers={'User-Agent':'binance-study/1.0'})
                count = 0
                with urllib.request.urlopen(req, timeout=90) as src, path.open('wb') as dst:
                    length = src.headers.get('Content-Length')
                    if length and int(length) > max_bytes: raise ValueError('HF archive exceeds temporary disk limit')
                    while True:
                        chunk = src.read(1024*1024)
                        if not chunk: break
                        count += len(chunk)
                        if count > max_bytes: raise ValueError('HF archive exceeds temporary disk limit')
                        dst.write(chunk)
                break
            except (urllib.error.URLError, OSError, TimeoutError):
                path.unlink(missing_ok=True)
                if attempt + 1 >= attempts: raise
                time.sleep(2**attempt)
        source = {'url':url, 'source_id':hashlib.sha256(url.encode()).hexdigest(),
                  'status':'available', 'source_type':'hf_parquet',
                  'dataset_revision':revision, 'bytes':count,
                  'verification':'third_party_pending_official_verification'}
        yield HFArchive(path, source)
