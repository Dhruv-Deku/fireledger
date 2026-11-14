"""Pre-aggregate harmonized hotspots onto a 0.25 deg monthly grid.

Reads  data/hotspots/*.parquet, data/derived/avail.parquet, data/derived/calib.parquet
Writes data/derived/grid.parquet          every year (the archive)
       data/derived/grid_recent.parquet   --recent: only years newer than grid.parquet
                                          (pipeline/fetch_recent.py data), so a daily update
                                          doesn't rebuild 26 years
       data/derived/mtd_grid.parquet      --mtd: the latest month's days 1..D in every year,
                                          so a month in progress is compared like for like

The API answers small areas from the hotspot points (exact). Continent-sized areas
would scan hundreds of millions of points, so they use this grid instead: weights
are computed per hotspot exactly as in the API, then summed per cell x month x
day/night x static-source flag. Cell index: gy = floor(lat * 4), gx = floor(lon * 4).

    python pipeline/build_grid.py              # everything -> grid.parquet
    python pipeline/build_grid.py --recent     # new years only -> grid_recent.parquet
    python pipeline/build_grid.py --mtd        # month to date -> mtd_grid.parquet
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.weights import JOINS, RAW_VIIRS_SQL, STATIC_SQL, Z90, create_pers, weight_sql  # noqa: E402

HOTDIR = ROOT / "data" / "hotspots"
HOT = (HOTDIR / "*.parquet").as_posix()
DER = ROOT / "data" / "derived"


def year_of(f: Path) -> int:
    return int(f.stem.rsplit("_", 1)[1])


def files_sql(files: list[str] | str) -> str:
    return repr(files) if isinstance(files, list) else f"'{files}'"


def build(con, files: list[str] | str, out: Path, where: str = "TRUE", extra: str = "") -> int:
    wv, sv = weight_sql("viirs")
    wm, sm = weight_sql("modis")
    tmp = out.with_suffix(".parquet.tmp")
    con.execute(f"""
        COPY (
            WITH r AS (
                SELECT CAST(floor(h.lat * 4) AS SMALLINT) AS gy, CAST(floor(h.lon * 4) AS SMALLINT) AS gx,
                       year(h.acq_date)::SMALLINT AS y, month(h.acq_date)::TINYINT AS m, h.dn,
                       {STATIC_SQL} AS static, h.sat, {RAW_VIIRS_SQL} AS rv,
                       {wv} AS wv, {sv} AS sv, {wm} AS wm, {sm} AS sm
                FROM read_parquet({files_sql(files)}) h {JOINS}
                WHERE {where}
            )
            SELECT gy, gx, y, m, dn, static,
                   sum(wv)::FLOAT AS v, sum(wv * exp(-{Z90} * sv))::FLOAT AS v_lo, sum(wv * exp({Z90} * sv))::FLOAT AS v_hi,
                   sum(wm)::FLOAT AS md, sum(wm * exp(-{Z90} * sm))::FLOAT AS md_lo, sum(wm * exp({Z90} * sm))::FLOAT AS md_hi,
                   (count(*) FILTER (WHERE sat IN ('Terra', 'Aqua')))::INT AS raw_modis,
                   (count(*) FILTER (WHERE rv))::INT AS raw_viirs,
                   (count(*) FILTER (WHERE sat = 'SNPP'))::INT AS raw_snpp,
                   (count(*) FILTER (WHERE sat = 'NOAA20'))::INT AS raw_noaa20,
                   coalesce(sum(wv) FILTER (WHERE sat IN ('Terra', 'Aqua')), 0)::FLOAT AS v_from_modis,
                   coalesce(sum(wv) FILTER (WHERE sat = 'SNPP'), 0)::FLOAT AS v_from_snpp {extra}
            FROM r GROUP BY ALL
            ORDER BY gy, gx, y, m
        ) TO '{tmp.as_posix()}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 200000)
    """)
    os.replace(tmp, out)
    return con.execute(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--recent", action="store_true", help="only years newer than grid.parquet")
    mode.add_argument("--mtd", action="store_true", help="days 1..D of the latest month, every year")
    args = ap.parse_args()

    t0 = time.time()
    con = duckdb.connect()
    con.execute("SET memory_limit = '4GB'")
    con.execute(f"SET temp_directory = '{(ROOT / 'data' / 'tmp').as_posix()}'")
    con.execute(f"CREATE TABLE avail AS SELECT * FROM read_parquet('{(DER / 'avail.parquet').as_posix()}')")
    con.execute(f"CREATE TABLE calib AS SELECT * FROM read_parquet('{(DER / 'calib.parquet').as_posix()}')")
    create_pers(con, DER)
    grid = DER / "grid.parquet"

    if args.recent:
        last = con.execute(f"SELECT max(y) FROM read_parquet('{grid.as_posix()}')").fetchone()[0]
        files = [f.as_posix() for f in HOTDIR.glob("*.parquet") if year_of(f) > last]
        out = DER / "grid_recent.parquet"
        if not files:
            out.unlink(missing_ok=True)
            print(f"no hotspot files after {last}; grid.parquet covers everything", flush=True)
            return
        n = build(con, files, out)
        print(f"grid_recent: {n:,} rows for years after {last} -> {out}  [{time.time() - t0:.0f}s]", flush=True)
    elif args.mtd:
        # latest day any satellite observed; the month it falls in is the month in progress
        d = con.execute("SELECT max(d) FROM avail WHERE has_terra OR has_aqua OR has_snpp OR has_noaa20").fetchone()[0]
        out = DER / "mtd_grid.parquet"
        n = build(con, HOT, out, where=f"month(h.acq_date) = {d.month} AND day(h.acq_date) <= {d.day}",
                  extra=f", {d.day}::TINYINT AS through_day")
        print(f"mtd_grid: {n:,} rows, month {d.month} days 1..{d.day} of every year -> {out}  [{time.time() - t0:.0f}s]",
              flush=True)
    else:
        n = build(con, HOT, grid)
        (DER / "grid_recent.parquet").unlink(missing_ok=True)  # grid.parquet now holds those years
        print(f"grid: {n:,} rows -> {grid}  [{time.time() - t0:.0f}s]", flush=True)


if __name__ == "__main__":
    main()
