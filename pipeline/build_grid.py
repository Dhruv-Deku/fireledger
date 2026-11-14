"""Pre-aggregate harmonized hotspots onto a 0.25 deg monthly grid.

Reads  data/hotspots/*.parquet, data/derived/avail.parquet, data/derived/calib.parquet
Writes data/derived/grid.parquet

The API answers small areas from the hotspot points (exact). Continent-sized areas
would scan hundreds of millions of points, so they use this grid instead: weights
are computed per hotspot exactly as in the API, then summed per cell x month x
day/night x static-source flag. Cell index: gy = floor(lat * 4), gx = floor(lon * 4).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.weights import JOINS, RAW_VIIRS_SQL, STATIC_SQL, Z90, create_pers, weight_sql  # noqa: E402

HOT = (ROOT / "data" / "hotspots" / "*.parquet").as_posix()
DER = ROOT / "data" / "derived"


def main() -> None:
    t0 = time.time()
    con = duckdb.connect()
    con.execute("SET memory_limit = '4GB'")
    con.execute(f"SET temp_directory = '{(ROOT / 'data' / 'tmp').as_posix()}'")
    con.execute(f"CREATE TABLE avail AS SELECT * FROM read_parquet('{(DER / 'avail.parquet').as_posix()}')")
    con.execute(f"CREATE TABLE calib AS SELECT * FROM read_parquet('{(DER / 'calib.parquet').as_posix()}')")
    create_pers(con, DER)

    wv, sv = weight_sql("viirs")
    wm, sm = weight_sql("modis")
    con.execute(f"""
        COPY (
            WITH r AS (
                SELECT CAST(floor(h.lat * 4) AS SMALLINT) AS gy, CAST(floor(h.lon * 4) AS SMALLINT) AS gx,
                       year(h.acq_date)::SMALLINT AS y, month(h.acq_date)::TINYINT AS m, h.dn,
                       {STATIC_SQL} AS static, h.sat, {RAW_VIIRS_SQL} AS rv,
                       {wv} AS wv, {sv} AS sv, {wm} AS wm, {sm} AS sm
                FROM read_parquet('{HOT}') h {JOINS}
            )
            SELECT gy, gx, y, m, dn, static,
                   sum(wv)::FLOAT AS v, sum(wv * exp(-{Z90} * sv))::FLOAT AS v_lo, sum(wv * exp({Z90} * sv))::FLOAT AS v_hi,
                   sum(wm)::FLOAT AS md, sum(wm * exp(-{Z90} * sm))::FLOAT AS md_lo, sum(wm * exp({Z90} * sm))::FLOAT AS md_hi,
                   (count(*) FILTER (WHERE sat IN ('Terra', 'Aqua')))::INT AS raw_modis,
                   (count(*) FILTER (WHERE rv))::INT AS raw_viirs,
                   (count(*) FILTER (WHERE sat = 'SNPP'))::INT AS raw_snpp,
                   (count(*) FILTER (WHERE sat = 'NOAA20'))::INT AS raw_noaa20,
                   coalesce(sum(wv) FILTER (WHERE sat IN ('Terra', 'Aqua')), 0)::FLOAT AS v_from_modis,
                   coalesce(sum(wv) FILTER (WHERE sat = 'SNPP'), 0)::FLOAT AS v_from_snpp
            FROM r GROUP BY ALL
            ORDER BY gy, gx, y, m
        ) TO '{(DER / 'grid.parquet').as_posix()}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 200000)
    """)
    n = con.execute(f"SELECT count(*) FROM read_parquet('{(DER / 'grid.parquet').as_posix()}')").fetchone()[0]
    print(f"grid: {n:,} rows -> {DER / 'grid.parquet'}  [{time.time() - t0:.0f}s]", flush=True)


if __name__ == "__main__":
    main()
