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
from collections import OrderedDict
import io
import json
import math
import os
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
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
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
GRID_LAYER = DER / "grid_layer.parquet"    # grid.parquet sorted by month, for the map layer
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
app.add_middleware(GZipMiddleware, minimum_size=2000)  # map frames and calendars shrink ~5x


@app.exception_handler(RequestValidationError)
async def bad_body(request: Request, exc: RequestValidationError):
    """A body that doesn't fit Query is a client error: 400, as the app expects."""
    return JSONResponse(status_code=400, content={"detail": exc.errors()})

_con: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()
# answers are remembered until the API restarts (update_data.bat restarts it with new data), so a preset
# or a map frame someone opened before comes back at once; numbers are exactly what was computed
_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
CACHE_MAX = {"calendar": 600, "summary": 600, "grid": 120, "monthly": 60}
SNAPSHOT = ROOT / "data" / "snapshot"  # pipeline/snapshot.py: the full API's preset answers
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
            con.execute(f"SET memory_limit = '{os.environ.get('FIRELEDGER_DUCKDB_MEMORY', '3GB')}'")
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
            if GRID_LAYER.exists():
                parts = [f"SELECT gy, gx, y, m, dn, static, v, md FROM read_parquet('{GRID_LAYER.as_posix()}')"]
                if GRID_RECENT.exists():
                    parts.append(f"SELECT gy, gx, y, m, dn, static, v, md FROM read_parquet('{GRID_RECENT.as_posix()}')")
                con.execute(f"CREATE VIEW layer AS {' UNION ALL '.join(parts)}")
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
                try:  # for a grid-only copy of the API (no hotspot files) to know where NRT starts
                    (DER / "nrt_from.json").write_text(json.dumps({k: str(v) for k, v in NRT_FROM.items()}))
                except OSError:
                    pass
            elif (DER / "nrt_from.json").exists():
                NRT_FROM = {k: date.fromisoformat(v) for k, v in json.loads((DER / "nrt_from.json").read_text()).items()}
            _con = con
        return _con.cursor()


# ---------------------------------------------------------------- answer cache

def cached(kind: str, key: str | None, compute):
    if key is None:
        return compute()
    k = (kind, key)
    with _cache_lock:
        if k in _cache:
            _cache.move_to_end(k)
            return _cache[k]
    val = compute()
    with _cache_lock:
        _cache[k] = val
        mine = [x for x in _cache if x[0] == kind]
        for old in mine[:max(0, len(mine) - CACHE_MAX[kind])]:
            del _cache[old]
    return val


def query_key(q) -> str | None:
    """Same answer, same key: dates normalised, an end after the last day with data = that day."""
    try:
        db()
        d = q.model_dump()
        d["start"] = parse_day(q.start, False).isoformat()
        end = parse_day(q.end, True) if q.end else LAST_DAY
        d["end"] = min(end, LAST_DAY).isoformat()
        return json.dumps(d, sort_keys=True, default=str)
    except Exception:  # a bad body: let the endpoint raise its own 400
        return None


def load_snapshot() -> int:
    """Grid-only copy (no hotspot files): load the presets' exact answers that the full API saved,
    so presets match the full API to the digit and only newly drawn areas come from the grid."""
    man = SNAPSHOT / "manifest.json"
    if HAVE_POINTS or not man.exists():
        return 0
    m = json.loads(man.read_text(encoding="utf-8"))
    geo = {p["id"]: p["geometry"] for p in preset_list()}
    n = 0
    for pid in m["presets"]:
        for v in m["variants"]:
            unit, dn, h, ind = v.split("_")
            q = Query(geometry=geo[pid], start=m["request"]["start"], unit=unit, daynight=dn,
                      harmonized=h == "harmonized", hide_industrial=ind == "noind")
            key = query_key(q)
            for kind in ("calendar", "summary"):
                f = SNAPSHOT / kind / pid / f"{v}.json"
                if key and f.exists():
                    with _cache_lock:
                        _cache[(kind, key)] = json.loads(f.read_text(encoding="utf-8"))
                    n += 1
    print(f"snapshot: {n} preset answers loaded", flush=True)
    return n


def warm_presets() -> None:
    """Work out the presets in the background after start-up, the way the app asks for them."""
    load_snapshot()
    for p in preset_list():
        for start, harmonized in [("2000-11-01", True), ("2000-11-01", False)]:
            q = dict(geometry=p["geometry"], start=start, unit="viirs", daynight="both",
                     hide_industrial=False, harmonized=harmonized)
            try:
                calendar(Query(**q))
                summary(Query(**q))
            except Exception:
                pass


