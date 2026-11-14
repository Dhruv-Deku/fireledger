"""Stream the FIRMS archive zips into one Parquet file per sensor-year.

    nasa-firms-data/<product>_<year>_all_countries.zip  ->  data/hotspots/<product>_<year>.parquet

Nothing is extracted to disk and nothing is dropped: every hotspot row is kept,
and quality information (confidence, type, scan/track) stays as columns so later
steps can flag instead of delete.

Each country CSV is written as its own row group(s). FIRMS already splits the
archive by country, so per-row-group lat/lon min/max is roughly that country's
bounding box, and DuckDB can skip most of a file for an area query without us
paying for a global sort.

Usage:
    python pipeline/ingest.py                      # everything, 4 worker processes
    python pipeline/ingest.py --only modis_2024    # one zip (prefix match)
    python pipeline/ingest.py --workers 2          # less RAM
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pcsv
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "nasa-firms-data"
OUT = ROOT / "data" / "hotspots"

# FIRMS satellite codes -> one name per platform
SAT_NAMES = {"Terra": "Terra", "T": "Terra", "Aqua": "Aqua", "A": "Aqua",
             "N": "SNPP", "N20": "NOAA20", "N21": "NOAA21"}

SCHEMA = pa.schema([
    ("lat", pa.float64()),
    ("lon", pa.float64()),
    ("acq_date", pa.date32()),
    ("acq_time", pa.int16()),          # HHMM UTC
    ("sat", pa.string()),              # Terra | Aqua | SNPP | NOAA20
    ("dn", pa.string()),               # D | N
    ("frp", pa.float32()),             # MW
    ("bt4", pa.float32()),             # MODIS brightness / VIIRS bright_ti4 (K)
    ("bt5", pa.float32()),             # MODIS bright_t31 / VIIRS bright_ti5 (K)
    ("scan", pa.float32()),            # pixel size along scan (km)
    ("track", pa.float32()),           # pixel size along track (km)
    ("conf", pa.string()),             # l | n | h for both sensors
    ("conf_pct", pa.int8()),           # MODIS 0-100, null for VIIRS
    ("type", pa.int8()),               # 0 veg fire, 1 volcano, 2 static land source, 3 offshore
    ("version", pa.string()),
    ("country", pa.string()),
])

READ_TYPES = {
    "latitude": pa.float64(), "longitude": pa.float64(),
    "brightness": pa.float32(), "bright_ti4": pa.float32(),
    "bright_t31": pa.float32(), "bright_ti5": pa.float32(),
    "scan": pa.float32(), "track": pa.float32(),
    "acq_date": pa.date32(), "acq_time": pa.int16(),
    "satellite": pa.string(), "instrument": pa.string(),
    "confidence": pa.string(), "version": pa.string(),
    "frp": pa.float32(), "daynight": pa.string(), "type": pa.int8(),
}


def modis_conf_class(pct: pa.Array) -> pa.Array:
    # MODIS C6.1 user guide: 0-29 low, 30-79 nominal, 80-100 high
    return pc.if_else(pc.less(pct, 30), "l", pc.if_else(pc.less(pct, 80), "n", "h"))


def normalise(batch: pa.RecordBatch, country: str | None) -> pa.Table:
    cols = {name: batch.column(name) for name in batch.schema.names}
    n = batch.num_rows
    is_modis = "brightness" in cols

    codes = pc.dictionary_encode(cols["satellite"])
    names = pa.array([SAT_NAMES.get(s, s) for s in codes.dictionary.to_pylist()], pa.string())
    sat = pc.take(names, codes.indices)
    if is_modis:
        conf_pct = pc.cast(cols["confidence"], pa.int8())
        conf = modis_conf_class(conf_pct)
    else:
        conf_pct = pa.nulls(n, pa.int8())
        # archive files say l/n/h, the near-real-time feed low/nominal/high
        conf = pc.utf8_lower(pc.utf8_slice_codeunits(cols["confidence"], 0, 1))

    return pa.table({
        "lat": cols["latitude"],
        "lon": cols["longitude"],
        "acq_date": cols["acq_date"],
        "acq_time": cols["acq_time"],
        "sat": sat,
        "dn": cols["daynight"],
        "frp": cols["frp"],
        "bt4": cols["brightness" if is_modis else "bright_ti4"],
        "bt5": cols["bright_t31" if is_modis else "bright_ti5"],
        "scan": cols["scan"],
        "track": cols["track"],
        "conf": conf,
        "conf_pct": conf_pct,
        "type": cols["type"] if "type" in cols else pa.nulls(n, pa.int8()),
        "version": cols["version"],
        "country": pa.repeat(pa.scalar(country, pa.string()), n),
    }, schema=SCHEMA)


def ingest_zip(zip_path: Path) -> tuple[str, int, float]:
    t0 = time.time()
    out = OUT / (zip_path.name.replace("_all_countries.zip", ".parquet"))
    tmp = out.with_suffix(".parquet.tmp")
    rows = 0
    with zipfile.ZipFile(zip_path) as zf, pq.ParquetWriter(
        tmp, SCHEMA, compression="zstd", compression_level=3,
        use_dictionary=["sat", "dn", "conf", "version", "country"],
        write_statistics=True,
    ) as writer:
        for name in sorted(n for n in zf.namelist() if n.endswith(".csv")):
            # .../modis_2024_Cote_d_Ivoire.csv -> "Cote d Ivoire"
            country = re.sub(r"^.*?_\d{4}_", "", Path(name).stem).replace("_", " ")
            with zf.open(name) as f:
                reader = pcsv.open_csv(
                    f,
                    read_options=pcsv.ReadOptions(block_size=64 << 20),
                    convert_options=pcsv.ConvertOptions(column_types=READ_TYPES),
                )
                for batch in reader:
                    if batch.num_rows:
                        writer.write_table(normalise(batch, country))
                        rows += batch.num_rows
    os.replace(tmp, out)
    return zip_path.name, rows, time.time() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="zip name prefixes to process")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--force", action="store_true", help="redo zips that already have output")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    zips = sorted(RAW.glob("*_all_countries.zip"))
    if args.only:
        zips = [z for z in zips if any(z.name.startswith(p) for p in args.only)]
    todo = [z for z in zips
            if args.force or not (OUT / z.name.replace("_all_countries.zip", ".parquet")).exists()]
    # biggest first so the long VIIRS years don't straggle at the end
    todo.sort(key=lambda z: -z.stat().st_size)
    print(f"{len(todo)} of {len(zips)} zips to ingest with {args.workers} workers", flush=True)

    t0, total = time.time(), 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(ingest_zip, z): z for z in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                name, rows, secs = fut.result()
            except Exception as e:  # keep going; a rerun picks up whatever failed
                print(f"[{i}/{len(todo)}] FAILED {futs[fut].name}: {e!r}", file=sys.stderr, flush=True)
                continue
            total += rows
            print(f"[{i}/{len(todo)}] {name}: {rows:,} rows in {secs:.0f}s", flush=True)
    print(f"done: {total:,} rows in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
