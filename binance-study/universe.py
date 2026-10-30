#!/usr/bin/env python3
"""Rank a Hugging Face candidate snapshot using official Binance public archives.

The current dataset snapshot is a discovery source and may omit delisted contracts.
No direct exchange API or Binance S3 listing is used by the execution path.
"""

from __future__ import annotations

import concurrent.futures
import csv
import json
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path


BUCKET = "https://data.binance.vision/"
PREFIX = "data/futures/um/monthly/klines/"
MONTH = "2026-03"
OUTPUT = Path(__file__).resolve().parent / "output" / "universe.json"
PROGRESS = OUTPUT.with_name(".universe-progress.json")
SYMBOL_CACHE = OUTPUT.with_name(".universe-candidates.json")
TOP_N = 100
SYMBOL_RE = re.compile(r"^[A-Z0-9]+USDT$")
MONTH_START_MS = 1772409600000  # 2026-03-02T00:00:00Z (30 days before April 1)
MONTH_END_MS = 1775001600000    # 2026-04-01T00:00:00Z
USER_AGENT = "binance-study-universe/1.0"


def request(url: str, method: str = "GET", timeout: int = 40) -> urllib.request.urlopen:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=timeout)


def list_symbols() -> list[str]:
    """Enumerate symbol folders from S3 ListObjectsV2, following every page."""
    symbols: set[str] = set()
    token: str | None = None
    while True:
        params = {"list-type": "2", "prefix": PREFIX, "delimiter": "/"}
        params["_cache_bust"] = str(time.time_ns())
        if token:
            params["continuation-token"] = token
        url = BUCKET + "?" + urllib.parse.urlencode(params)
        for attempt in range(8):
            try:
                with request(url) as response:
                    root = ET.fromstring(response.read())
                break
            except urllib.error.HTTPError as exc:
                if exc.code < 500 and exc.code not in (408, 429):
                    raise
                if attempt == 7:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == 7:
                    raise
            time.sleep(min(30, 2 * (attempt + 1)))
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        for node in root.findall("s3:CommonPrefixes/s3:Prefix", ns):
            folder = node.text or ""
            symbol = folder[len(PREFIX) :].strip("/")
            if SYMBOL_RE.fullmatch(symbol):
                symbols.add(symbol)
        truncated = root.findtext("s3:IsTruncated", default="false", namespaces=ns)
        token = root.findtext("s3:NextContinuationToken", namespaces=ns)
        if truncated.lower() != "true":
            break
        if not token:
            raise RuntimeError("S3 marked a listing page truncated without a continuation token")
    result = sorted(symbols)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    SYMBOL_CACHE.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def archive_key(symbol: str) -> str:
    return f"{PREFIX}{symbol}/1d/{symbol}-1d-{MONTH}.zip"


def archive_exists(symbol: str) -> bool:
    """Use S3 object metadata as the authoritative archive listing check."""
    url = BUCKET + urllib.parse.quote(archive_key(symbol), safe="/")
    for attempt in range(6):
        try:
            with request(url, method="HEAD", timeout=20) as response:
                return response.status == 200
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            if exc.code < 500 and exc.code not in (408, 429):
                raise
            if attempt == 5:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 5:
                raise
        time.sleep(min(8, 0.5 * (2**attempt)))
    return False


def quote_volume_from_archive(symbol: str) -> dict[str, object]:
    """Download one monthly archive at a time and validate its daily quote-volume rows."""
    key = urllib.parse.quote(archive_key(symbol), safe="/")
    # The regional S3 endpoint serves the same Binance public-data bucket and
    # provides a useful route when the canonical data.binance.vision edge is
    # temporarily returning 5xx responses.
    urls = [
        BUCKET + key,
        "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/" + key,
    ]
    last_error: Exception | None = None
    for source_url in urls:
        for attempt in range(3):
            try:
                response_context = request(source_url, timeout=40)
                with response_context as response, tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        tmp.write(chunk)
                    tmp.flush()
                    with zipfile.ZipFile(tmp.name) as zf:
                        csv_names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
                        if len(csv_names) != 1:
                            raise ValueError(f"expected one CSV in {archive_key(symbol)}, found {csv_names}")
                        total = Decimal(0)
                        observed_open_times: set[int] = set()
                        duplicate_rows = 0
                        invalid_rows = 0
                        with zf.open(csv_names[0]) as raw:
                            for row in csv.reader(line.decode("utf-8") for line in raw):
                                if len(row) < 8:
                                    invalid_rows += 1
                                    continue
                                try:
                                    open_time = int(row[0])
                                    quote_volume = Decimal(row[7])
                                except (ValueError, InvalidOperation):
                                    # Some archive revisions include a CSV header.
                                    continue
                                if MONTH_START_MS <= open_time < MONTH_END_MS:
                                    if open_time % 86_400_000 != 0 or not quote_volume.is_finite() or quote_volume < 0:
                                        invalid_rows += 1
                                        continue
                                    if open_time in observed_open_times:
                                        duplicate_rows += 1
                                        continue
                                    observed_open_times.add(open_time)
                                    total += quote_volume
                        if not observed_open_times:
                            raise ValueError(f"no March 2026 daily rows in {archive_key(symbol)}")
                        days = len(observed_open_times)
                        return {
                            "volume": str(total),
                            "observed_days": days,
                            "expected_days": 30,
                            "duplicate_rows": duplicate_rows,
                            "invalid_rows": invalid_rows,
                            "partial_period": days < 30,
                        }
            except urllib.error.HTTPError as exc:
                last_error = exc
                # 404 is definitive for one endpoint; check the second endpoint
                # before declaring the archive unavailable.
                if exc.code == 404:
                    break
                if exc.code < 500 and exc.code not in (408, 429):
                    raise
            except (urllib.error.URLError, TimeoutError, zipfile.BadZipFile) as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
    if last_error:
        raise last_error
    raise RuntimeError(f"could not read {archive_key(symbol)}")


