"""Learn the MODIS <-> VIIRS conversion from the years the satellites overlap.

Reads  data/hotspots/*.parquet  (from ingest.py)
Writes data/derived/avail.parquet   which satellites were observing on each day
       data/derived/cellcounts.parquet  hotspot counts per 1 deg cell x year x month x day/night x satellite
       data/derived/calib.parquet   conversion factors per 1 deg cell x calendar month x day/night

Reference unit is NOAA-20 VIIRS ("VIIRS-equivalent"): S-NPP data ends in Nov 2026, so the
current month comes from NOAA-20 and history is expressed in NOAA-20 terms. MODIS is
linked through S-NPP (a two-step chain), because S-NPP overlaps the pre-drift MODIS years
(2012-2019) far longer than NOAA-20 does (2018-2019).

Factors (ratio of sums, counted only on days when both satellites were observing):
    k_s   NOAA-20 / S-NPP          2018-2025   S-NPP days without NOAA-20 (2012-2017, outages)
    k_ta  S-NPP / (Terra + Aqua)   2012-2019   pre-VIIRS days with both MODIS up (x k_s)
    k_t   S-NPP / Terra            2012-2019   days with only Terra (Nov 2000 - Jul 2002, outages)
    k_a   S-NPP / Aqua             2012-2021   days with only Aqua
    m_t   (Terra + Aqua) / Terra   2003-2011   MODIS-equivalent unit, Terra-only days
    m_a   (Terra + Aqua) / Aqua    2003-2011   MODIS-equivalent unit, Aqua-only days

Calibration windows stop before each MODIS orbit started drifting (Terra ~2020, Aqua ~2022),
so the factors describe the overpass times the pre-2012 record was actually taken at.

Small cells are noisy, so each factor is shrunk toward its 5 deg block, and each block toward
the global value for that calendar month and day/night (empirical-Bayes style). Each factor
also gets s_*, the year-to-year spread of log(ratio) in its block, which becomes the error band.

Factors are learned from vegetation-fire hotspots. Industrial / persistent hot spots (FIRMS
type 1-3, or flagged by pipeline/flags.py) get their own set (k_s_st, k_ta_st, k_t_st, k_a_st):
VIIRS detects far more small hot industrial sources than MODIS, so converting them with the
vegetation-fire ratio would invent jumps where industry is dense (gas flares, steel plants).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from api.weights import PERS_JOIN, STATIC_SQL, create_pers  # noqa: E402
from fetch_recent import MARKER  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HOT = (ROOT / "data" / "hotspots" / "*.parquet").as_posix()
DER = ROOT / "data" / "derived"

SATS = ["Terra", "Aqua", "SNPP", "NOAA20"]
# a satellite counts as observing on a day if its global hotspot count is at least
# this fraction of its own +/-15 day rolling median (catches outages and safe modes)
PRESENT_FRAC = 0.25
N0 = 30.0          # prior strength, in denominator hotspots, for shrinkage
MIN_DEN_YEAR = 5   # a year needs this many denominator hotspots to enter the spread estimate

# name: (numerator sats, denominator sats, first year, last year)
FACTORS = {
    "k_s": (["NOAA20"], ["SNPP"], 2018, 2025),
    "k_ta": (["SNPP"], ["Terra", "Aqua"], 2012, 2019),
    "k_t": (["SNPP"], ["Terra"], 2012, 2019),
    "k_a": (["SNPP"], ["Aqua"], 2012, 2021),
    "m_t": (["Terra", "Aqua"], ["Terra"], 2003, 2011),
    "m_a": (["Terra", "Aqua"], ["Aqua"], 2003, 2011),
}

STATIC_FACTORS = ["k_s", "k_ta", "k_t", "k_a"]  # refit on industrial / persistent hot spots as *_st
MODIS_LINKS = ["k_ta", "k_t", "k_a"]            # the MODIS -> S-NPP factors (what the 2012 switch rests on)

# How the factors are fitted. The shipped choice lives in pipeline/harmonize_config.json, so the
# pipeline, the proof (validate.py) and the experiments all read one config, not code edits.
DEFAULT_CFG = {
    "static_mask": "persistent",  # F6: "persistent", or "persistent+typed" (+ places either sensor types as static)
    "industrial": True,           # F7: industrial / persistent rows get their own *_st factors
    "n0": N0,                     # F8: prior strength (denominator hotspots), 1 deg cell -> parent
    "n0_block": N0,               #     5 deg block -> parent
    "mid": None,                  # F8: None, or the size in degrees of a level between block and globe
    "n0_mid": N0,                 #     mid level -> globe
    "month_pool": 0.0,            # F10: weight of each neighbouring calendar month at cell and block level
    "mass": False,                # F1: rescale so each 5 deg block x month x day/night keeps its VIIRS total
    "n0_mass": 300.0,             #     prior strength of that rescaling (predicted hotspots)
    "fit_years": None,            # F4: e.g. [2012, 2015]: years the MODIS -> S-NPP factors are fitted on
    "matched": True,              # F5: False = fit on all days, not only days both sensors observed
    "n0_factor": {},              # F8: per-factor overrides, e.g. {"k_s": [300, 300]} = [cell n0, block n0]
    "pool_levels": ["block", "cell"],  # where F10 pools neighbouring months
    "pool_mode": "sum",           # F10: "sum" adds w x the neighbours' counts (busy months dominate quiet
                                  # ones); "ratio" moves each month w of the way to each neighbour's ratio
    "dn_pool": 0.0,               # F12: move day and night each w of the way toward the other's ratio
    "year_tau": None,             # F4b: weight overlap year y by exp(-(y - 2012) / tau) (MODIS links only)
    "factor_cfg": {},             # per-factor overrides of any of the above, e.g. {"k_s": {"n0": 3e4}}
    "static_cfg": {},             # overrides for the industrial / persistent (*_st) factors only
}
CFG_PATH = Path(__file__).resolve().parent / "harmonize_config.json"


def load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    if CFG_PATH.exists():
        cfg.update(json.loads(CFG_PATH.read_text(encoding="utf-8")).get("config", {}))
    return cfg


CFG = load_cfg()

CELL_SQL = "(least(CAST(floor(lat) AS INT), 89) + 90) * 360 + (least(CAST(floor(lon) AS INT), 179) + 180)"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET memory_limit = '4GB'")
    con.execute(f"SET temp_directory = '{(ROOT / 'data' / 'tmp').as_posix()}'")
    con.execute("SET preserve_insertion_order = false")
    return con


def build_avail(con, files: str | list[str] = HOT) -> pd.DataFrame:
    src = repr(files) if isinstance(files, list) else f"'{files}'"
    daily = con.execute(f"SELECT acq_date AS d, sat, count(*) AS n FROM read_parquet({src}) GROUP BY ALL").df()
    wide = daily.pivot(index="d", columns="sat", values="n").reindex(columns=SATS).fillna(0)
    wide = wide.reindex(pd.date_range(wide.index.min(), wide.index.max(), freq="D"), fill_value=0)
    avail = pd.DataFrame({"d": wide.index.date})
    for s in SATS:
        med = wide[s].replace(0, np.nan).rolling(31, center=True, min_periods=5).median()
        avail[f"n_{s.lower()}"] = wide[s].to_numpy().astype("int64")
        avail[f"has_{s.lower()}"] = (wide[s] >= PRESENT_FRAC * med).fillna(False).to_numpy()
    return avail


def update_avail(con) -> None:
    """Redo availability for the years pipeline/fetch_recent.py downloads, keeping the archive
    days exactly as the calibration was fitted with them. The year before is read too, so the
    rolling median has context at the start of the new data."""
    year_of = lambda f: int(f.stem.rsplit("_", 1)[1])  # noqa: E731
    files = list((ROOT / "data" / "hotspots").glob("*.parquet"))
    recent = [f for f in files if MARKER in (pq.read_schema(f).metadata or {})]
    if not recent:
        print("avail: no files from fetch_recent.py, nothing to add", flush=True)
        return
    first = date(min(map(year_of, recent)), 1, 1)
    new = build_avail(con, [f.as_posix() for f in files if year_of(f) >= first.year - 1])
    new = new[new.d >= first]
    old = pd.read_parquet(DER / "avail.parquet")
    pd.concat([old[old.d < first], new], ignore_index=True).to_parquet(DER / "avail.parquet", index=False)
    up = {s: int(new[f"has_{s.lower()}"].sum()) for s in SATS}
    print(f"avail: {len(new)} recent days {min(new.d)} .. {max(new.d)}, observing days {up}", flush=True)


def build_cellcounts(con) -> None:
    con.execute(f"""
        COPY (
            SELECT {CELL_SQL} AS cell,
                   year(h.acq_date)::SMALLINT AS y, month(h.acq_date)::TINYINT AS m,
                   h.dn, h.sat,
                   (a.has_terra::INT + 2 * a.has_aqua::INT + 4 * a.has_snpp::INT + 8 * a.has_noaa20::INT)::TINYINT AS flags,
                   count(*) AS n_all,
                   count(*) FILTER (WHERE NOT {STATIC_SQL}) AS n_veg,
                   sum(ln(1 + greatest(h.frp, 0))) FILTER (WHERE NOT {STATIC_SQL}) AS lfrp_sum,
                   sum(ln(greatest(h.scan * h.track, 0.01))) FILTER (WHERE NOT {STATIC_SQL}) AS larea_sum
            FROM read_parquet('{HOT}') h JOIN avail a ON a.d = h.acq_date {PERS_JOIN}
            GROUP BY ALL
        ) TO '{(DER / 'cellcounts.parquet').as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """)


