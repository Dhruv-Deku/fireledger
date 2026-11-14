"""FireLedger API: harmonized hotspot calendar for any area the user draws.

    uvicorn api.main:app --host 0.0.0.0 --port 8000

All three POST endpoints take the same body (see Query). Every hotspot row gets a
harmonization weight in the requested unit, so the total for any polygon and date
range is just the sum of weights of the hotspots inside it. See pipeline/calibrate.py
for where the weights come from.
"""
from __future__ import annotations

import calendar as _cal
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
import shapely
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from scipy import stats
from shapely.geometry import box, shape
from shapely.ops import unary_union

from api.weights import JOINS, RAW_VIIRS_SQL, STATIC_SQL, Z90, create_pers, weight_sql

ROOT = Path(__file__).resolve().parents[1]
HOT = (ROOT / "data" / "hotspots" / "*.parquet").as_posix()
DER = ROOT / "data" / "derived"
GRID = DER / "grid.parquet"
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

app = FastAPI(title="FireLedger API", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_con: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()


def db() -> duckdb.DuckDBPyConnection:
    global _con
    with _lock:
        if _con is None:
            con = duckdb.connect()
            con.execute("SET memory_limit = '3GB'")
            if HAVE_POINTS:
                con.execute(f"CREATE VIEW hot AS SELECT * FROM read_parquet('{HOT}')")
            con.execute(f"CREATE TABLE avail AS SELECT * FROM read_parquet('{(DER / 'avail.parquet').as_posix()}')")
            con.execute(f"CREATE TABLE calib AS SELECT * FROM read_parquet('{(DER / 'calib.parquet').as_posix()}')")
            create_pers(con, DER)
            if REGIONS.exists():
                con.execute(f"CREATE TABLE regions AS SELECT * FROM read_parquet('{REGIONS.as_posix()}')")
            if GRID.exists():
                con.execute(f"""CREATE VIEW grid AS SELECT *, (gy + 0.5) / 4.0 AS lat, (gx + 0.5) / 4.0 AS lon
                                FROM read_parquet('{GRID.as_posix()}')""")
            _con = con
        return _con.cursor()


# ---------------------------------------------------------------- request

class Query(BaseModel):
    geometry: dict | None = Field(None, description="GeoJSON Polygon/MultiPolygon, Feature or FeatureCollection")
    region: str | None = Field(None, description="preset id from /api/regions (e.g. IND, ARG); used when geometry is empty")
    start: str = Field("2000-11", description="YYYY-MM or YYYY-MM-DD")
    end: str = Field("2025-12", description="YYYY-MM or YYYY-MM-DD")
    unit: Literal["viirs", "modis"] = "viirs"
    daynight: Literal["D", "N", "both"] = "both"
    hide_industrial: bool = False
    ref_month: str | None = Field(None, description="summary only: YYYY-MM to compare with normal; default = last month in range")
    precision: Literal["auto", "exact", "grid"] = Field(
        "auto", description="exact = every hotspot point; grid = 0.25 deg cells (fast for continents); auto picks by area")


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
        raise HTTPException(422, "geometry must be a non-empty Polygon or MultiPolygon")
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
    raise HTTPException(422, "send either geometry or region")


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


def rows_sql(q: Query, geom, cur, extra_cols: str = "") -> tuple[str, list]:
    """SQL selecting every hotspot in the AOI with its weight w and band spread s."""
    where, params = filters(q, geom, True)
    where.append("h.acq_date BETWEEN ? AND ?")
    params += [parse_day(q.start, False), parse_day(q.end, True)]
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


def monthly_grid(q: Query, geom, cur):
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
        FROM grid h WHERE {' AND '.join(where)} GROUP BY ALL
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


def monthly(q: Query) -> tuple[list[dict], str]:
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

    out = []
    y, m = d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        has_cov = (y, m) in cov.index and (cov.loc[(y, m), "modis"] if q.unit == "modis"
                                            else bool(cov.loc[(y, m), ["modis", "snpp", "noaa20"]].any()))
        r = df.loc[(y, m)] if (y, m) in df.index else None
        count = float(r["count"]) if r is not None else 0.0
        if not has_cov:
            out.append({"year": y, "month": m, "count": None, "lo": None, "hi": None,
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
            out.append({
                "year": y, "month": m,
                "drift": q.unit == "modis" and y >= DRIFT_FROM,
                "count": round(count, 1),
                "lo": round(lo, 1),
                "hi": round(hi, 1),
                "raw_modis": int(r["raw_modis"]) if r is not None else 0,
                "raw_viirs": int(r["raw_viirs"]) if r is not None else 0,
                "raw_snpp": int(r["raw_snpp"]) if r is not None else 0,
                "raw_noaa20": int(r["raw_noaa20"]) if r is not None else 0,
                "industrial": round(float(np.nan_to_num(r["w_industrial"])), 1) if r is not None else 0.0,
                "source": source,
            })
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out, method


# ---------------------------------------------------------------- endpoints

@app.get("/api/health")
def health():
    cur = db()
    lo, hi = cur.execute("SELECT min(d), max(d) FROM avail").fetchone()
    return {"ok": True, "coverage": {"start": str(lo), "end": str(hi)},
            "mode": "full" if HAVE_POINTS else "grid-only"}


@app.get("/api/proof")
def proof():
    """Validation results for the Method & Trust page (built by pipeline/validate.py)."""
    path = DER / "proof.json"
    if not path.exists():
        raise HTTPException(503, "proof not built yet: run pipeline/validate.py")
    return json.loads(path.read_text(encoding="utf-8"))


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
        raise HTTPException(422, "res must be 0.25, 0.5, 1 or 2 (degrees)")
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
    months, method = monthly(q)
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
    months, method = monthly(q)
    months = [r for r in months if r["count"] is not None]
    if not months:
        raise HTTPException(404, "no satellite coverage in this date range")

    if q.ref_month:
        ry, rm = (int(p) for p in q.ref_month.split("-")[:2])
    else:
        ry, rm = months[-1]["year"], months[-1]["month"]
    ref = next((r for r in months if (r["year"], r["month"]) == (ry, rm)), None)
    if ref is None:
        raise HTTPException(422, f"ref_month {ry}-{rm:02d} is outside the covered range")

    same = np.array([r["count"] for r in months if r["month"] == rm and r["year"] != ry])
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
        record = f"most fire for {MONTHS[rm - 1]} in {n_all} years"
    elif len(same) >= 5 and rank == n_all:
        record = f"least fire for {MONTHS[rm - 1]} in {n_all} years"
    this_month = {
        "year": ry, "month": rm, "label": f"{MONTHS[rm - 1]} {ry}",
        "count": ref["count"], "lo": ref["lo"], "hi": ref["hi"],
        "median": round(med, 1), "p25": round(p25, 1), "p75": round(p75, 1),
        "pct_diff": round(100 * (ref["count"] - med) / med, 1) if med else None,
        "anomaly": anomaly_label(pct), "percentile": round(pct, 1), "z": round(z, 2),
        "rank": rank, "of_years": n_all, "record": record, "n_years": int(len(same)),
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
    busiest_months = sorted(clim, key=lambda c: -c["mean"])[:3]

    annual = [{"year": y, "count": round(sum(r["count"] for r in months if r["year"] == y), 1)} for y in full]
    busiest_years = sorted(annual, key=lambda a: -a["count"])[:5]

    critical_next = []
    for i in range(1, 4):
        mm = (rm - 1 + i) % 12 + 1
        c = clim[mm - 1]
        critical_next.append({"year": ry + (rm - 1 + i) // 12, "month": mm, "name": c["name"],
                              "typical": c["median"], "p75": c["p75"], "level": c["level"]})

    trend = None
    if len(annual) >= 8:
        yrs = np.array([a["year"] for a in annual], float)
        cnt = np.array([a["count"] for a in annual], float)
        slope = stats.theilslopes(cnt, yrs)[0]
        tau, p = stats.kendalltau(yrs, cnt)
        mean = cnt.mean() or 1.0
        trend = {"per_year": round(float(slope), 1), "pct_per_decade": round(float(1000 * slope / mean), 1),
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

    return {"unit": q.unit, "method": method, "this_month": this_month, "industrial": industrial,
            "busiest_months": busiest_months,
            "busiest_years": busiest_years, "critical_next": critical_next,
            "season": clim, "season_timing": season_timing(months, full), "annual": annual, "trend": trend}


CSV_COLS = """,
    h.lat AS latitude, h.lon AS longitude, h.acq_time, h.dn AS daynight, h.frp,
    h.bt4 AS bright_fire, h.bt5 AS bright_bg, h.scan, h.track, h.conf AS confidence,
    h.conf_pct AS modis_confidence_pct, h.type, p.iy IS NOT NULL AS persistent, h.version, h.country"""


@app.post("/api/hotspots.csv")
def hotspots_csv(q: Query):
    if not HAVE_POINTS:
        raise HTTPException(503, "this server runs in grid-only mode; hotspot CSV needs the full data")
    cur = db()
    geom = aoi(q)
    if GRID.exists() and geom.area > 25:
        est = monthly_grid(q, geom, cur)[["raw_modis", "raw_snpp", "raw_noaa20"]].to_numpy().sum()
        if est > CSV_MAX_ROWS:
            raise HTTPException(413, f"about {int(est):,} hotspots match; the CSV limit is {CSV_MAX_ROWS:,}. "
                                     "Draw a smaller area or pick a shorter date range.")
    inner, params = rows_sql(q, geom, cur, CSV_COLS)
    reader = cur.execute(f"""
        SELECT latitude, longitude, acq_date, acq_time, sat AS satellite, daynight, frp,
               bright_fire, bright_bg, scan, track, confidence, modis_confidence_pct, type, persistent, version, country,
               round(w, 4) AS weight,
               round(w * exp(-{Z90} * s), 4) AS weight_lo,
               round(w * exp({Z90} * s), 4) AS weight_hi,
               w > 0 AS counted
        FROM ({inner}) ORDER BY acq_date, acq_time
    """, params).fetch_record_batch(100_000)

    def stream():
        header = True
        for batch in reader:
            buf = io.BytesIO()
            pacsv.write_csv(batch, buf, pacsv.WriteOptions(include_header=header))
            header = False
            yield buf.getvalue()

    name = f"fireledger_{q.unit}_{q.start}_{q.end}.csv"
    return StreamingResponse(stream(), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})
