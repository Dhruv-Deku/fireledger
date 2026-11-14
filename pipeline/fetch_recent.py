"""Download the hotspots that have no yearly archive zip yet (the current year) from the FIRMS
area API and add them to data/hotspots/ in the same layout as ingest.py.

    FIRMS_MAP_KEY=... in .env (free: https://firms.modaps.eosdis.nasa.gov/api/map_key/)
    python pipeline/fetch_recent.py                     # download + convert, Jan 1 -> yesterday (UTC)
    python pipeline/fetch_recent.py --download-only     # just fetch (safe while the API runs)
    python pipeline/fetch_recent.py --convert-only      # just rebuild the parquet files

Each request is a world query for up to DAYS days of one source, saved as its own CSV under
data/raw_recent/<source>/ before anything else happens, so a dropped connection costs one chunk
and a rerun resumes. Science-quality (SP) data is used where FIRMS has it, near-real-time (NRT)
after that. Chunks ending in the last few days are always fetched again, since NRT keeps filling
in. The CSVs then become data/hotspots/<product>_<year>.parquet, rows sorted by place so area
queries can skip most of each file. Those files carry a marker, and this script never overwrites
a file that came from an archive zip (when the yearly zip appears, ingest.py --force replaces ours).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pcsv
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest import READ_TYPES, SCHEMA, normalise  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hotspots"
RAW = ROOT / "data" / "raw_recent"
BASE = "https://firms.modaps.eosdis.nasa.gov"

# archive product name -> (science-quality source, near-real-time source) in the FIRMS API
PRODUCTS = {
    "modis": ("MODIS_SP", "MODIS_NRT"),
    "viirs-snpp": ("VIIRS_SNPP_SP", "VIIRS_SNPP_NRT"),
    "viirs-jpss1": ("VIIRS_NOAA20_SP", "VIIRS_NOAA20_NRT"),
}
DAYS = 5          # FIRMS area API maximum day range per request
REFRESH_DAYS = 3  # chunks reaching this close to the end date are fetched again
MARKER = b"fireledger_source"
SATS = {"Terra", "Aqua", "SNPP", "NOAA20"}


def map_key() -> str:
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("FIRMS_MAP_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    if not key:
        sys.exit("No FIRMS_MAP_KEY: get one free at https://firms.modaps.eosdis.nasa.gov/api/map_key/ "
                 "and put FIRMS_MAP_KEY=<key> in .env")
    return key


def get(url: str, tries: int = 6) -> str:
    """GET with retries for flaky connections and the API's transaction limit."""
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=600) as r:
                text = r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            wait = 10 * 2 ** i
            print(f"  network error ({e}); retry in {wait}s", flush=True)
            time.sleep(wait)
            continue
        if "transaction" in text[:300].lower():  # "Exceeding allowed transaction limit"
            print("  FIRMS transaction limit reached; waiting 2 min", flush=True)
            time.sleep(120)
            continue
        return text
    raise RuntimeError(f"gave up after {tries} tries: {url.split('/csv/')[0]}/...")


def availability(key: str) -> dict[str, tuple[date, date]]:
    text = get(f"{BASE}/api/data_availability/csv/{key}/ALL")
    if not text.startswith("data_id"):
        sys.exit(f"FIRMS refused the key: {text[:200]}")
    return {r["data_id"]: (date.fromisoformat(r["min_date"]), date.fromisoformat(r["max_date"]))
            for r in csv.DictReader(io.StringIO(text))}


def archive_years(product: str) -> set[int]:
    """Years of this product that came from the yearly archive zips (not from this script)."""
    years = set()
    for f in OUT.glob(f"{product}_*.parquet"):
        if MARKER not in (pq.read_schema(f).metadata or {}):
            years.add(int(f.stem.rsplit("_", 1)[1]))
    return years


def plan(product: str, start: date, end: date, avail: dict) -> list[tuple[str, date, int]]:
    """Chunks (source, first day, n days): SP where FIRMS has it, else NRT, at most DAYS each."""
    sp, nrt = PRODUCTS[product]
    chunks: list[list] = []
    d = start
    while d <= end:
        src = next((s for s in (sp, nrt) if s in avail and avail[s][0] <= d <= avail[s][1]), None)
        if src and chunks and chunks[-1][0] == src and chunks[-1][2] < DAYS \
                and chunks[-1][1] + timedelta(chunks[-1][2]) == d:
            chunks[-1][2] += 1
        elif src:
            chunks.append([src, d, 1])
        d += timedelta(1)
    return [tuple(c) for c in chunks]


def chunk_path(src: str, d0: date, n: int) -> Path:
    return RAW / src / f"{d0.isoformat()}_{n}.csv"