@app.on_event("startup")
def _warm() -> None:
    threading.Thread(target=warm_presets, daemon=True).start()


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
    """The monthly table behind both /api/calendar and /api/summary; computed once per request body."""
    resolve_end(q)  # callers read q.end afterwards, even when the table comes from the cache
    months, method, cov = cached("monthly", query_key(q), lambda: _monthly(q))
    return [dict(r) for r in months], method, cov  # callers may edit rows; the cached copy stays clean


VIIRS_ERA = 2012  # S-NPP data starts 20 Jan 2012


def modis_only_points(q: Query, geom, cur) -> pd.DataFrame:
    """MODIS detections (any type, same area and filters as the calendar) with no VIIRS detection within
    their own pixel, max(1 km, scan), on the same UTC day: fires only MODIS saw. Raw counts per month
    from 2012 on. Neighbours are searched in 0.05 deg cells, +-2 cells, then by great-circle distance."""
    inner, params = rows_sql(q, geom, cur, ", h.lat AS lat, h.lon AS lon, h.scan AS scan",
                             extra_where=[f"h.acq_date >= DATE '{VIIRS_ERA}-01-01'"])
    return cur.execute(f"""
        WITH r AS ({inner}),
        m AS (SELECT row_number() OVER () AS id, acq_date AS d, lat, lon, scan FROM r WHERE sat IN ('Terra', 'Aqua')),
        v AS (SELECT acq_date AS d, lat, lon, CAST(floor(lat * 20) AS INT) AS cy, CAST(floor(lon * 20) AS INT) AS cx
              FROM r WHERE sat NOT IN ('Terra', 'Aqua')),
        mk AS (SELECT id, d, lat, lon, scan, CAST(floor(lat * 20) AS INT) + dy AS cy, CAST(floor(lon * 20) AS INT) + dx AS cx
               FROM m, range(-2, 3) t1(dy), range(-2, 3) t2(dx)),
        hit AS (SELECT DISTINCT mk.id FROM mk JOIN v ON v.d = mk.d AND v.cy = mk.cy AND v.cx = mk.cx
                WHERE 2 * 6371.0 * asin(sqrt(pow(sin(radians(v.lat - mk.lat) / 2), 2)
                      + cos(radians(mk.lat)) * cos(radians(v.lat)) * pow(sin(radians(v.lon - mk.lon) / 2), 2)))
                      <= greatest(1.0, coalesce(mk.scan, 1.0)))
        SELECT year(m.d) AS year, month(m.d) AS month, count(*) AS modis_only
        FROM m LEFT JOIN hit ON hit.id = m.id WHERE hit.id IS NULL GROUP BY ALL
    """, params).df()


def _monthly(q: Query) -> tuple[list[dict], str, dict]:
    """Calendar months, the method used, and which months VIIRS was observing (for the raw view)."""
    resolve_end(q)
    cur = db()
    geom = aoi(q)
    method = "grid" if use_grid(q, geom) else "exact"
    df = (monthly_grid if method == "grid" else monthly_points)(q, geom, cur)
    df = df.astype({"year": int, "month": int}).set_index(["year", "month"])
    mo = None  # fires only MODIS saw: needs single detections, so only for areas answered from points
    if method == "exact":
        mo = (modis_only_points(q, geom, cur).astype({"year": int, "month": int})
              .set_index(["year", "month"]).modis_only)
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
                        "modis_only": None, "source": "no data",
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
                "modis_only": (int(mo.get((y, m), 0)) if mo is not None and y >= VIIRS_ERA else None),
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

@app.get("/", include_in_schema=False, response_class=HTMLResponse)
def home(request: Request):
    """A plain front page for people who open the bare link."""
    db()
    host = request.headers.get("host", "localhost:8000")
    base = ("http://" if host.startswith(("localhost", "127.", "10.", "192.168.")) else "https://") + host
    mode = "full (every hotspot)" if HAVE_POINTS else "grid-only (0.25° cells; the 9 presets are exact)"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>FireLedger API</title>
