"""FireLedger API: harmonized hotspot calendar for any area the user draws.

    uvicorn api.main:app --host 0.0.0.0 --port 8000

All three POST endpoints take the same body (see Query). Every hotspot row gets a
harmonization weight in the requested unit, so the total for any polygon and date
range is just the sum of weights of the hotspots inside it. See pipeline/calibrate.py
for where the weights come from.
"""
from __future__ import annotations

import calendar as _cal
import functools
import io
import json
import math
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Literal

import duckdb
import numpy as np
import pandas as pd
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import shapely
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from scipy import stats
from shapely.geometry import box, shape
from shapely.ops import unary_union

from api.weights import JOINS, RAW_VIIRS_SQL, STATIC_SQL, Z90, create_pers, weight_sql

ROOT = Path(__file__).resolve().parents[1]
HOT = (ROOT / "data" / "hotspots" / "*.parquet").as_posix()
DER = ROOT / "data" / "derived"
GRID = DER / "grid.parquet"
GRID_RECENT = DER / "grid_recent.parquet"  # years added by pipeline/fetch_recent.py
MTD_GRID = DER / "mtd_grid.parquet"        # the month in progress: days 1..D of every year
REGIONS = DER / "regions.parquet"
BANDS_PATH = DER / "bands.json"
# conformal 90% bands learned from held-out years (pipeline/validate.py)
BANDS = json.loads(BANDS_PATH.read_text()) if BANDS_PATH.exists() else None
HAVE_POINTS = any((ROOT / "data" / "hotspots").glob("*.parquet"))
GRID_MIN_AREA = 50.0    # deg^2 (~7x7 deg); bigger areas are answered from the 0.25 deg grid
CSV_MAX_ROWS = 3_000_000
INDUSTRIAL_WARN = 0.10   # warn when this share of an area's harmonized count is industrial / persistent
DRIFT_FROM = 2021       # MODIS overpass drift makes modis-unit months from here on not like-for-like
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# quality flags per month (the app's badges): M counted from MODIS, C cross-calibrated VIIRS,
# D MODIS orbit drift, B blind spot (MODIS saw under 1 in BLIND_SPOT of the fires VIIRS would)
BLIND_SPOT = 8.0
BUSY_SHARE = 0.25   # a month is "busy" if its median is at least this share of the peak month's
LOOKAHEAD = 6       # critical_next looks this many months ahead
# area presets for GET /api/presets: (id, name, Natural Earth admin-1 ids to merge)
PRESETS = [
    ("punjab", "Punjab, India", ["IND-3249"]),
    ("haryana", "Haryana, India", ["IND-2430"]),
    ("patagonia", "Northern Patagonia, Argentina", ["ARG-1276", "ARG-1297"]),
    ("cordoba", "Córdoba, Argentina", ["ARG-1299"]),
    ("mato-grosso", "Mato Grosso, Brazil", ["BRA-602"]),
    ("california", "California, USA", ["USA-3521"]),
    ("northern-territory", "Northern Territory, Australia", ["AUS-2650"]),
    ("yakutia", "Sakha (Yakutia), Russia", ["RUS-2612"]),
    ("central-kalimantan", "Central Kalimantan, Indonesia", ["IDN-1931"]),
]