def quote_volume_from_hf(symbol: str) -> dict[str, object]:
    import hf_source
    total = Decimal(0)
    times = set()
    duplicates = invalid = 0
    with hf_source.open_archive(symbol, "1d") as archive:
        for row in archive.iter_rows(MONTH_START_MS, MONTH_END_MS):
            try:
                t = int(row[0]); q = Decimal(row[7]); close_time = int(row[6])
                if t >= 10**14:
                    if t % 1000 or close_time != t + 86400000000 - 1000:
                        invalid += 1
                        continue
                    t //= 1000; close_time //= 1000
                if (t < MONTH_START_MS or t >= MONTH_END_MS or t % 86400000
                    or close_time != t + 86400000 - 1 or not q.is_finite() or q < 0):
                    invalid += 1
                    continue
                if t in times:
                    duplicates += 1
                    continue
                times.add(t); total += q
            except (ValueError, IndexError, InvalidOperation):
                invalid += 1
        source = dict(archive.source)
    if not times:
        raise ValueError("no valid 1d bars in ranking period")
    return {"volume": str(total), "observed_days": len(times), "expected_days": 30,
            "duplicate_rows": duplicates, "invalid_rows": invalid,
            "partial_period": len(times) < 30, "source": source,
            "official_verified": False, "cross_validated_against_binance": False}