<style>body{{font:16px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;color:#1d1d1f;background:#fbfaf7}}
h1{{font-size:24px;margin:0 0 4px}}p.sub{{color:#666;margin:0 0 24px}}code,pre{{font:13px ui-monospace,Consolas,monospace;background:#f0eee8;border-radius:4px}}
code{{padding:1px 5px}}pre{{padding:12px;overflow-x:auto}}a{{color:#9a3412}}li{{margin:4px 0}}
@media (prefers-color-scheme:dark){{body{{background:#161514;color:#e8e6e1}}p.sub{{color:#9a968f}}code,pre{{background:#24221f}}a{{color:#fb923c}}}}</style>
</head><body>
<h1>FireLedger API</h1>
<p class="sub">One fire record from MODIS and VIIRS, Nov 2000 to {LAST_DAY:%d %b %Y}. NASA Space Apps Challenge 2026,
"Harmonization of MODIS and VIIRS Hot Spots".</p>
<p>NASA FIRMS hotspots from MODIS (2000&ndash;) and VIIRS (2012&ndash;), converted into one VIIRS-equivalent count,
so the record doesn't jump when VIIRS arrives in 2012. Ask for any area: monthly counts with a likely range,
how this month compares with other years, the fire season, and how well the 2012 switch holds there.</p>
<ul>
<li><a href="/docs">/docs</a>: interactive documentation, try every endpoint in the browser</li>
<li><a href="/api/health">/api/health</a>: data coverage and mode (this server: {mode})</li>
<li><a href="/api/presets">/api/presets</a>: the 9 preset areas (Punjab, Patagonia, California, ...)</li>
<li><a href="/api/proof">/api/proof</a>: how accurate it is: held-out tests, the 2012 jump, burned area, India's official counts</li>
<li><a href="/api/grid?year=2024&amp;month=9&amp;res=1">/api/grid?year=2024&amp;month=9&amp;res=1</a>: a month's map layer</li>
</ul>
<p>Monthly calendar for an area (any GeoJSON polygon, or a <code>region</code> id from <code>/api/regions?q=punjab</code>):</p>
<pre>curl -X POST {base}/api/calendar -H "Content-Type: application/json" \
  -d '{{"region": "IND-3249", "start": "2010-01"}}'</pre>
<p>Data: NASA FIRMS (MODIS C6.1, VIIRS 375 m S-NPP and NOAA-20), harmonized by FireLedger.
Code and method: <a href="https://github.com/Dhruv-Deku/fireledger">github.com/Dhruv-Deku/fireledger</a></p>
</body></html>"""


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
    key = json.dumps([year, month, unit, res, daynight, hide_industrial])
    return cached("grid", key, lambda: _grid_layer(year, month, unit, res, daynight, hide_industrial))


def _grid_layer(year, month, unit, res, daynight, hide_industrial):
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
        FROM {"layer" if GRID_LAYER.exists() else "grid"} WHERE {' AND '.join(where)} GROUP BY 1, 2 HAVING sum({col}) > 0
    """, params).fetchall()
    return {"year": year, "month": month, "unit": unit, "res": res,
            "max": max((r[2] for r in rows), default=0), "cells": [list(r) for r in rows]}


@app.post("/api/calendar")
def calendar(q: Query):
    return cached("calendar", query_key(q), lambda: _calendar(q))


def _calendar(q: Query):
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
    return cached("summary", query_key(q), lambda: _summary(q))


def _summary(q: Query):
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
        "modis_only": ref.get("modis_only"), "industrial": ref.get("industrial"),
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

    # busiest full years without a blind-spot (B) month, whose totals lean hardest on the conversion
    blind = {r["year"] for r in months if "B" in (r["flags"] or "")}
    busiest_years_no_blind = [{"year": a["year"], "total": a["count"]}
                              for a in sorted((a for a in annual if a["year"] not in blind),
                                              key=lambda a: -a["count"])[:3]]

    trend = trend_of(annual, 3)
    # the same trend on full years from 2012 on only: VIIRS-observed, no MODIS conversion involved
    trend_viirs = trend_of([a for a in annual if a["year"] >= VIIRS_ERA], 6) if q.harmonized else None

    total_all = sum(r["count"] for r in months) or 1.0
    share = sum(r["industrial"] or 0.0 for r in months) / total_all
    industrial = {"share": round(share, 3), "warning": None}
    if share >= INDUSTRIAL_WARN and not q.hide_industrial:
        industrial["warning"] = (f"{100 * share:.0f}% of this area's hotspots are industrial or persistent sources "
                                 "(gas flares, plants, volcanoes). MODIS saw few of the small ones, so before 2012 they are "
                                 "rebuilt from the few it saw and are rougher than the rest: tick 'hide industrial' for "
                                 "fires only.")

    return {"unit": q.unit, "harmonized": q.harmonized, "method": method, "this_month": this_month,
            "industrial": industrial, "busiest_months": busiest_months, "busiest_years": busiest_years,
            "busiest_years_no_blind": busiest_years_no_blind,
            "season": season, "critical_next": critical_next, "trend": trend, "trend_viirs": trend_viirs,
            "season_timing": season_timing(months, full), "climatology": clim, "annual": annual,
            "reliability": reliability(q, months)}


def trend_of(annual: list[dict], min_years: int) -> dict | None:
    """Sen's slope of yearly totals with a two-sided Mann-Kendall p-value; None if too few years."""
    if len(annual) < min_years:
        return None
    yrs = np.array([a["year"] for a in annual], float)
    cnt = np.array([a["count"] for a in annual], float)
    slope = stats.theilslopes(cnt, yrs)[0]
    tau, p = stats.kendalltau(yrs, cnt)
    mean = cnt.mean() or 1.0
    return {"sen_slope_per_year": round(float(slope), 1), "per_year": round(float(slope), 1),
            "pct_per_decade": round(float(1000 * slope / mean), 1),
            "p_value": round(float(p), 3), "significant": bool(p < 0.05),
            "direction": "rising" if slope > 0 else "falling" if slope < 0 else "flat",
            "years": [int(yrs[0]), int(yrs[-1])]}


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
    out = {"region": region, "leftover_2012": None, "verdict": "too few fires", "modis_fires": [int(m_pre), int(m_post)],
           "leftover_2012_range": None, "noise_2012": None}
    if min(m_pre, m_post) < SWITCH_MIN or h_pre <= 0:
        out["note"] = f"Fewer than {SWITCH_MIN} MODIS fires in 2008-11 or 2012-15, so the 2012 switch can't be checked here."
        return out
    left = (h_post / h_pre) / (m_post / m_pre) - 1
    out["leftover_2012"] = round(left, 3)
    # 90% range: resample whole years before (2008-11) and after (2012-15), 1,000 draws, fixed seed
    def yr(y, k):
        return sum(r[k] or 0 for r in months if r["year"] == y)
    H = np.array([[yr(y, "count") for y in range(2008, 2012)], [yr(y, "count") for y in range(2012, 2016)]])
    M = np.array([[yr(y, "raw_modis") for y in range(2008, 2012)], [yr(y, "raw_modis") for y in range(2012, 2016)]])
    rng = np.random.default_rng(2012)
    i, j = rng.integers(0, 4, (1000, 4)), rng.integers(0, 4, (1000, 4))
    with np.errstate(divide="ignore", invalid="ignore"):
        draws = (H[1][j].sum(1) / H[0][i].sum(1)) / (M[1][j].sum(1) / M[0][i].sum(1)) - 1
    draws = draws[np.isfinite(draws)]
    if len(draws):
        lo, hi = np.percentile(draws, [5, 95])
        out["leftover_2012_range"] = [round(float(min(lo, left)), 3), round(float(max(hi, left)), 3)]
    out["noise_2012"] = switch_noise(q, geom)
    out["verdict"] = "good" if abs(left) <= SWITCH_OK else "fair" if abs(left) <= SWITCH_FAIR else "caution"
    out["note"] = (f"Across the 2012 switch to VIIRS, this record changes {100 * left:+.0f}% more than MODIS's own count "
                   + ("(inside the normal noise of that comparison)." if out["verdict"] == "good" else
                      "(a little more than the usual noise)." if out["verdict"] == "fair" else
                      "(read changes across 2012 here with care; industrial sites or a shift in fire timing are the usual reasons)."))
    return out


NOISE_POINTS_MAX_AREA = 1500.0  # deg^2; bigger areas use the grid (all days) for noise_2012


def switch_noise(q: Query, geom) -> float | None:
    """The 2012 check run inside the overlap years, where VIIRS's real change is known: |log((MODIS change)
    / (S-NPP change))| from 2012-15 to 2016-19, vegetation fires on days Terra, Aqua and S-NPP all
    observed. How noisy MODIS's own change is as a yardstick in this area."""
    if parse_day(q.start, False) > date(2012, 1, 1) or parse_day(q.end, True) < date(2019, 12, 31):
        return None
    cur = db()
    if HAVE_POINTS and geom.area <= NOISE_POINTS_MAX_AREA:
        inner, params = rows_sql(q, geom, cur, ", a.has_terra AS ht, a.has_aqua AS ha, a.has_snpp AS hs",
                                 extra_where=["h.acq_date BETWEEN DATE '2012-01-01' AND DATE '2019-12-31'"])
        df = cur.execute(f"""SELECT year(acq_date) AS y, count(*) FILTER (WHERE sat IN ('Terra', 'Aqua')) AS m,
                                    count(*) FILTER (WHERE sat = 'SNPP') AS v
                             FROM ({inner}) WHERE NOT st AND ht AND ha AND hs GROUP BY ALL""", params).df()
    elif GRID.exists():
        q2 = q.model_copy(update={"start": "2012-01-01", "end": "2019-12-31"})
        where, params = filters(q2, geom, False)
        df = cur.execute(f"""SELECT h.y AS y, sum(raw_modis) AS m, sum(raw_snpp) AS v FROM grid h
                             WHERE {' AND '.join(where)} AND NOT h.static AND h.y BETWEEN 2012 AND 2019 GROUP BY ALL""",
                         params).df()
    else:
        return None
    t = df.set_index("y").reindex(range(2012, 2020), fill_value=0)
    a, b = t.loc[2012:2015].sum(), t.loc[2016:2019].sum()
    if min(a.m, a.v, b.m, b.v) <= 0:
        return None
    return round(float(abs(math.log((b.m / a.m) / (b.v / a.v)))), 3)


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
