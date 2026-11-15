"""Single hotspots with their weights for the preset regions, so a grid-only server (the public copy)
can still give the hotspot CSV there.

For each preset: every hotspot inside the preset polygon grown by 0.25 deg, with the CSV columns and
both units' weights (w, s for VIIRS; wm, sm for MODIS). The weights come from the full API's own SQL,
so the CSV matches the laptop's row for row. Writes data/preset_points/{id}.parquet and {id}.json (the
area the file covers).

    python pipeline/preset_points.py [preset ...]
"""
import json
import sys
from pathlib import Path

import shapely
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api import main as m  # noqa: E402
from api.weights import weight_sql  # noqa: E402

OUT = ROOT / "data" / "preset_points"
GROW = 0.25  # deg around the preset, so areas drawn along its edge are covered too


def main(ids):
    OUT.mkdir(parents=True, exist_ok=True)
    cur = m.db()
    wm, sm = weight_sql("modis")
    for p in m.preset_list():
        if ids and p["id"] not in ids:
            continue
        cover = shapely.simplify(shape(p["geometry"]).buffer(GROW), 0.02)
        q = m.Query(geometry=mapping(cover), start="2000-11-01", end=m.LAST_DAY.isoformat(), unit="viirs",
                    daynight="both", hide_industrial=False, harmonized=True)
        inner, params = m.rows_sql(q, cover, cur, m.CSV_COLS + f", {wm} AS wm, {sm} AS sm")
        f = OUT / f"{p['id']}.parquet"
        cur.execute(f"""COPY (SELECT acq_date, sat, st AS static, CAST(w AS FLOAT) AS w, CAST(s AS FLOAT) AS s,
                                     CAST(wm AS FLOAT) AS wm, CAST(sm AS FLOAT) AS sm,
                                     * EXCLUDE (acq_date, sat, st, rv, w, s, wm, sm)
                              FROM ({inner}) ORDER BY latitude, acq_date)
                        TO '{f.as_posix()}' (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 200000)""", params)
        (OUT / f"{p['id']}.json").write_text(json.dumps(mapping(cover)), encoding="utf-8")
        n = cur.execute(f"SELECT count(*) FROM '{f.as_posix()}'").fetchone()[0]
        print(f"{p['id']}: {n:,} hotspots, {f.stat().st_size / 1e6:.0f} MB", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