def main() -> None:
    # HF snapshot is a third-party discovery list, not proof of contract terms.
    hf_path = OUTPUT.with_name("hf_candidates.json")
    if not hf_path.exists():
        raise SystemExit("Missing output/hf_candidates.json: fetch the Hugging Face repository tree first")
    symbols = set(json.loads(hf_path.read_text()))
    extra = OUTPUT.with_name("historical_candidates.json")
    if extra.exists():
        symbols.update(json.loads(extra.read_text()))
    candidates = sorted(s for s in symbols if SYMBOL_RE.fullmatch(s))
    SYMBOL_CACHE.write_text(json.dumps(candidates, indent=2) + "\n")

    progress: dict[str, dict[str, str]] = {}
    if PROGRESS.exists():
        try:
            progress = json.loads(PROGRESS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            progress = {}
    # Discard stale progress from a different source date or candidate set.
    progress = {s: v for s, v in progress.items() if s in candidates}

    official_cooldown_until = 0.0
    consecutive_transport_failures = 0
    for index, symbol in enumerate(candidates, start=1):
        previous = progress.get(symbol, {})
        if previous.get("status") == "ok" and previous.get("source", {}).get("source_type"):
            continue
        try:
            if time.monotonic() < official_cooldown_until:
                raise RuntimeError("official source temporarily unavailable; retry deferred")
            details = quote_volume_from_archive(symbol)
            details.update({"source": {"url": BUCKET + archive_key(symbol), "source_type": "binance_official_archive", "dataset_revision": None}, "official_verified": True, "cross_validated_against_binance": False})
        except Exception as official_error:
            if not isinstance(official_error, urllib.error.HTTPError) or official_error.code != 404:
                if not isinstance(official_error, RuntimeError) or "retry deferred" not in str(official_error):
                    official_cooldown_until = time.monotonic() + 60
            try:
                details = quote_volume_from_hf(symbol)
                details["official_error"] = f"{type(official_error).__name__}: {official_error}"
            except Exception as fallback_error:
                progress[symbol] = {"status": "failed", "error": f"official: {official_error}; fallback: {fallback_error}"}
                print(f"[{index}/{len(candidates)}] {symbol}: FAILED {progress[symbol]['error']}", flush=True)
                PROGRESS.write_text(json.dumps(progress, indent=2) + "\n", encoding="utf-8")
                if isinstance(fallback_error, (urllib.error.URLError, TimeoutError, OSError)) and not (isinstance(fallback_error, urllib.error.HTTPError) and fallback_error.code == 404):
                    consecutive_transport_failures += 1
                    if consecutive_transport_failures >= 3:
                        raise SystemExit("Both sources unavailable repeatedly; progress retained for later resume.")
                else:
                    consecutive_transport_failures = 0
                continue
        consecutive_transport_failures = 0
        progress[symbol] = {"status": "ok", **details}
        print(f"[{index}/{len(candidates)}] {symbol}: {details['volume']} ({details['observed_days']} days; {details['source']['source_type']})", flush=True)
        PROGRESS.write_text(json.dumps(progress, indent=2) + "\n", encoding="utf-8")

    ranked = [
        (symbol, Decimal(record["volume"]))
        for symbol, record in progress.items()
        if record.get("status") == "ok"
    ]
    failures = {
        symbol: record.get("error", "unknown archive error")
        for symbol, record in progress.items()
        if record.get("status") == "failed"
    }

    ranked.sort(key=lambda item: (-item[1], item[0]))
    limitations = (
        "The third-party Hugging Face snapshot is described as USDT-M perpetual but is not official contract metadata. A direct March Binance archive confirms historical UM market data, "
        "not the original listing date or current trading status. USDT-M perpetual is inferred "
        "conservatively from the UM archive path and a symbol ending in USDT with no dated-contract "
        "suffix; the archives do not independently prove contract type. Ranking sums native daily "
        "quote-volume bars from the explicitly recorded official archive or third-party Parquet source. "
        "The selection uses currently represented Hugging Face symbols and may exclude delisted contracts and others, creating survivorship and discovery bias. The top 100 is conditional on these candidates and any ranking failures."
    )
    ranked_top = ranked[:TOP_N]
    if len(ranked_top) < TOP_N:
        raise SystemExit(f"Only {len(ranked_top)} eligible candidates; cannot select 100. Progress retained.")
    result = {
        "as_of": "2026-04-01",
        "source": "Hugging Face repository tree for discovery; official Binance monthly 1d archives preferred, Hugging Face daily Parquet fallback when unavailable",
        "ranking_metric": "sum of daily quote_asset_volume (kline CSV column 8, zero-index 7), in quote USDT",
        "ranking_period": "2026-03-02 through 2026-03-31 (30 daily bars)",
        "candidate_count": len(candidates),
        "ranked_candidate_count": len(ranked),
        "failed_candidate_count": len(failures),
        "ranking_coverage": round(len(ranked) / len(candidates), 6) if candidates else 0,
        "selection_limit": TOP_N,
        "candidate_sources_file": "candidate_sources.json",
        "historical_universe_complete": False,
        "survivorship_bias": "Current third-party snapshot may exclude delisted symbols; historical top 100 is conditional on discovered candidates.",
        "ranking_failures": failures,
        "partial_period_count": sum(1 for symbol, _ in ranked if progress[symbol].get("partial_period")),
        "duplicate_row_count": sum(int(progress[s].get("duplicate_rows", 0)) for s, _ in ranked),
        "invalid_row_count": sum(int(progress[s].get("invalid_rows", 0)) for s, _ in ranked),
        "known_listing_limitations": limitations,
        "symbols": [
            {
                "symbol": symbol,
                "market_type": "USDT-M perpetual (third-party dataset assertion; archive-inferred)",
                "contract_classification": "inferred: third-party dataset describes perpetual, official UM archive does not establish contract type",
                "ranking_archive_url": progress[symbol].get("source", {}).get("url", BUCKET + archive_key(symbol)),
                "ranking_archive_verified": progress[symbol].get("official_verified", "source" not in progress[symbol]),
                "ranking_source": progress[symbol].get("source", {"url": BUCKET + archive_key(symbol), "source_type": "binance_official_archive", "dataset_revision": None}),
                "cross_validated_against_binance": progress[symbol].get("cross_validated_against_binance", False),
                "ranking": rank,
                "ranking_volume": float(volume),
                "observed_days": int(progress[symbol].get("observed_days", 0)),
                "expected_days": 30,
                "duplicate_rows": int(progress[symbol].get("duplicate_rows", 0)),
                "invalid_rows": int(progress[symbol].get("invalid_rows", 0)),
                "partial_period": bool(progress[symbol].get("partial_period", True)),
                "ranking_period": "2026-03-02 through 2026-03-31 (30 daily bars)",
                "selection_method": "Hugging Face candidate; ranked by sum of available daily quote volume for 30 days before 2026-04-01 using source indicated",
                "known_listing_limitations": limitations,
            }
            for rank, (symbol, volume) in enumerate(ranked_top, start=1)
        ],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote top {len(ranked_top)} of {len(ranked)} ranked candidates ({len(candidates)} archive candidates) to {OUTPUT}")


if __name__ == "__main__":
    main()
