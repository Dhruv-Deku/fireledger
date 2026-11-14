"""Persistent hot spots: places that "burn" almost all year, year after year.

Gas flares, refineries, steel plants, volcanoes and coal-seam fires show up in most
months of the year, every year. Vegetation and crop fires don't: a field burns once
or twice a year, a savanna once. So a 0.01 deg (~1 km) cell is flagged persistent if,
in at least MIN_YEARS different years, it has hotspots in at least MIN_MONTHS
different calendar months of that year (any satellite).

The flag never deletes anything. It feeds the "hide industrial" filter (together with
FIRMS' own type 1-3), and the factor calibration leaves flagged rows out.

Reads  data/hotspots/*.parquet
Writes data/derived/persistent.parquet  iy, ix (= floor(lat*100), floor(lon*100)), years_flagged,
                                        first_year, last_year, detections, share_type_static
       The API and grid use a 3x3-cell buffer around each flagged cell, since a flare's
       detections scatter across neighbouring cells with geolocation error.
"""
from __future__ import annotations

import time
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HOT = ROOT / "data" / "hotspots"
DER = ROOT / "data" / "derived"
MIN_MONTHS = 9
MIN_YEARS = 3


def main() -> None:
    t0 = time.time()
    con = duckdb.connect()
    con.execute("SET memory_limit = '3GB'")
    con.execute(f"SET temp_directory = '{(ROOT / 'data' / 'tmp').as_posix()}'")
    years = sorted({int(p.stem.split("_")[-1]) for p in HOT.glob("*.parquet")})
    parts = []
    for y in years:  # one year at a time keeps memory bounded
        files = [p.as_posix() for p in HOT.glob(f"*_{y}.parquet")]
        df = con.execute(f"""
            WITH c AS (
                SELECT CAST(floor(lat * 100) AS INT) AS iy, CAST(floor(lon * 100) AS INT) AS ix,
                       month(acq_date) AS m, count(*) AS n,
                       count(*) FILTER (WHERE type IN (1, 2, 3)) AS n_static
                FROM read_parquet({files}) GROUP BY ALL
            )
            SELECT iy, ix, {y} AS y, count(*) AS months, sum(n) AS n, sum(n_static) AS n_static
            FROM c GROUP BY iy, ix HAVING count(*) >= {MIN_MONTHS}
        """).df()
        parts.append(df)
        print(f"  {y}: {len(df):,} cells with >= {MIN_MONTHS} active months  [{time.time() - t0:.0f}s]", flush=True)

    allc = pd.concat(parts)
    agg = allc.groupby(["iy", "ix"]).agg(years_flagged=("y", "nunique"), first_year=("y", "min"),
                                         last_year=("y", "max"), detections=("n", "sum"),
                                         n_static=("n_static", "sum")).reset_index()
    pers = agg[agg.years_flagged >= MIN_YEARS].copy()
    pers["share_type_static"] = (pers.n_static / pers.detections).round(3)
    pers = pers.drop(columns="n_static").astype({"iy": "int32", "ix": "int32"})
    pers.to_parquet(DER / "persistent.parquet", index=False)
    print(f"persistent cells: {len(pers):,}; detections in them: {int(pers.detections.sum()):,}; "
          f"median share FIRMS already types as static: {pers.share_type_static.median():.2f}  [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
