"""Persistent hot spots: places that "burn" almost all year, year after year.

Gas flares, refineries, steel plants, volcanoes and coal-seam fires show up in most
months of the year, every year. Vegetation and crop fires don't: a field burns once
or twice a year, a savanna once. So a 0.01 deg (~1 km) cell is flagged persistent if,
in at least MIN_YEARS different years, it has hotspots in at least MIN_MONTHS
different calendar months of that year (any satellite).

The flag never deletes anything. It feeds the "hide industrial" filter (together with
FIRMS' own type 1-3), and the factor calibration leaves flagged rows out.

FIRMS' own type is not consistent between sensors: VIIRS types almost every eruption as a
vegetation fire (Holuhraun 2014: 49,791 of 49,958 S-NPP detections type 0), while MODIS types the
same lava as static land source or volcano in some years and as vegetation in others. With
"static_mask": "persistent+typed" in pipeline/harmonize_config.json, a 0.05 deg cell where either
sensor family types at least TYPED_SHARE of its detections as 1-3 becomes a static place for every
sensor and year (data/derived/typed_static.parquet, cy, cx = floor(lat*20), floor(lon*20)).
"persistent+typed_vbuffer" (shipped) adds the 8 neighbouring 0.05 deg cells around places VIIRS
types static, since MODIS's 1-5 km pixels put detections of the same flare or plant beyond them.

Reads  data/hotspots/*.parquet
Writes data/derived/persistent.parquet  iy, ix (= floor(lat*100), floor(lon*100)), years_flagged,
                                        first_year, last_year, detections, share_type_static
       The API and grid use a 3x3-cell buffer around each flagged cell, since a flare's
       detections scatter across neighbouring cells with geolocation error.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from calibrate import CFG  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HOT = ROOT / "data" / "hotspots"
DER = ROOT / "data" / "derived"
MIN_MONTHS = 9
MIN_YEARS = 3
TYPED_SHARE = 0.5   # share of a sensor family's detections in a 0.05 deg cell typed 1-3 by FIRMS
TYPED_MIN = 5       # ... out of at least this many detections of that family


def typed_static(con) -> None:
    out = DER / "typed_static.parquet"
    mask = CFG["static_mask"]
    if not mask.startswith("persistent+typed"):
        out.unlink(missing_ok=True)
        return
    share = 0.3 if mask.endswith("30") else TYPED_SHARE
    # margin: "_buffer" around every typed place; "_vbuffer" only around places VIIRS types static
    # (375 m, precise), which catches MODIS's coarser detections of the same flare or plant without
    # spreading MODIS-only typed places (e.g. grain dryers in Mato Grosso) over nearby farm fires
    pad = 1 if mask.endswith("buffer") else 0
    pad_where = f"n_v >= {TYPED_MIN} AND t_v >= {share} * n_v" if mask.endswith("_vbuffer") else "TRUE"
    con.execute(f"""
        COPY (
            WITH c AS (
                SELECT CAST(floor(lat * 20) AS INT) AS cy, CAST(floor(lon * 20) AS INT) AS cx,
                       count(*) FILTER (WHERE sat IN ('Terra', 'Aqua')) AS n_m,
                       count(*) FILTER (WHERE sat IN ('Terra', 'Aqua') AND type IN (1, 2, 3)) AS t_m,
                       count(*) FILTER (WHERE sat NOT IN ('Terra', 'Aqua')) AS n_v,
                       count(*) FILTER (WHERE sat NOT IN ('Terra', 'Aqua') AND type IN (1, 2, 3)) AS t_v
                FROM read_parquet('{(HOT / "*.parquet").as_posix()}') GROUP BY ALL),
            typed AS (
                SELECT * FROM c WHERE (n_m >= {TYPED_MIN} AND t_m >= {share} * n_m)
                                   OR (n_v >= {TYPED_MIN} AND t_v >= {share} * n_v))
            SELECT cy, cx FROM typed
            UNION
            SELECT t.cy + dy AS cy, t.cx + dx AS cx
            FROM (SELECT * FROM typed WHERE {pad_where}) t, range(-{pad}, {pad} + 1) r1(dy), range(-{pad}, {pad} + 1) r2(dx)
        ) TO '{out.as_posix()}' (FORMAT parquet)
    """)
    n = con.execute(f"SELECT count(*) FROM '{out.as_posix()}'").fetchone()[0]
    print(f"typed static places ({mask}): {n:,} cells of 0.05 deg", flush=True)


def main() -> None:
    t0 = time.time()
    con = duckdb.connect()
    con.execute("SET memory_limit = '3GB'")
    con.execute(f"SET temp_directory = '{(ROOT / 'data' / 'tmp').as_posix()}'")
    if "--typed-only" in sys.argv:  # keep persistent.parquet, redo only the both-sensor static places
        typed_static(con)
        return
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
    typed_static(con)
    print(f"persistent cells: {len(pers):,}; detections in them: {int(pers.detections.sum()):,}; "
          f"median share FIRMS already types as static: {pers.share_type_static.median():.2f}  [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