BIT = {"Terra": 1, "Aqua": 2, "SNPP": 4, "NOAA20": 8}


def factor_counts(cc: pd.DataFrame, num: list[str], den: list[str], y0: int, y1: int,
                  matched: bool = True) -> pd.DataFrame:
    """Numerator / denominator counts per cell x m x dn x year on days all involved sats observed."""
    need = sum(BIT[s] for s in set(num) | set(den)) if matched else 0
    sub = cc[(cc.y >= y0) & (cc.y <= y1) & ((cc["flags"] & need) == need)]
    sub = sub.assign(num=np.where(sub.sat.isin(num), sub.n_veg, 0),
                     den=np.where(sub.sat.isin(den), sub.n_veg, 0))
    return sub.groupby(["cell", "m", "dn", "y"], as_index=False)[["num", "den"]].sum()


def shrink(num, den, prior):
    return (num + N0 * prior) / (den + N0)


def log_spread(per_year: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    ok = per_year[(per_year.den >= MIN_DEN_YEAR) & (per_year.num > 0)]
    lr = ok.assign(lr=np.log(ok.num / ok.den))
    return lr.groupby(keys).lr.agg(sd="std", ny="count").reset_index()


def dn_blend(g: pd.DataFrame, keys: list[str], w: float) -> pd.DataFrame:
    """F12: day rows gain w x their den at the night ratio of the same keys x month, and vice versa."""
    r = g[g.den > 0].assign(r=lambda t: t.num / t.den)[keys + ["m", "dn", "r"]]
    r["dn"] = r.dn.map({"D": "N", "N": "D"})
    out = g.merge(r, on=keys + ["m", "dn"], how="left")
    has = out.r.notna() & (out.den > 0)
    out.loc[has, "num"] = out.num[has] + w * out.den[has] * out.r[has]
    out.loc[has, "den"] = out.den[has] * (1 + w)
    return out.drop(columns="r")


def pooled(df: pd.DataFrame, keys: list[str], w: float, mode: str = "sum", dn_w: float = 0.0) -> pd.DataFrame:
    """num, den summed per keys x m x dn; with w > 0 each neighbouring calendar month adds w of its own
    (mode "sum"), or adds w x this month's den at the neighbour's ratio (mode "ratio"); then F12."""
    g = _pooled(df, keys, w, mode)
    return dn_blend(g, keys, dn_w) if dn_w else g


def _pooled(df: pd.DataFrame, keys: list[str], w: float, mode: str) -> pd.DataFrame:
    g = df.groupby(keys + ["m", "dn"], as_index=False)[["num", "den"]].sum()
    if not w:
        return g
    if mode == "ratio":
        out = g.copy()
        for sh in (-1, 1):
            nb = g.copy()
            nb["m"] = ((nb.m.astype(int) - 1 - sh) % 12 + 1).astype(g.m.dtype)  # neighbour's row, moved onto m
            nb = nb[nb.den > 0].assign(r=lambda t: t.num / t.den)[keys + ["m", "dn", "r"]]
            out = out.merge(nb, on=keys + ["m", "dn"], how="left")
            has = out.r.notna() & (out.den > 0)
            out.loc[has, "num"] = out.num[has] + w * out.den[has] * out.r[has]
            out.loc[has, "den"] = out.den[has] * (1 + w)
            out = out.drop(columns="r")
        return out
    parts = [g]
    for sh in (-1, 1):
        t = g.copy()
        t["m"] = ((t.m.astype(int) - 1 + sh) % 12 + 1).astype(g.m.dtype)
        t[["num", "den"]] = t[["num", "den"]] * w
        parts.append(t)
    return pd.concat(parts).groupby(keys + ["m", "dn"], as_index=False)[["num", "den"]].sum()


def mid_of(cell, deg: int):
    """Id of the deg x deg box a 1 deg cell falls in (the F8 middle level)."""
    lat_i = cell // 360 - 90
    lon_i = cell % 360 - 180
    return (np.floor_divide(lat_i + 90, deg) * 1000 + np.floor_divide(lon_i + 180, deg)).astype("int64")


def fit_factor(cc: pd.DataFrame, name: str, num, den, y0, y1, cells: pd.DataFrame,
               cfg: dict | None = None, static: bool = False) -> pd.DataFrame:
    cfg = {**DEFAULT_CFG, **(CFG if cfg is None else cfg)}
    if name in cfg["n0_factor"]:
        cfg = cfg | dict(zip(["n0", "n0_block"], cfg["n0_factor"][name]))
    cfg = cfg | cfg["factor_cfg"].get(name, {})
    if static:
        cfg = cfg | cfg["static_cfg"]
    if cfg["fit_years"] and name in MODIS_LINKS:
        y0, y1 = max(y0, cfg["fit_years"][0]), min(y1, cfg["fit_years"][1])
    py = factor_counts(cc, num, den, y0, y1, cfg["matched"])
    if cfg["year_tau"] and name in MODIS_LINKS:
        wy = np.exp(-(py.y.astype(float) - 2012) / cfg["year_tau"])
        py = py.assign(num=py.num * wy, den=py.den * wy)
    py["block"] = block_of(py.cell)
    pool, dn_w, mode = cfg["month_pool"], cfg["dn_pool"], cfg["pool_mode"]

    # global, per calendar month x day/night
    g = py.groupby(["m", "dn"], as_index=False)[["num", "den"]].sum()
    g["k_g"] = g.num / g.den.where(g.den > 0)
    g_all = py.num.sum() / max(py.den.sum(), 1)
    g["k_g"] = g.k_g.fillna(g_all)

    # optional middle level (F8) shrunk to global; the 5 deg block is shrunk to its parent
    if cfg["mid"]:
        deg = int(cfg["mid"])
        py["mid"] = mid_of(py.cell, deg)
        md = pooled(py, ["mid"], pool, mode, dn_w).merge(g[["m", "dn", "k_g"]], on=["m", "dn"])
        md["k_p"] = (md.num + cfg["n0_mid"] * md.k_g) / (md.den + cfg["n0_mid"])
        parent, pkeys = md[["mid", "m", "dn", "k_p"]], ["mid", "m", "dn"]
    else:
        parent, pkeys = g[["m", "dn", "k_g"]].rename(columns={"k_g": "k_p"}), ["m", "dn"]
    b = pooled(py, ["block"], pool if "block" in cfg["pool_levels"] else 0.0, mode, dn_w)
    if cfg["mid"]:
        b["mid"] = mid_of((b.block // 72) * 5 * 360 + (b.block % 72) * 5, deg)
    b = b.merge(parent, on=pkeys, how="left").merge(g[["m", "dn", "k_g"]], on=["m", "dn"], how="left")
    b["k_p"] = b.k_p.fillna(b.k_g)
    b["k_b"] = (b.num + cfg["n0_block"] * b.k_p) / (b.den + cfg["n0_block"])

    # 1 deg cell, shrunk to block
    c = pooled(py, ["cell"], pool if "cell" in cfg["pool_levels"] else 0.0, mode, dn_w)

    # year-to-year spread of log ratio: block level, shrunk toward the global spread
    by = py.groupby(["block", "m", "dn", "y"], as_index=False)[["num", "den"]].sum()
    sb = log_spread(by, ["block", "m", "dn"])
    gy = py.groupby(["m", "dn", "y"], as_index=False)[["num", "den"]].sum()
    sg = log_spread(gy, ["m", "dn"]).rename(columns={"sd": "sd_g"})[["m", "dn", "sd_g"]]
    sd_g_all = float(np.nanmedian(sg.sd_g)) if len(sg) else 0.3

    out = cells.copy()
    out["block"] = block_of(out.cell)
    out = (out.merge(c, on=["cell", "m", "dn"], how="left")
              .merge(b[["block", "m", "dn", "k_b"]], on=["block", "m", "dn"], how="left")
              .merge(g[["m", "dn", "k_g"]], on=["m", "dn"], how="left")
              .merge(sb, on=["block", "m", "dn"], how="left")
              .merge(sg, on=["m", "dn"], how="left"))
    out["k_g"] = out.k_g.fillna(g_all)
    if cfg["mid"]:  # a cell whose block has no data falls back to its middle-level box
        out["mid"] = mid_of(out.cell, deg)
        out["k_g"] = out.merge(parent, on=pkeys, how="left").k_p.fillna(out.k_g).to_numpy()
    out["k_b"] = out.k_b.fillna(out.k_g)
    out[name] = (out.num.fillna(0) + cfg["n0"] * out.k_b) / (out.den.fillna(0) + cfg["n0"])

    if cfg["mass"]:
        # F1: VIIRS hotspots in cell-months where MODIS saw nothing can't ride on a ratio, and
        # shrinkage moves mass between cells. So each block x month x day/night is rescaled until its
        # training-years prediction adds up to what VIIRS saw there (shrunk toward month x day/night).
        tr = py[["cell", "block", "m", "dn", "num", "den"]].merge(
            out[["cell", "m", "dn", name]], on=["cell", "m", "dn"], how="left")
        tr["P"] = tr.den * tr[name].fillna(0)
        rg = tr.groupby(["m", "dn"], as_index=False)[["num", "P"]].sum()
        rg["r_g"] = (rg.num / rg.P.where(rg.P > 0)).fillna(1.0)
        rb = tr.groupby(["block", "m", "dn"], as_index=False)[["num", "P"]].sum().merge(rg[["m", "dn", "r_g"]])
        rb["r_b"] = (rb.num + cfg["n0_mass"] * rb.r_g) / (rb.P + cfg["n0_mass"])
        r = (out[["block", "m", "dn"]].merge(rb[["block", "m", "dn", "r_b"]], on=["block", "m", "dn"], how="left")
                .merge(rg[["m", "dn", "r_g"]], on=["m", "dn"], how="left"))
        out[name] = out[name].to_numpy() * r.r_b.fillna(r.r_g).fillna(1.0).to_numpy()

    sd_g = out.sd_g.fillna(sd_g_all)
    ny = out.ny.fillna(0)
    out[f"s_{name}"] = np.sqrt((ny * out.sd.fillna(0) ** 2 + 3 * sd_g ** 2) / (ny + 3))
    out[f"den_{name}"] = out.den.fillna(0).astype("int64")
    return out[["cell", "m", "dn", name, f"s_{name}", f"den_{name}"]]


def block_of(cell):
    lat_i = cell // 360 - 90
    lon_i = cell % 360 - 180
    return (np.floor_divide(lat_i + 90, 5) * 72 + np.floor_divide(lon_i + 180, 5)).astype("int32")


FRP_SHRINK_N = 10.0  # MODIS hotspots at which a cell-month's FRP deviation counts half


def modis_features(cc: pd.DataFrame) -> pd.DataFrame:
    """How unusual each cell x year x month x day/night's MODIS fires are, vs that place's normal.

    x_frp:  mean log(1+FRP) minus its 2003-2019 average. Negative = weaker fires than usual,
            so MODIS misses more of the small fires VIIRS still sees.
    x_area: the same for mean log pixel area (scan x track; bigger = nearer the swath edge).
    Both shrink toward 0 when the cell had few MODIS hotspots that month.
    """
    t = cc[cc.sat.isin(["Terra", "Aqua"])]
    g = t.groupby(["cell", "y", "m", "dn"], as_index=False)[["n_veg", "lfrp_sum", "larea_sum"]].sum()
    g = g[g.n_veg > 0]
    base = (g[(g.y >= 2003) & (g.y <= 2019)]
            .groupby(["cell", "m", "dn"], as_index=False)[["n_veg", "lfrp_sum", "larea_sum"]].sum())
    base = base.assign(bf=base.lfrp_sum / base.n_veg, ba=base.larea_sum / base.n_veg)[["cell", "m", "dn", "bf", "ba"]]
    g = g.merge(base, on=["cell", "m", "dn"], how="left")
    w = g.n_veg / (g.n_veg + FRP_SHRINK_N)
    g["x_frp"] = ((g.lfrp_sum / g.n_veg - g.bf) * w).fillna(0.0)
    g["x_area"] = ((g.larea_sum / g.n_veg - g.ba) * w).fillna(0.0)
    return g[["cell", "y", "m", "dn", "x_frp", "x_area"]]


def fit_frp(units: pd.DataFrame) -> list[float]:
    """Weighted least squares of log((V+1)/(P+1)) on x_frp and x_area, where P is the factor
    prediction of V from MODIS count M. Returns the two slopes (the intercept is dropped)."""
    r = np.log((units.V + 1) / (units.P + 1)).to_numpy()
    sw = np.sqrt(np.clip(units.M.to_numpy(), 1, 500))
    X = np.column_stack([np.ones(len(units)), units.x_frp.to_numpy(), units.x_area.to_numpy()])
    b, *_ = np.linalg.lstsq(X * sw[:, None], r * sw, rcond=None)
    return [float(b[1]), float(b[2])]


def main() -> None:
    t0 = time.time()
    DER.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "tmp").mkdir(parents=True, exist_ok=True)
    con = connect()
    if "--avail-only" in sys.argv:  # new days from fetch_recent.py; factors stay as fitted
        update_avail(con)
        return

    avail = build_avail(con)
    avail.to_parquet(DER / "avail.parquet", index=False)
    con.register("avail", avail)
    create_pers(con, DER)
    up = {s: int(avail[f"has_{s.lower()}"].sum()) for s in SATS}
    print(f"avail: {len(avail)} days {avail.d.min()} .. {avail.d.max()}, observing days {up}  [{time.time() - t0:.0f}s]", flush=True)

    build_cellcounts(con)
    cc = pd.read_parquet(DER / "cellcounts.parquet", columns=["cell", "y", "m", "dn", "sat", "flags", "n_all", "n_veg"])
    cc = cc.astype({"cell": "int32", "y": "int16", "m": "int8", "flags": "int8", "n_all": "int32", "n_veg": "int32"})
    print(f"cellcounts: {len(cc):,} rows  [{time.time() - t0:.0f}s]", flush=True)

    cells = cc[["cell", "m", "dn"]].drop_duplicates().reset_index(drop=True)
    calib = cells
    for name, (num, den, y0, y1) in FACTORS.items():
        f = fit_factor(cc, name, num, den, y0, y1, cells)
        calib = calib.merge(f, on=["cell", "m", "dn"], how="left")
        q = f[name].quantile([0.1, 0.5, 0.9]).round(2).tolist()
        print(f"{name}: p10/p50/p90 = {q}, median band s = {f[f's_{name}'].median():.2f}", flush=True)

    # industrial / persistent hot spots get their own factors: VIIRS picks up far more small
    # hot industrial sources than MODIS, so the vegetation-fire ratio badly undercounts them
    static = cc.assign(n_veg=cc.n_all - cc.n_veg)  # fit_factor counts the n_veg column
    for name in STATIC_FACTORS:
        num, den, y0, y1 = FACTORS[name]
        f = fit_factor(static, name, num, den, y0, y1, cells, static=True)
        f = f.rename(columns={name: f"{name}_st", f"s_{name}": f"s_{name}_st", f"den_{name}": f"den_{name}_st"})
        calib = calib.merge(f, on=["cell", "m", "dn"], how="left")
        q = f[f"{name}_st"].quantile([0.1, 0.5, 0.9]).round(2).tolist()
        print(f"{name}_st (industrial): p10/p50/p90 = {q}", flush=True)

    calib["cell"] = calib.cell.astype("int32")
    calib["m"] = calib.m.astype("int8")
    calib.to_parquet(DER / "calib.parquet", index=False)
    print(f"calib: {len(calib):,} rows -> {DER / 'calib.parquet'}  [{time.time() - t0:.0f}s]", flush=True)


if __name__ == "__main__":
    main()