def fetch_chunk(key: str, src: str, d0: date, n: int, end: date) -> tuple[str, int, str]:
    path = chunk_path(src, d0, n)
    fresh = d0 + timedelta(n - 1) >= end - timedelta(REFRESH_DAYS)
    if path.exists() and not fresh:
        return path.name, -1, "cached"
    text = get(f"{BASE}/api/area/csv/{key}/{src}/world/{n}/{d0.isoformat()}")
    if not text.startswith("latitude"):
        raise RuntimeError(f"{src} {d0}: FIRMS said {text[:200]!r}")
    # the API returns [DATE, DATE + n - 1]; make sure, since conversion filters to exactly that
    days = {r[5] for r in csv.reader(io.StringIO(text)) if r and r[0] != "latitude"}
    want = {(d0 + timedelta(i)).isoformat() for i in range(n)}
    if days and not days & want:
        raise RuntimeError(f"{src} {d0}: got days {sorted(days)[:3]}..., expected {sorted(want)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return path.name, text.count("\n") - 1, src


def convert(product: str, year: int, chunks: list[tuple[str, date, int]]) -> int:
    """All of this product-year's chunks -> data/hotspots/<product>_<year>.parquet."""
    parts = []
    for src, d0, n in chunks:
        path = chunk_path(src, d0, n)
        t = pcsv.read_csv(path, convert_options=pcsv.ConvertOptions(
            column_types={k: v for k, v in READ_TYPES.items()}))
        if t.num_rows == 0:
            continue
        d = t.column("acq_date")
        keep = pc.and_(pc.greater_equal(d, pa.scalar(d0, pa.date32())),
                       pc.less_equal(d, pa.scalar(d0 + timedelta(n - 1), pa.date32())))
        keep = pc.and_(keep, pc.equal(pc.year(d), year))
        for batch in t.filter(keep).combine_chunks().to_batches():
            if batch.num_rows:
                parts.append(normalise(batch, None))
    out = OUT / f"{product}_{year}.parquet"
    if out.exists() and MARKER not in (pq.read_schema(out).metadata or {}):
        raise RuntimeError(f"{out.name} came from an archive zip; not overwriting it")
    if not parts:
        return 0
    t = pa.concat_tables(parts)
    bad = set(pc.unique(t.column("sat")).to_pylist()) - SATS
    if bad:
        raise RuntimeError(f"unexpected satellite codes {bad} in {product} {year}")
    # sort by 5 deg latitude band then longitude so each row group covers a small box
    band = pc.floor(pc.divide(pc.add(t.column("lat"), 90.0), 5.0))
    t = t.append_column("_band", band).sort_by([("_band", "ascending"), ("lon", "ascending")]).drop(["_band"])
    t = t.replace_schema_metadata({MARKER: b"firms_api"})
    tmp = out.with_suffix(".parquet.tmp")
    pq.write_table(t, tmp, compression="zstd",
                   compression_level=3, row_group_size=100_000,
                   use_dictionary=["sat", "dn", "conf", "version", "country"])
    os.replace(tmp, out)
    return t.num_rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, help="first day (default: Jan 1 after the last archive year)")
    ap.add_argument("--end", type=date.fromisoformat, help="last day (default: yesterday, UTC)")
    ap.add_argument("--products", nargs="*", default=list(PRODUCTS))
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--convert-only", action="store_true")
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()

    # --convert-only reuses the download step's plan, so it needs no network
    cache = RAW / "last_run.json"
    if args.convert_only and cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        avail = {k: (date.fromisoformat(a), date.fromisoformat(b)) for k, (a, b) in saved["avail"].items()}
        end = args.end or date.fromisoformat(saved["end"])
    else:
        avail = availability(map_key())
        end = args.end or datetime.now(timezone.utc).date() - timedelta(1)
        RAW.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"end": end.isoformat(),
                                     "avail": {k: [a.isoformat(), b.isoformat()] for k, (a, b) in avail.items()}}))
    plans = {}
    for p in args.products:
        start = args.start or date(max(archive_years(p) or {end.year - 1}) + 1, 1, 1)
        plans[p] = plan(p, start, end, avail)
        days = sum(n for _, _, n in plans[p])
        srcs = sorted({s for s, _, _ in plans[p]})
        print(f"{p}: {start} .. {end}, {days} days from {srcs or 'nothing'} in {len(plans[p])} requests", flush=True)

    if not args.convert_only:
        t0 = time.time()
        jobs = [(s, d0, n) for p in args.products for s, d0, n in plans[p]]
        key = map_key()
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(fetch_chunk, key, s, d0, n, end) for s, d0, n in jobs]
            for i, f in enumerate(as_completed(futs), 1):
                name, rows, src = f.result()
                if rows >= 0:
                    print(f"[{i}/{len(jobs)}] {src} {name}: {rows:,} rows  [{time.time() - t0:.0f}s]", flush=True)
        print(f"download done in {time.time() - t0:.0f}s", flush=True)

    if not args.download_only:
        for p in args.products:
            for y in sorted({d0.year for _, d0, _ in plans[p]} | {(d0 + timedelta(n - 1)).year for _, d0, n in plans[p]}):
                n = convert(p, y, [c for c in plans[p] if c[1].year <= y <= (c[1] + timedelta(c[2] - 1)).year])
                print(f"{p}_{y}.parquet: {n:,} rows", flush=True)


if __name__ == "__main__":
    main()