app = FastAPI(title="FireLedger API", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(RequestValidationError)
async def bad_body(request: Request, exc: RequestValidationError):
    """A body that doesn't fit Query is a client error: 400, as the app expects."""
    return JSONResponse(status_code=400, content={"detail": exc.errors()})

_con: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()
LAST_DAY: date | None = None            # latest day any satellite observed
MTD_KEY: tuple[int, int] | None = None  # (month, last day) that mtd_grid.parquet covers
# first near-real-time day per unit's reference satellites: FIRMS reprocesses NRT data into
# science-quality data ~3 months later, so counts from then on are preliminary
NRT_FROM: dict[str, date] = {}


def db() -> duckdb.DuckDBPyConnection:
    global _con, LAST_DAY, MTD_KEY, NRT_FROM
    with _lock:
        if _con is None:
            con = duckdb.connect()
            con.execute("SET memory_limit = '3GB'")
            if HAVE_POINTS:
                con.execute(f"CREATE VIEW hot AS SELECT * FROM read_parquet('{HOT}')")
            con.execute(f"CREATE TABLE avail AS SELECT * FROM read_parquet('{(DER / 'avail.parquet').as_posix()}')")
            LAST_DAY = con.execute("SELECT max(d) FROM avail "
                                   "WHERE has_terra OR has_aqua OR has_snpp OR has_noaa20").fetchone()[0]
            con.execute(f"CREATE TABLE calib AS SELECT * FROM read_parquet('{(DER / 'calib.parquet').as_posix()}')")
            create_pers(con, DER)
            if REGIONS.exists():
                con.execute(f"CREATE TABLE regions AS SELECT * FROM read_parquet('{REGIONS.as_posix()}')")
            if GRID.exists():
                files = [f.as_posix() for f in (GRID, GRID_RECENT) if f.exists()]
                con.execute(f"""CREATE VIEW grid AS SELECT *, (gy + 0.5) / 4.0 AS lat, (gx + 0.5) / 4.0 AS lon
                                FROM read_parquet({files!r})""")
            if MTD_GRID.exists():
                con.execute(f"""CREATE VIEW mtd_grid AS SELECT *, (gy + 0.5) / 4.0 AS lat, (gx + 0.5) / 4.0 AS lon
                                FROM read_parquet('{MTD_GRID.as_posix()}')""")
                MTD_KEY = con.execute("SELECT any_value(m), any_value(through_day) FROM mtd_grid").fetchone()
            recent = [f.as_posix() for f in (ROOT / "data" / "hotspots").glob("*.parquet")
                      if b"fireledger_source" in (pq.read_schema(f).metadata or {})]
            if recent:
                nrt = dict(con.execute(f"""
                    SELECT CASE WHEN sat IN ('Terra', 'Aqua') THEN 'modis' ELSE 'viirs' END, min(acq_date)
                    FROM read_parquet({recent!r}) WHERE version LIKE '%NRT' AND sat IN ('Terra', 'Aqua', 'NOAA20')
                    GROUP BY 1""").fetchall())
                NRT_FROM = {k: v for k, v in nrt.items() if v is not None}
            _con = con
        return _con.cursor()


# ---------------------------------------------------------------- request

class Query(BaseModel):
    geometry: dict | None = Field(None, description="GeoJSON Polygon/MultiPolygon, Feature or FeatureCollection")
    region: str | None = Field(None, description="preset id from /api/regions (e.g. IND, ARG); used when geometry is empty")
    start: str = Field("2000-11", description="YYYY-MM or YYYY-MM-DD")
    end: str | None = Field(None, description="YYYY-MM or YYYY-MM-DD; default = the latest day with data")
    unit: Literal["viirs", "modis"] = "viirs"
    harmonized: bool = Field(True, description="false = the raw record as it stands (MODIS before VIIRS "
                                               "existed, VIIRS after), i.e. the 'before' view with the 2012 jump")
    daynight: Literal["D", "N", "both"] = "both"
    hide_industrial: bool = False
    ref_month: str | None = Field(None, description="summary only: YYYY-MM to compare with normal; default = last month in range")
    precision: Literal["auto", "exact", "grid"] = Field(
        "auto", description="exact = every hotspot point; grid = 0.25 deg cells (fast for continents); auto picks by area")


def resolve_end(q: Query) -> None:
    """No end date given: up to the latest day any satellite observed."""
    if q.end is None:
        db()
        q.end = LAST_DAY.isoformat()


def parse_day(s: str, end: bool) -> date:
    parts = [int(p) for p in s.split("-")]
    if len(parts) == 2:
        y, m = parts
        return date(y, m, _cal.monthrange(y, m)[1] if end else 1)
    return date(*parts)


def parse_geometry(g: dict):
    t = g.get("type")
    if t == "FeatureCollection":
        geom = unary_union([shape(f["geometry"]) for f in g["features"] if f.get("geometry")])
    elif t == "Feature":
        geom = shape(g["geometry"])
    else:
        geom = shape(g)
    if geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        raise HTTPException(400, "geometry must be a non-empty Polygon or MultiPolygon")
    return shapely.make_valid(geom)


def aoi(q: Query):
    """The area of interest: the drawn geometry, else the preset region."""
    if q.geometry:
        return parse_geometry(q.geometry)
    if q.region:
        if not REGIONS.exists():
            raise HTTPException(503, "region presets not built: run pipeline/build_regions.py")
        row = db().execute("SELECT geojson FROM regions WHERE id = ?", [q.region]).fetchone()
        if row is None:
            raise HTTPException(404, f"unknown region {q.region!r}")
        return parse_geometry(json.loads(row[0]))
    raise HTTPException(400, "send either geometry or region")


# ---------------------------------------------------------------- core query

def pip_sql(geom, max_vertices: int = 400) -> str:
    """Even-odd ray-casting point-in-polygon test as plain SQL over h.lon / h.lat.

    DuckDB vectorizes this, so it is far faster than a Python UDF. Holes and
    multipolygons work because parity is counted across every ring. Very detailed
    shapes (e.g. a pasted country border) are simplified to at most max_vertices.
    """
    tol = 0.0
    while True:
        g = geom.simplify(tol, preserve_topology=True) if tol else geom
        polys = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
        rings = [r for p in polys for r in [p.exterior, *p.interiors]]
        if sum(len(r.coords) for r in rings) <= max_vertices or tol > 1:
            break
        tol = tol * 2 if tol else 0.001
    terms = []
    for ring in rings:
        pts = list(ring.coords)
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if y1 == y2:
                continue  # horizontal edges never cross a horizontal ray
            terms.append(f"((({y1!r} > h.lat) <> ({y2!r} > h.lat)) AND "
                         f"(h.lon < {x1!r} + (h.lat - {y1!r}) * {(x2 - x1) / (y2 - y1)!r}))::INT")
    return f"(({' + '.join(terms)}) % 2 = 1)"


MASK_RES = 0.05  # deg; pre-filter cells for the exact point-in-polygon test


def cell_mask(geom) -> pd.DataFrame:
    """MASK_RES cells touching the polygon: inside=True if the cell lies wholly inside it.

    Points in wholly-inside cells need no point-in-polygon test, so the ray-casting
    SQL only runs on the thin band of cells along the border.
    """
    minx, miny, maxx, maxy = geom.bounds
    cx = np.arange(math.floor(minx / MASK_RES), math.floor(maxx / MASK_RES) + 1)
    cy = np.arange(math.floor(miny / MASK_RES), math.floor(maxy / MASK_RES) + 1)
    X, Y = (a.ravel() for a in np.meshgrid(cx, cy))
    cells = shapely.box(X * MASK_RES, Y * MASK_RES, (X + 1) * MASK_RES, (Y + 1) * MASK_RES)
    shapely.prepare(geom)
    touch = shapely.intersects(geom, cells)
    inside = shapely.contains(geom, cells[touch])
    return pd.DataFrame({"cy": Y[touch].astype("int32"), "cx": X[touch].astype("int32"), "inside": inside})


def filters(q: Query, geom, points: bool) -> tuple[list[str], list]:
    """WHERE clauses shared by the point and grid paths (table alias h)."""
    minx, miny, maxx, maxy = geom.bounds
    where = ["h.lat BETWEEN ? AND ?", "h.lon BETWEEN ? AND ?"]
    params: list = [miny, maxy, minx, maxx]
    if q.daynight != "both":
        where.append("h.dn = ?")
        params.append(q.daynight)
    if q.hide_industrial:  # FIRMS type 1-3 (volcano, static land source, offshore) or persistent hot spot
        where.append(f"NOT {STATIC_SQL}" if points else "NOT h.static")
    # a drawn rectangle needs no point-in-polygon test; points use a cell mask (rows_sql)
    if not points and not is_rect(geom):
        where.append(pip_sql(geom))
    return where, params


def is_rect(geom) -> bool:
    return geom.equals(box(*geom.bounds))


def rows_sql(q: Query, geom, cur, extra_cols: str = "",
             extra_where: list[str] | None = None, extra_params: list | None = None) -> tuple[str, list]:
    """SQL selecting every hotspot in the AOI with its weight w and band spread s."""
    where, params = filters(q, geom, True)
    where.append("h.acq_date BETWEEN ? AND ?")
    params += [parse_day(q.start, False), parse_day(q.end, True)]
    where += extra_where or []
    params += extra_params or []
    mask_join = ""
    if not is_rect(geom):
        name = f"aoi_{uuid.uuid4().hex}"
        cur.register(name, cell_mask(geom))
        inv = int(round(1 / MASK_RES))
        mask_join = (f"JOIN {name} m ON m.cy = CAST(floor(h.lat * {inv}) AS INT) "
                     f"AND m.cx = CAST(floor(h.lon * {inv}) AS INT)")
        where.append(f"(m.inside OR {pip_sql(geom)})")
    w, s = weight_sql(q.unit)
    sql = f"""
        SELECT h.acq_date, h.sat, {RAW_VIIRS_SQL} AS rv, {STATIC_SQL} AS st, {w} AS w, {s} AS s {extra_cols}
        FROM hot h {mask_join} {JOINS}
        WHERE {' AND '.join(where)}
    """
    return sql, params


def use_grid(q: Query, geom) -> bool:
    if not HAVE_POINTS:
        return True
    if q.precision == "exact" or not GRID.exists():
        return False
    return q.precision == "grid" or geom.area > GRID_MIN_AREA


def monthly_points(q: Query, geom, cur):
    inner, params = rows_sql(q, geom, cur)
    return cur.execute(f"""
        SELECT year(acq_date) AS year, month(acq_date) AS month,
               sum(w) AS count,
               sum(w * exp(-{Z90} * s)) AS lo,
               sum(w * exp({Z90} * s)) AS hi,
               count(*) FILTER (WHERE sat IN ('Terra', 'Aqua')) AS raw_modis,
               count(*) FILTER (WHERE rv) AS raw_viirs,
               count(*) FILTER (WHERE sat = 'SNPP') AS raw_snpp,
               count(*) FILTER (WHERE sat = 'NOAA20') AS raw_noaa20,
               sum(w) FILTER (WHERE sat IN ('Terra', 'Aqua')) AS w_modis,
               sum(w) FILTER (WHERE sat = 'SNPP') AS w_snpp,
               sum(w) FILTER (WHERE st) AS w_industrial
        FROM ({inner}) GROUP BY ALL
    """, params).df()


def monthly_grid(q: Query, geom, cur, table: str = "grid"):
    """Same columns as monthly_points, from the 0.25 deg grid (cell centre inside the AOI)."""
    minx, miny, maxx, maxy = geom.bounds
    where, params = filters(q, geom, False)
    # integer cell bounds too, so DuckDB can skip parquet row groups
    where += ["h.gy BETWEEN ? AND ?", "(h.y::INT * 100 + h.m) BETWEEN ? AND ?"]
    d0, d1 = parse_day(q.start, False), parse_day(q.end, True)
    params += [math.floor(miny * 4) - 1, math.floor(maxy * 4), d0.year * 100 + d0.month, d1.year * 100 + d1.month]
    v = "v" if q.unit == "viirs" else "md"
    from_modis = "v_from_modis" if q.unit == "viirs" else "md"
    from_snpp = "v_from_snpp" if q.unit == "viirs" else "0"
    return cur.execute(f"""
        SELECT h.y AS year, h.m AS month,
               sum({v}) AS count, sum({v}_lo) AS lo, sum({v}_hi) AS hi,
               sum(raw_modis) AS raw_modis, sum(raw_viirs) AS raw_viirs,
               sum(raw_snpp) AS raw_snpp, sum(raw_noaa20) AS raw_noaa20,
               sum({from_modis}) AS w_modis, sum({from_snpp}) AS w_snpp,
               sum({v}) FILTER (WHERE h.static) AS w_industrial
        FROM {table} h WHERE {' AND '.join(where)} GROUP BY ALL
    """, params).df()


def band_stratum(geom) -> str | None:
    """Latitude stratum whose band table fits this area; None (the all-latitude table) for areas
    that span strata, e.g. a continent."""
    lo, hi = abs(geom.bounds[1]), abs(geom.bounds[3])
    if geom.bounds[1] < 0 < geom.bounds[3]:
        lo = 0.0
    lo, hi = min(lo, hi), max(lo, hi)
    for name, (a, b) in (BANDS or {}).get("strata", {}).items():
        if a <= lo and hi < b + 5:  # allow a small spill over the edge
            return name
    return None


def band_scale(geom) -> str:
    """Which scale's band table fits an area this size (pipeline/validate.py SCALE_MAX_AREA)."""
    for name, max_area in BANDS.get("scale_max_area_deg2", {}).items():
        if max_area is None or geom.area <= max_area:
            return name
    return "world_month"


def conf_q(total: float, link: str, band: tuple[str, str | None] | None = None) -> float:
    """90% half-width in log space for a monthly total converted through one link, from the
    band table for this area's size and latitude (the all-units table if there is none)."""
    edges = [e if e is not None else float("inf") for e in BANDS["edges"]]
    table = BANDS[link]
    if band and f"{link}_tables" in BANDS:
        by_scale = BANDS[f"{link}_tables"].get(band[0], {})
        table = by_scale.get(band[1] or "all") or by_scale.get("all") or table
    i = max(0, min(len(table) - 1, int(np.searchsorted(edges, total, side="right")) - 1))
    return table[i]


def viirs_band(count: float, from_modis: float, from_snpp: float,
               band: tuple[str, str | None] | None = None) -> tuple[float, float]:
    """lo/hi for a VIIRS-unit monthly total: NOAA-20 part is observed, the S-NPP part carries
    the S-NPP -> NOAA-20 band, the MODIS part carries both links (added in quadrature)."""
    lo = hi = count - from_modis - from_snpp
    for part, q in [(from_snpp, conf_q(from_snpp, "snpp", band)),
                    (from_modis, math.hypot(conf_q(from_modis, "modis", band), conf_q(from_modis, "snpp", band)))]:
        if part > 0:
            lo += max(0.0, (part + 1) * math.exp(-q) - 1)
            hi += (part + 1) * math.exp(q) - 1
    return max(lo, 0.0), hi


def month_to_date(q: Query, geom, method: str, month: int, day: int) -> dict[int, dict] | None:
    """Counts for days 1..day of `month` in every year of the range (harmonized `count`, plus
    `raw_modis` and `raw_viirs`), so a month in progress is compared with the same days of other
    years. None if the grid path has no mtd_grid for exactly this month and day
    (pipeline/build_grid.py --mtd)."""
    cur = db()
    if method == "grid":
        if MTD_KEY is None or tuple(MTD_KEY) != (month, day):
            return None
        df = monthly_grid(q, geom, cur, table="mtd_grid")
    else:
        inner, params = rows_sql(q, geom, cur, extra_where=["month(h.acq_date) = ?", "day(h.acq_date) <= ?"],
                                 extra_params=[month, day])
        df = cur.execute(f"""
            SELECT year(acq_date) AS year, sum(w) AS count,
                   count(*) FILTER (WHERE sat IN ('Terra', 'Aqua')) AS raw_modis, count(*) FILTER (WHERE rv) AS raw_viirs
            FROM ({inner}) GROUP BY 1""", params).df()
    return {int(r.year): {"count": float(r.count), "raw_modis": float(r.raw_modis), "raw_viirs": float(r.raw_viirs)}
            for r in df.itertuples()}


def month_flags(q: Query, y: int, count: float, raw_modis: int, from_modis: bool) -> str:
    """Quality badges: M counted from MODIS, C cross-calibrated VIIRS, D MODIS orbit drift,
    B blind spot (VIIRS would see BLIND_SPOT x more fires than MODIS did)."""
    f = "M" if from_modis else ("C" if q.harmonized else "")
    if from_modis and y >= DRIFT_FROM:
        f += "D"
    if from_modis and q.unit == "viirs" and q.harmonized and raw_modis > 0 and count / raw_modis >= BLIND_SPOT:
        f += "B"
    return f


def monthly(q: Query) -> tuple[list[dict], str, dict]:
    """Calendar months, the method used, and which months VIIRS was observing (for the raw view)."""
    resolve_end(q)
    cur = db()
    geom = aoi(q)
    method = "grid" if use_grid(q, geom) else "exact"
    df = (monthly_grid if method == "grid" else monthly_points)(q, geom, cur)
    df = df.astype({"year": int, "month": int}).set_index(["year", "month"])
    band = (band_scale(geom), band_stratum(geom)) if BANDS else None

    # months the satellites were actually observing (so "0 fires" differs from "no data")
    d0, d1 = parse_day(q.start, False), parse_day(q.end, True)
    cov = cur.execute("""
        SELECT year(d) AS year, month(d) AS month,
               bool_or(has_terra OR has_aqua) AS modis, bool_or(has_snpp) AS snpp, bool_or(has_noaa20) AS noaa20,
               avg(((has_terra <> has_aqua) AND NOT has_snpp AND NOT has_noaa20)::INT) AS single_modis
        FROM avail WHERE d BETWEEN ? AND ? GROUP BY ALL
    """, [d0, d1]).df().set_index(["year", "month"])

    out, viirs_cov = [], {}
    y, m = d0.year, d0.month
    stop = min(d1, LAST_DAY)  # no months after the data ends
    while (y, m) <= (stop.year, stop.month):
        has_cov = (y, m) in cov.index and (cov.loc[(y, m), "modis"] if q.unit == "modis"
                                            else bool(cov.loc[(y, m), ["modis", "snpp", "noaa20"]].any()))
        viirs_cov[(y, m)] = (y, m) in cov.index and bool(cov.loc[(y, m), ["snpp", "noaa20"]].any())
        r = df.loc[(y, m)] if (y, m) in df.index else None
        count = float(r["count"]) if r is not None else 0.0
        if not has_cov:
            out.append({"year": y, "month": m, "count": None, "lo": None, "hi": None, "flags": "",
                        "raw_modis": None, "raw_viirs": None, "raw_snpp": None, "raw_noaa20": None, "industrial": None,
                        "source": "no data",
                        "drift": False})
        else:
            if q.unit == "modis":
                source = "MODIS"
            elif r is None or count == 0:
                source = ("VIIRS" if cov.loc[(y, m), "noaa20"] else
                          "S-NPP→VIIRS" if cov.loc[(y, m), "snpp"] else "MODIS→VIIRS")
            else:
                from_modis = (r["w_modis"] or 0) / count
                from_snpp = (r["w_snpp"] or 0) / count
                source = ("MODIS→VIIRS" if from_modis > 0.5 else
                          "S-NPP→VIIRS" if from_snpp > 0.5 else "VIIRS")
            if r is None:
                lo = hi = 0.0
            elif q.unit == "viirs" and BANDS:
                lo, hi = viirs_band(count, float(np.nan_to_num(r["w_modis"])), float(np.nan_to_num(r["w_snpp"])),
                                    band)
                # months mostly seen by one MODIS only (Terra-only Nov 2000 - Jul 2002) aren't covered by
                # the conformal tables, which were learned on Terra+Aqua years: keep the factor band if wider
                if cov.loc[(y, m), "single_modis"] > 0.2:
                    lo, hi = min(lo, float(r["lo"])), max(hi, float(r["hi"]))
            else:
                lo, hi = float(r["lo"]), float(r["hi"])
            raw_modis = int(r["raw_modis"]) if r is not None else 0
            raw_viirs = int(r["raw_viirs"]) if r is not None else 0
            from_modis = q.unit == "modis" or source == "MODIS→VIIRS"
            if not q.harmonized:  # the record as it stands: MODIS until VIIRS exists, then VIIRS
                from_modis = q.unit == "modis" or not viirs_cov[(y, m)]
                count = lo = hi = float(raw_modis if from_modis else raw_viirs)
                source = "MODIS (raw)" if from_modis else "VIIRS (raw)"
            out.append({
                "year": y, "month": m,
                "drift": q.unit == "modis" and y >= DRIFT_FROM,
                "count": round(count, 1),
                "lo": round(lo, 1),
                "hi": round(hi, 1),
                "flags": month_flags(q, y, count, raw_modis, from_modis),
                "raw_modis": raw_modis,
                "raw_viirs": raw_viirs,
                "raw_snpp": int(r["raw_snpp"]) if r is not None else 0,
                "raw_noaa20": int(r["raw_noaa20"]) if r is not None else 0,
                "industrial": round(float(np.nan_to_num(r["w_industrial"])), 1) if r is not None else 0.0,
                "source": source,
            })
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    # the month the data stops in, if it stops before the month ends
    last = min(d1, LAST_DAY)
    for r in out:
        r["partial"], r["through_day"] = False, None
        nrt = NRT_FROM.get(q.unit)
        r["preliminary"] = bool(nrt and r["count"] is not None
                                and date(r["year"], r["month"], _cal.monthrange(r["year"], r["month"])[1]) >= nrt)
        if ((r["year"], r["month"]) == (last.year, last.month) and r["count"] is not None
                and last.day < _cal.monthrange(last.year, last.month)[1]):
            r["partial"], r["through_day"] = True, last.day
    return out, method, viirs_cov


# ---------------------------------------------------------------- endpoints

@app.get("/api/health")
def health():
    cur = db()
    lo = cur.execute("SELECT min(d) FROM avail").fetchone()[0]
    return {"ok": True, "coverage": {"start": str(lo), "end": str(LAST_DAY)},
            "mode": "full" if HAVE_POINTS else "grid-only",
            "month_to_date": {"month": MTD_KEY[0], "through_day": MTD_KEY[1]} if MTD_KEY else None,
            "preliminary_from": {k: str(v) for k, v in NRT_FROM.items()}}


@app.get("/api/proof")
def proof():
    """Validation results for the Method & Trust page (built by pipeline/validate.py)."""
    path = DER / "proof.json"
    if not path.exists():
        raise HTTPException(503, "proof not built yet: run pipeline/validate.py")
    out = json.loads(path.read_text(encoding="utf-8"))
    ba = DER / "burned_area.json"  # independent check against burned area (pipeline/burned_area.py)
    if ba.exists():
        out["burned_area"] = json.loads(ba.read_text(encoding="utf-8"))
    cr = DER / "creams_check.json"  # India's official residue-burning counts (pipeline/creams.py)
    if cr.exists():
        out["creams"] = json.loads(cr.read_text(encoding="utf-8"))
    return out


@functools.lru_cache(maxsize=1)
def preset_list() -> list[dict]:
    out = []
    for pid, name, ids in PRESETS:
        rows = db().execute(f"SELECT geojson FROM regions WHERE id IN ({', '.join('?' * len(ids))})", ids).fetchall()
        if not rows:
            continue
        g = unary_union([parse_geometry(json.loads(r[0])) for r in rows]).buffer(0)
        if g.geom_type == "MultiPolygon":  # the app takes one Polygon: keep the main landmass
            g = max(g.geoms, key=lambda p: p.area)
        g = g.simplify(0.01, preserve_topology=True)
        rings = [g.exterior, *g.interiors]
        out.append({"id": pid, "name": name, "geometry": {
            "type": "Polygon", "coordinates": [[[round(x, 4), round(y, 4)] for x, y in r.coords] for r in rings]}})
    return out


@app.get("/api/presets")
def presets():
    """Ready-made areas for the app: [{id, name, geometry (GeoJSON Polygon)}]."""
    if not REGIONS.exists():
        raise HTTPException(503, "region presets not built: run pipeline/build_regions.py")
    return preset_list()


@app.get("/api/regions")
def regions(q: str | None = None, kind: Literal["country", "state"] | None = None, limit: int = 300):
    """Preset areas (Natural Earth, India point of view): id, name, kind, country, subregion, bbox."""
    if not REGIONS.exists():
        raise HTTPException(503, "region presets not built: run pipeline/build_regions.py")
    where, params = ["true"], []
    if q:
        where.append("(name ILIKE ? OR country ILIKE ? OR id ILIKE ?)")
        params += [f"%{q}%"] * 3
    if kind:
        where.append("kind = ?")
        params.append(kind)
    rows = db().execute(f"""
        SELECT id, name, kind, country, subregion, bbox FROM regions
        WHERE {' AND '.join(where)} ORDER BY kind, name LIMIT {int(limit)}
    """, params).fetchall()
    return [{"id": r[0], "name": r[1], "kind": r[2], "country": r[3], "subregion": r[4], "bbox": list(r[5])}
            for r in rows]


@app.get("/api/regions/{region_id}")
def region_geometry(region_id: str):
    """One preset as a GeoJSON Feature, to draw on the map or send back as geometry."""
    row = db().execute("SELECT id, name, kind, country, geojson FROM regions WHERE id = ?", [region_id]).fetchone() \
        if REGIONS.exists() else None
    if row is None:
        raise HTTPException(404, f"unknown region {region_id!r}")
    return {"type": "Feature", "properties": {"id": row[0], "name": row[1], "kind": row[2], "country": row[3]},
            "geometry": json.loads(row[4])}


@app.get("/api/grid")
def grid_layer(year: int, month: int | None = None, unit: Literal["viirs", "modis"] = "viirs",
               res: float = 0.5, daynight: Literal["D", "N", "both"] = "both",
               hide_industrial: bool = False):
    """Harmonized hotspot count per grid cell for one year (or one month), for the map layer.

    cells: [lat, lon, count] at the cell centre, only cells with fire.
    """
    if not GRID.exists():
        raise HTTPException(503, "grid not built yet: run pipeline/build_grid.py")
    if res not in (0.25, 0.5, 1.0, 2.0):
        raise HTTPException(400, "res must be 0.25, 0.5, 1 or 2 (degrees)")
    f = int(res * 4)
    where, params = ["y = ?"], [year]
    if month:
        where.append("m = ?")
        params.append(month)
    if daynight != "both":
        where.append("dn = ?")
        params.append(daynight)
    if hide_industrial:
        where.append("NOT static")
    col = "v" if unit == "viirs" else "md"
    rows = db().execute(f"""
        SELECT (floor(gy / {f}) + 0.5) * {res} AS lat, (floor(gx / {f}) + 0.5) * {res} AS lon,
               round(sum({col}), 1) AS count
        FROM grid WHERE {' AND '.join(where)} GROUP BY 1, 2 HAVING sum({col}) > 0
    """, params).fetchall()
    return {"year": year, "month": month, "unit": unit, "res": res,
            "max": max((r[2] for r in rows), default=0), "cells": [list(r) for r in rows]}


@app.post("/api/calendar")
def calendar(q: Query):
    months, method, _ = monthly(q)
    return {"unit": q.unit, "method": method, "months": months}


def anomaly_label(pct: float) -> str:
    """pct = share of other years with a lower count for this calendar month (0-100)."""
    if pct >= 90: return "well above normal"
    if pct >= 75: return "above normal"
    if pct <= 10: return "well below normal"
    if pct <= 25: return "below normal"
    return "normal"


def month_label(cal: float) -> str:
    """10.2 -> 'early Oct' (cal = 1-based calendar month plus fraction of the month)."""
    m = int(cal) % 12 or 12
    part = cal - int(cal)
    return f"{'early' if part < 1 / 3 else 'mid' if part < 2 / 3 else 'late'} {MONTHS[m - 1]}"


def crossing(counts: np.ndarray, frac: float) -> float:
    """Months from the start of the sequence until `frac` of its total has burned
    (each month's fire spread evenly through the month)."""
    target = frac * counts.sum()
    cum = np.concatenate([[0.0], np.cumsum(counts)])
    i = int(np.searchsorted(cum, target, side="left")) - 1
    i = max(0, min(i, len(counts) - 1))
    return i + ((target - cum[i]) / counts[i] if counts[i] > 0 else 0.0)


def season_window(clim: np.ndarray, peak: int, floor: float = 0.15) -> list[int]:
    """Calendar months of the season around `peak`: grow both ways while the month still has at
    least `floor` of the peak's fire and we haven't passed a trough into another season."""
    thr = floor * clim[peak - 1]
    win = [peak]
    for step in (-1, 1):
        m = peak
        for _ in range(11):
            nxt = (m - 1 + step) % 12 + 1
            if nxt in win or clim[nxt - 1] < thr or (m != peak and clim[nxt - 1] > clim[m - 1]):
                break
            win = [nxt] + win if step < 0 else win + [nxt]
            m = nxt
    return win


def window_timing(win: list[int], counts: np.ndarray) -> tuple[float, float, float]:
    """Start / centre / end (10% / 50% / 90% of the window's fire) as calendar month + fraction."""
    w0 = win[0]
    to_cal = lambda pos: (w0 - 1 + pos) % 12 + 1  # noqa: E731
    return tuple(to_cal(crossing(counts, f)) for f in (0.10, 0.50, 0.90))


def season_timing(months: list[dict], full: list[int]) -> dict | None:
    """Fire-season timing: the main season around the peak month (start / peak / end = 10% /
    busiest month / 90% of that season's hotspots), a second season if there is one, and whether
    the main season's start and end have shifted over the years (Sen's slope, Mann-Kendall)."""
    by = {(r["year"], r["month"]): r["count"] for r in months}
    clim = np.array([np.mean([by[(y, m)] for y in full if (y, m) in by] or [0.0]) for m in range(1, 13)])
    if clim.sum() <= 0:
        return None
    total = clim.sum()

    def describe(peak: int) -> dict:
        win = season_window(clim, peak)
        st, _, en = window_timing(win, clim[[m - 1 for m in win]])
        return {"months": [MONTHS[m - 1] for m in win], "peak": {"month": peak, "label": MONTHS[peak - 1]},
                "start": {"cal": round(st, 2), "label": month_label(st)},
                "end": {"cal": round(en, 2), "label": month_label(en)},
                "length_months": round((en - st) % 12, 1),
                "share_of_year": round(float(clim[[m - 1 for m in win]].sum() / total), 3)}

    peak = int(np.argmax(clim)) + 1
    main = describe(peak)
    win = season_window(clim, peak)

    # a second season: another local maximum outside the main season, at least 30% of the peak
    second = None
    maxima = [m for m in range(1, 13) if m not in win and clim[m - 1] >= clim[m % 12] and clim[m - 1] >= clim[m - 2]]
    for m in sorted(maxima, key=lambda m: -clim[m - 1]):
        if clim[m - 1] >= 0.3 * clim[peak - 1]:
            second = describe(m) | {"share_of_peak": round(float(clim[m - 1] / clim[peak - 1]), 2)}
        break

    # top 3 consecutive months: how concentrated the burning is
    tri = [clim[[(i + k) % 12 for k in range(3)]].sum() for i in range(12)]
    i3 = int(np.argmax(tri))

    # main season, year by year, in the season window padded by a month each side (so a season that
    # drifts isn't cut off at the window edge); a window across New Year takes Jan.. from the next year
    other = set(season_window(clim, second["peak"]["month"])) if second else set()
    twin = list(win)
    for pad, at_start in (((win[0] - 2) % 12 + 1, True), (win[-1] % 12 + 1, False)):
        if pad not in twin and pad not in other and len(twin) < 12:
            twin = [pad] + twin if at_start else twin + [pad]
    rows = []
    for y in full:
        keys = [(y + (m < twin[0]), m) for m in twin]
        if not all(k in by for k in keys):
            continue
        c = np.array([by[k] for k in keys], float)
        if c.sum() < 20:
            continue
        st, ce, en = window_timing(twin, c)
        rows.append({"year": y, "start": round(st, 2), "center": round(ce, 2), "end": round(en, 2),
                     "total": round(float(c.sum()), 1), "_pos": [crossing(c, f) for f in (0.10, 0.5, 0.90)]})
    shift = None
    if len(rows) >= 8:
        yrs = np.array([r["year"] for r in rows], float)
        shift = {"years": [int(yrs[0]), int(yrs[-1])]}
        for k, name in enumerate(["start", "center", "end"]):
            pos = np.array([r["_pos"][k] for r in rows])
            slope = stats.theilslopes(pos, yrs)[0]
            pv = stats.kendalltau(yrs, pos)[1]
            shift[f"{name}_days_per_decade"] = round(float(slope * 30.44 * 10), 1)
            shift[f"{name}_p"] = round(float(pv), 3) if np.isfinite(pv) else 1.0
        shift["significant"] = bool(min(shift["start_p"], shift["end_p"]) < 0.05)
        parts = []
        for name in ("start", "end"):
            d = shift[f"{name}_days_per_decade"]
            parts.append(f"{'starts' if name == 'start' else 'ends'} {abs(d):.0f} days "
                         f"{'earlier' if d < 0 else 'later'} per decade"
                         + ("" if shift[f"{name}_p"] < 0.05 else " (not significant)"))
        shift["text"] = "main season " + ", ".join(parts)
    for r in rows:
        r.pop("_pos")

    return main | {"second_season": second,
                   "core_3_months": {"months": [MONTHS[(i3 + k) % 12] for k in range(3)],
                                     "share": round(float(tri[i3] / total), 3)},
                   "shift": shift, "by_year": rows}


@app.post("/api/summary")
def summary(q: Query):
    months, method, viirs_cov = monthly(q)
    months = [r for r in months if r["count"] is not None]
    if not months:
        raise HTTPException(404, "no satellite coverage in this date range")

    if q.ref_month:
        ry, rm = (int(p) for p in q.ref_month.split("-")[:2])
    else:
        ry, rm = months[-1]["year"], months[-1]["month"]
    ref = next((r for r in months if (r["year"], r["month"]) == (ry, rm)), None)
    if ref is None:
        raise HTTPException(400, f"ref_month {ry}-{rm:02d} is outside the covered range")

    others = {r["year"]: r["count"] for r in months if r["month"] == rm and r["year"] != ry}
    full_month = dict(others)
    period, compared = MONTHS[rm - 1], f"all of {MONTHS[rm - 1]} in other years"
    same_days = False
    if ref["partial"]:
        mtd = month_to_date(q, aoi(q), method, rm, ref["through_day"])
        if mtd is not None:
            def pick(y: int) -> float:
                v = mtd.get(y, {})
                if q.harmonized:
                    return v.get("count", 0.0)
                raw_viirs = q.unit == "viirs" and viirs_cov.get((y, rm), False)
                return v.get("raw_viirs" if raw_viirs else "raw_modis", 0.0)
            others = {y: pick(y) for y in others}
            period = f"{MONTHS[rm - 1]} 1–{ref['through_day']}"
            compared = f"{period} in other years"
            same_days = True
    same = np.array(list(others.values()), dtype=float)
    med = float(np.median(same)) if len(same) else 0.0
    p25, p75 = (float(np.percentile(same, 25)), float(np.percentile(same, 75))) if len(same) else (0.0, 0.0)
    mad = 1.4826 * float(np.median(np.abs(same - med))) if len(same) else 0.0
    spread = mad or (p75 - p25) / 1.349 or max(1.0, math.sqrt(med))
    z = (ref["count"] - med) / spread
    pct = 100 * (float((same < ref["count"]).sum()) + 0.5 * float((same == ref["count"]).sum())) / max(len(same), 1)
    rank = int((same > ref["count"]).sum()) + 1  # 1 = most fire of all years
    n_all = len(same) + 1
    record = None
    if len(same) >= 5 and rank == 1:
        record = f"most fire for {period} in {n_all} years"
    elif len(same) >= 5 and rank == n_all:
        record = f"least fire for {period} in {n_all} years"
    this_month = {
        "year": ry, "month": rm,
        "label": f"{MONTHS[rm - 1]} {ry}" + (f" (1–{ref['through_day']})" if ref["partial"] else ""),
        "partial": ref["partial"], "through_day": ref["through_day"], "compared_with": compared,
        "preliminary": ref["preliminary"],
        "count": ref["count"], "lo": ref["lo"], "hi": ref["hi"], "flags": ref["flags"],
        "days_covered": ref["through_day"] if ref["partial"] else _cal.monthrange(ry, rm)[1],
        "median": round(med, 1), "p25": round(p25, 1), "p75": round(p75, 1),
        "pct_diff": round(100 * (ref["count"] - med) / med, 1) if med else 0.0,
        # robust z: (count - median) / (1.4826 x MAD) over the same month (same days) of other years
        "anomaly": round(z, 2), "anomaly_label": anomaly_label(pct), "z": round(z, 2),
        "percentile": round(pct, 1), "rank": rank, "of_years": n_all, "record": record, "n_years": int(len(same)),
        "full_month_median": round(float(np.median(list(full_month.values()))), 1) if full_month else None,
        "same_days_past": ([{"year": y, "count": round(c, 1)} for y, c in sorted(others.items())]
                           if same_days else None),
    }

    # fire-season climatology from full years only
    years = sorted({r["year"] for r in months})
    full = [y for y in years if sum(1 for r in months if r["year"] == y) == 12]
    clim = []
    for m in range(1, 13):
        v = np.array([r["count"] for r in months if r["month"] == m and r["year"] in full])
        clim.append({"month": m, "name": MONTHS[m - 1],
                     "mean": round(float(v.mean()), 1) if len(v) else 0.0,
                     "median": round(float(np.median(v)), 1) if len(v) else 0.0,
                     "p75": round(float(np.percentile(v, 75)), 1) if len(v) else 0.0})
    total = sum(c["mean"] for c in clim) or 1.0
    for c in clim:
        c["share"] = round(100 * c["mean"] / total, 1)
        ratio = c["mean"] / (total / 12)
        c["level"] = "peak" if ratio >= 1.5 else "elevated" if ratio >= 1.0 else "low"

    # busiest months, season and what's coming: median of every complete month in the range
    # (not the current one), as the app's contract defines them
    done = [r for r in months[:-1] if not r["partial"]]
    med_by_month = {mo: float(np.median(v)) for mo in range(1, 13)
                    if (v := [r["count"] for r in done if r["month"] == mo])}
    busiest_months = [{"month": mo, "name": MONTHS[mo - 1], "median": round(med_by_month[mo], 1)}
                      for mo in sorted(med_by_month, key=med_by_month.get, reverse=True)[:3]]
    season, critical_next = None, []
    if med_by_month:
        peak = max(med_by_month, key=med_by_month.get)
        busy = {mo for mo, v in med_by_month.items() if v >= BUSY_SHARE * med_by_month[peak]}
        start = end = peak
        while (start - 2) % 12 + 1 in busy and (start - 2) % 12 + 1 != peak:
            start = (start - 2) % 12 + 1
        while end % 12 + 1 in busy and end % 12 + 1 != peak:
            end = end % 12 + 1
        season = {"start_month": start, "peak_month": peak, "end_month": end,
                  "text": f"{MONTHS[start - 1]}–{MONTHS[end - 1]}, peak {MONTHS[peak - 1]}" if start != end
                  else f"{MONTHS[peak - 1]}"}
        for i in range(1, LOOKAHEAD + 1):
            mo = (rm - 1 + i) % 12 + 1
            if mo in busy:
                critical_next.append({"year": ry + (rm - 1 + i) // 12, "month": mo, "name": MONTHS[mo - 1],
                                      "median": round(med_by_month[mo], 1),
                                      "note": "peak month" if mo == peak else "historically busy"})

    annual = [{"year": y, "count": round(sum(r["count"] for r in months if r["year"] == y), 1)} for y in full]
    busiest_years = [{"year": a["year"], "total": a["count"]}
                     for a in sorted(annual, key=lambda a: -a["count"])[:3]]

    trend = None
    if len(annual) >= 3:
        yrs = np.array([a["year"] for a in annual], float)
        cnt = np.array([a["count"] for a in annual], float)
        slope = stats.theilslopes(cnt, yrs)[0]
        tau, p = stats.kendalltau(yrs, cnt)
        mean = cnt.mean() or 1.0
        trend = {"sen_slope_per_year": round(float(slope), 1), "per_year": round(float(slope), 1),
                 "pct_per_decade": round(float(1000 * slope / mean), 1),
                 "p_value": round(float(p), 3), "significant": bool(p < 0.05),
                 "direction": "rising" if slope > 0 else "falling" if slope < 0 else "flat",
                 "years": [int(yrs[0]), int(yrs[-1])]}

    total_all = sum(r["count"] for r in months) or 1.0
    share = sum(r["industrial"] or 0.0 for r in months) / total_all
    industrial = {"share": round(share, 3), "warning": None}
    if share >= INDUSTRIAL_WARN and not q.hide_industrial:
        industrial["warning"] = (f"{100 * share:.0f}% of this area's hotspots are industrial or persistent sources "
                                 "(gas flares, plants, volcanoes). MODIS missed most small ones, so their numbers "
                                 "before 2012 can't be fully reconstructed: tick 'hide industrial' for a like-for-like record.")

    return {"unit": q.unit, "harmonized": q.harmonized, "method": method, "this_month": this_month,
            "industrial": industrial, "busiest_months": busiest_months, "busiest_years": busiest_years,
            "season": season, "critical_next": critical_next, "trend": trend,
            "season_timing": season_timing(months, full), "climatology": clim, "annual": annual,
            "reliability": reliability(q, months)}


SWITCH_OK, SWITCH_FAIR = 0.08, 0.15  # leftover thresholds for the reliability verdict
SWITCH_MIN = 200                     # MODIS fires needed in each 4-year window


@functools.lru_cache(maxsize=1)
def _countries():
    reg = pd.read_parquet(REGIONS, columns=["kind", "subregion", "geojson"])
    reg = reg[reg.kind == "country"].reset_index(drop=True)
    geoms = [shape(json.loads(g)) for g in reg.geojson]
    return shapely.STRtree(geoms), reg.subregion.to_numpy(), geoms


def subregion_of(geom) -> str | None:
    if not REGIONS.exists():
        return None
    tree, sub, _ = _countries()
    pt = geom.representative_point()
    hit = tree.query(pt, predicate="within")
    if len(hit) == 0:
        hit = tree.query_nearest(pt, max_distance=1.0)
    return str(sub[hit[0]]) if len(hit) else None


def reliability(q: Query, months: list[dict]) -> dict | None:
    """How well this area's record holds across the 2012 MODIS -> VIIRS switch: the harmonized change from
    2008-11 to 2012-15 against raw MODIS's own change (MODIS flew straight through it). In the overlap years
    MODIS's own change is itself within 8 points of VIIRS's real change in 3 of 4 areas of this size, so
    leftovers up to that are normal (docs/experiments_2012.md)."""
    if not q.harmonized or q.unit != "viirs":
        return None
    have = {(r["year"], r["month"]): r for r in months}
    pre = [have.get((y, m)) for y in range(2008, 2012) for m in range(1, 13)]
    post = [have.get((y, m)) for y in range(2012, 2016) for m in range(1, 13)]
    if None in pre or None in post:
        return None  # the request doesn't cover 2008-2015
    tot = lambda rows, k: sum(r[k] or 0 for r in rows)  # noqa: E731
    m_pre, m_post, h_pre, h_post = tot(pre, "raw_modis"), tot(post, "raw_modis"), tot(pre, "count"), tot(post, "count")
    geom = aoi(q)
    region = subregion_of(geom)
    out = {"region": region, "leftover_2012": None, "verdict": "too few fires", "modis_fires": [int(m_pre), int(m_post)]}
    if min(m_pre, m_post) < SWITCH_MIN or h_pre <= 0:
        out["note"] = f"Fewer than {SWITCH_MIN} MODIS fires in 2008-11 or 2012-15, so the 2012 switch can't be checked here."
        return out
    left = (h_post / h_pre) / (m_post / m_pre) - 1
    out["leftover_2012"] = round(left, 3)
    out["verdict"] = "good" if abs(left) <= SWITCH_OK else "fair" if abs(left) <= SWITCH_FAIR else "caution"
    out["note"] = (f"Across the 2012 switch to VIIRS, this record changes {100 * left:+.0f}% more than MODIS's own count "
                   + ("(inside the normal noise of that comparison)." if out["verdict"] == "good" else
                      "(a little more than the usual noise)." if out["verdict"] == "fair" else
                      "(read changes across 2012 here with care; industrial sites or a shift in fire timing are the usual reasons)."))
    return out


CSV_COLS = """,
    h.lat AS latitude, h.lon AS longitude, h.acq_time, h.dn AS daynight, h.frp,
    h.bt4 AS bright_fire, h.bt5 AS bright_bg, h.scan, h.track, h.conf AS confidence,
    h.conf_pct AS modis_confidence_pct, h.type, p.iy IS NOT NULL AS persistent, h.version, h.country"""


CSV_CONTRACT = """
    SELECT acq_date, lpad(CAST(acq_time AS VARCHAR), 4, '0') AS acq_time, latitude, longitude,
           CASE sat WHEN 'NOAA20' THEN 'N20' WHEN 'SNPP' THEN 'N' WHEN 'NOAA21' THEN 'N21' ELSE sat END AS satellite,
           CASE WHEN sat IN ('Terra', 'Aqua') THEN 'MODIS' ELSE 'VIIRS' END AS instrument,
           frp, CASE WHEN sat IN ('Terra', 'Aqua') THEN CAST(modis_confidence_pct AS VARCHAR) ELSE confidence END
               AS confidence,
           daynight, type, round(w, 4) AS weight"""
CSV_FULL = f"""
    SELECT latitude, longitude, acq_date, acq_time, sat AS satellite, daynight, frp,
           bright_fire, bright_bg, scan, track, confidence, modis_confidence_pct, type, persistent, version, country,
           round(w, 4) AS weight,
           round(w * exp(-{Z90} * s), 4) AS weight_lo,
           round(w * exp({Z90} * s), 4) AS weight_hi,
           w > 0 AS counted"""


@app.post("/api/hotspots.csv")
def hotspots_csv(q: Query, full: bool = False):
    """Every hotspot in the area. Columns: the app's 11 (acq_date ... weight), or with ?full=true
    also brightness, pixel size, persistent flag, version, country and the weight's 90% band."""
    if not HAVE_POINTS:
        raise HTTPException(503, "this server runs in grid-only mode; hotspot CSV needs the full data")
    resolve_end(q)
    cur = db()
    geom = aoi(q)
    if GRID.exists() and geom.area > 25:
        est = monthly_grid(q, geom, cur)[["raw_modis", "raw_snpp", "raw_noaa20"]].to_numpy().sum()
        if est > CSV_MAX_ROWS:
            raise HTTPException(413, f"about {int(est):,} hotspots match; the CSV limit is {CSV_MAX_ROWS:,}. "
                                     "Draw a smaller area or pick a shorter date range.")
    inner, params = rows_sql(q, geom, cur, CSV_COLS)
    reader = cur.execute(f"""
        {CSV_FULL if full else CSV_CONTRACT}
        FROM ({inner}) ORDER BY acq_date, acq_time
    """, params).fetch_record_batch(100_000)

    def stream():
        header = True
        for batch in reader:
            buf = io.BytesIO()
            pacsv.write_csv(batch, buf, pacsv.WriteOptions(include_header=header, quoting_style="none",
                                                           quoting_header="none"))
            header = False
            yield buf.getvalue()

    name = f"fireledger_{q.unit}_{q.start}_{q.end}.csv"
    return StreamingResponse(stream(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})
