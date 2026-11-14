"""Proof pack: does the harmonization work, and are its error bars honest?

Reads  data/derived/{cellcounts,grid,regions}.parquet
Writes data/derived/proof.json  (served by GET /api/proof)

1. Ablation, leave-one-year-out. For each overlap year Y in 2012-2019, learn the
   MODIS -> VIIRS factor from the other years only, predict Y's S-NPP hotspot counts
   from Y's Terra+Aqua counts, and compare with what S-NPP actually saw (matched days,
   vegetation fires). Error = WAPE = sum|pred - actual| / sum actual, reported at three
   scales: 1 deg cell x month, world subregion x month, globe x month. Rows add one
   ingredient at a time, then remove one ingredient at a time from the full method.

2. Jump tests. MODIS observes straight through the 2012 VIIRS handover, so its own
   change across 2012 is the real change. The harmonized record should show the same
   change; a naive merged record (MODIS until 2011, VIIRS after) should not. Same for
   the 2018 S-NPP -> NOAA-20 handover, using S-NPP as the continuous reference.
   Plus a Pettitt change-point test on annual totals (raw merged vs harmonized).

3. Band coverage. Share of held-out values that fall inside the factor-based 90% band.

4. Conformal bands. The factor band ignores counting noise and local variability, so
   instead the band width is learned from the held-out errors themselves:
   r = |log((actual+1)/(pred+1))| for monthly totals at several scales (1 deg cell, 5 deg
   block, world subregion, globe), and the 90th percentile of r per size bin of the
   predicted total becomes the band half-width (bigger totals -> tighter bands).
   Coverage is cross-fitted: each year's coverage uses a table learned without it.
   Done for both links: MODIS -> S-NPP (2012-2019) and S-NPP -> NOAA-20 (2018-2025).
   Writes data/derived/bands.json, which the API uses for lo/hi.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from calibrate import BIT, CFG, DEFAULT_CFG, block_of, fit_factor, fit_frp, modis_features  # noqa: E402

DER = ROOT / "data" / "derived"
Z90 = 1.6449
TEST_YEARS = list(range(2012, 2020))
NEED = BIT["Terra"] | BIT["Aqua"] | BIT["SNPP"]
MODIS, VIIRS = ["Terra", "Aqua"], ["SNPP"]


# ---------------------------------------------------------------- cell -> world subregion

def cell_subregions(cells: np.ndarray) -> pd.Series:
    """1 deg cell id -> Natural Earth subregion of the country under the cell centre."""
    reg = pd.read_parquet(DER / "regions.parquet")
    reg = reg[reg.kind == "country"].reset_index(drop=True)
    geoms = [shape(json.loads(g)) for g in reg.geojson]
    lat = cells // 360 - 90 + 0.5
    lon = cells % 360 - 180 + 0.5
    pts = shapely.points(lon, lat)
    tree = shapely.STRtree(geoms)
    p_idx, g_idx = tree.query(pts, predicate="within")
    sub = pd.Series("Other", index=cells, dtype=object)
    sub.iloc[p_idx] = reg.subregion.to_numpy()[g_idx]
    # coastal cells whose centre is at sea: nearest country within ~1 deg
    miss = np.where(sub.to_numpy() == "Other")[0]
    if len(miss):
        near_p, near_g = tree.query_nearest(pts[miss], max_distance=1.0, return_distance=False)
        sub.iloc[miss[near_p]] = reg.subregion.to_numpy()[near_g]
    return sub


# ---------------------------------------------------------------- ablation

def held_out(cc: pd.DataFrame, y: int, num=VIIRS, den=MODIS) -> pd.DataFrame:
    """Actual target (V) and source (M) counts per cell x month x day/night in year y, matched days."""
    need = sum(BIT[x] for x in set(num) | set(den))
    t = cc[(cc.y == y) & ((cc["flags"] & need) == need)]
    t = t.assign(V=np.where(t.sat.isin(num), t.n_veg, 0), M=np.where(t.sat.isin(den), t.n_veg, 0))
    return t.groupby(["cell", "m", "dn"], as_index=False)[["V", "M"]].sum()


def ratio_by(train: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    t = train[(train["flags"] & NEED) == NEED]
    t = t.assign(V=np.where(t.sat.isin(VIIRS), t.n_veg, 0), M=np.where(t.sat.isin(MODIS), t.n_veg, 0))
    g = t.groupby(keys, as_index=False)[["V", "M"]].sum() if keys else pd.DataFrame([t[["V", "M"]].sum()])
    g["k"] = g.V / g.M.where(g.M > 0)
    return g.drop(columns=["V", "M"])


def predict(test: pd.DataFrame, k: pd.Series, s: pd.Series | None = None) -> pd.DataFrame:
    out = test.assign(P=test.M * k.to_numpy())
    if s is not None:
        out["P_lo"] = out.P * np.exp(-Z90 * s.to_numpy())
        out["P_hi"] = out.P * np.exp(Z90 * s.to_numpy())
    return out


FULL = "+ per 1° cell (shrunk) = FireLedger"


def fit_qm(units: pd.DataFrame, min_n: int = 30) -> dict:
    """Zero-aware quantile mapping per 5 deg block x day/night: the distribution of factor
    predictions P is mapped onto the distribution of actual V, using units where MODIS saw fire."""
    lv = np.linspace(0, 1, 51)
    tables = {}
    for key, g in units[units.M > 0].groupby(["block", "dn"]):
        if len(g) >= min_n:
            tables[key] = (np.maximum.accumulate(np.quantile(g.P, lv)), np.maximum.accumulate(np.quantile(g.V, lv)))
    return tables


def apply_qm(df: pd.DataFrame, tables: dict) -> np.ndarray:
    out = df.P.to_numpy(dtype=float).copy()
    for key, idx in df.groupby(["block", "dn"]).indices.items():
        if key not in tables:
            continue
        qp, qv = tables[key]
        x = out[idx]
        yv = np.interp(x, qp, qv)
        top = x > qp[-1]  # beyond the training range: keep the ratio at the top quantile
        yv[top] = x[top] * (qv[-1] / qp[-1] if qp[-1] > 0 else 1.0)
        out[idx] = yv
    return out


def variants(cc: pd.DataFrame, y: int, H: dict, allkeys: pd.DataFrame, feats: pd.DataFrame) -> dict[str, pd.DataFrame]:
    test = H[y]
    win = cc[(cc.y >= 2012) & (cc.y <= 2019) & (cc.y != y)]
    keys = test[["cell", "m", "dn"]]
    out = {"No correction (raw MODIS)": predict(test, pd.Series(np.ones(len(test))))}

    g = ratio_by(win, [])["k"].iloc[0]
    out["One global ratio"] = predict(test, pd.Series(np.full(len(test), g)))

    mdn = ratio_by(win, ["m", "dn"])
    k = keys.merge(mdn, on=["m", "dn"], how="left").k.fillna(g)
    out["+ per month and day/night"] = predict(test, k)

    # 5 deg block: give every 1 deg cell the id of its block's corner cell, so fit_factor pools the block
    def blk(df):
        b = block_of(df.cell)
        return df.assign(cell=((b // 72) * 5 * 360 + (b % 72) * 5).astype("int64"))
    fb = fit_factor(blk(win), "k_ta", ["SNPP"], MODIS, 2012, 2019, blk(keys).drop_duplicates(), DEFAULT_CFG)
    k = blk(keys).merge(fb, on=["cell", "m", "dn"], how="left").k_ta
    out["+ per 5° region (shrunk)"] = predict(test, k)

    # the shipped method (pipeline/harmonize_config.json); the rows above are the build-up
    full = fit_factor(win, "k_ta", ["SNPP"], MODIS, 2012, 2019, allkeys, CFG)
    f = keys.merge(full, on=["cell", "m", "dn"], how="left")
    out[FULL] = predict(test, f.k_ta, f.s_k_ta)

    # residual models, learned on the training years' units (their factor predictions vs actual)
    train = pd.concat([H[t].assign(y=t) for t in TEST_YEARS if t != y])
    train = train.merge(full[["cell", "m", "dn", "k_ta"]], on=["cell", "m", "dn"], how="left")
    train["P"] = train.M * train.k_ta
    train = train.merge(feats, on=["cell", "y", "m", "dn"], how="left").fillna({"x_frp": 0.0, "x_area": 0.0})
    b = fit_frp(train[train.M > 0])
    tf = test.assign(y=y).merge(feats, on=["cell", "y", "m", "dn"], how="left").fillna({"x_frp": 0.0, "x_area": 0.0})
    adj = np.exp(b[0] * tf.x_frp.to_numpy() + b[1] * tf.x_area.to_numpy())
    out["+ FRP-aware adjustment"] = predict(test, f.k_ta * adj, f.s_k_ta)

    tables = fit_qm(train.assign(block=block_of(train.cell)))
    pq = predict(test, f.k_ta)
    out["+ quantile mapping (zero-aware)"] = pq.assign(P=apply_qm(pq.assign(block=block_of(pq.cell)), tables))

    # remove one ingredient at a time from the full method
    raw = ratio_by(win, ["cell", "m", "dn"])
    k = keys.merge(raw, on=["cell", "m", "dn"], how="left").k
    k = k.fillna(keys.merge(mdn, on=["m", "dn"], how="left").k).fillna(g)
    out["− without shrinkage"] = predict(test, k)

    nomatch = fit_factor(win.assign(flags=15), "k_ta", ["SNPP"], MODIS, 2012, 2019, keys, CFG)
    out["− without matched days"] = predict(test, keys.merge(nomatch, on=["cell", "m", "dn"], how="left").k_ta)

    allyrs = cc[(cc.y >= 2012) & (cc.y != y)]
    drift = fit_factor(allyrs, "k_ta", ["SNPP"], MODIS, 2012, 2025, keys, CFG | {"fit_years": None})
    out["− without drift-aware window"] = predict(test, keys.merge(drift, on=["cell", "m", "dn"], how="left").k_ta)
    return out


def wape(df: pd.DataFrame, by: list[str]) -> tuple[float, float]:
    a = df.groupby(by)[["P", "V"]].sum()
    return float((a.P - a.V).abs().sum() / a.V.sum()), float(a.P.sum() / a.V.sum() - 1)


def coverage(df: pd.DataFrame, by: list[str]) -> dict:
    a = df.groupby(by)[["P", "P_lo", "P_hi", "V"]].sum()
    a = a[(a.V > 0) | (a.P > 0)]
    inside = (a.V >= a.P_lo) & (a.V <= a.P_hi)
    # add counting noise (Poisson) on top of the factor uncertainty
    noise = Z90 * np.sqrt(a.P)
    inside_n = (a.V >= a.P_lo - noise) & (a.V <= a.P_hi + noise)
    # split-conformal: how much wider (in log space) must the band be to cover 90%?
    ok = (a.V > 0) & (a.P > 0) & (a.P_hi > a.P)
    u = np.abs(np.log(a.V[ok] / a.P[ok])) / np.log(a.P_hi[ok] / a.P[ok])
    return {"n": int(len(a)), "coverage": round(float(inside.mean()), 3),
            "coverage_with_count_noise": round(float(inside_n.mean()), 3),
            "conformal_widen_factor": round(float(np.quantile(u, 0.9)), 2) if len(u) else None}


def jump_variants(cc: pd.DataFrame, sub: pd.Series, feats: pd.DataFrame) -> dict:
    """2012 step per world region for each method, from the cell counts (Feb-Dec only, since
    S-NPP data starts on 20 Jan 2012). Real change = MODIS's own step; gap = method - MODIS."""
    t = cc[(cc.y >= 2009) & (cc.y <= 2014) & (cc.m >= 2)]
    mod = (t[t.sat.isin(MODIS)].groupby(["cell", "y", "m", "dn"], as_index=False).n_veg.sum()
           .rename(columns={"n_veg": "M"}))
    snpp = t[t.sat == "SNPP"].groupby(["cell", "y"], as_index=False).n_veg.sum().rename(columns={"n_veg": "V"})
    win = cc[(cc.y >= 2012) & (cc.y <= 2019)]
    keys = mod[["cell", "m", "dn"]].drop_duplicates()
    k = fit_factor(win, "k_ta", ["SNPP"], MODIS, 2012, 2019, keys)
    H = {yy: held_out(cc, yy).assign(y=yy) for yy in TEST_YEARS}
    train = pd.concat(H.values()).merge(fit_factor(win, "k_ta", ["SNPP"], MODIS, 2012, 2019,
                                                   pd.concat(H.values())[["cell", "m", "dn"]].drop_duplicates())
                                        [["cell", "m", "dn", "k_ta"]], on=["cell", "m", "dn"], how="left")
    train["P"] = train.M * train.k_ta
    train = train.merge(feats, on=["cell", "y", "m", "dn"], how="left").fillna({"x_frp": 0.0, "x_area": 0.0})
    b = fit_frp(train[train.M > 0])
    tables = fit_qm(train.assign(block=block_of(train.cell)))

    mod = mod.merge(k[["cell", "m", "dn", "k_ta"]], on=["cell", "m", "dn"], how="left")
    mod = mod.merge(feats, on=["cell", "y", "m", "dn"], how="left").fillna({"x_frp": 0.0, "x_area": 0.0})
    mod["ratio"] = mod.M * mod.k_ta
    mod["frp"] = mod.ratio * np.exp(b[0] * mod.x_frp + b[1] * mod.x_area)
    mod["qm"] = apply_qm(mod.assign(P=mod.ratio, block=block_of(mod.cell)), tables)
    mod["region"] = sub.reindex(mod.cell.to_numpy()).fillna("Other").to_numpy()
    snpp["region"] = sub.reindex(snpp.cell.to_numpy()).fillna("Other").to_numpy()

    pre, post = [2009, 2010, 2011], [2012, 2013, 2014]
    rows = {}
    for region in list(mod.region.unique()) + ["World"]:
        mm = mod if region == "World" else mod[mod.region == region]
        vv = snpp if region == "World" else snpp[snpp.region == region]
        if region == "Other" or mm.M.sum() < 6 * 5000:
            continue
        a = mm.groupby("y")[["M", "ratio", "frp", "qm"]].sum()
        v = vv.groupby("y").V.sum()
        real = a.M.loc[post].mean() / a.M.loc[pre].mean() - 1
        rows[region] = {"modis": round(real, 3)}
        for meth in ["ratio", "frp", "qm"]:
            rows[region][meth] = round(v.loc[post].mean() / a[meth].loc[pre].mean() - 1 - real, 3)
    summary = {meth: round(float(np.mean([abs(r[meth]) for k_, r in rows.items() if k_ != "World"])), 3)
               for meth in ["ratio", "frp", "qm"]}
    return {"frp_slopes": {"x_frp": round(b[0], 3), "x_area": round(b[1], 3)}, "regions": rows,
            "mean_abs_gap": summary}


# ---------------------------------------------------------------- conformal bands

EDGES = [0, 3, 10, 30, 100, 300, 1_000, 3_000, 10_000, 30_000, 100_000, np.inf]
CONF_SCALES = {"cell_month": ["y", "cell", "m"], "block_month": ["y", "block", "m"],
               "region_month": ["y", "region", "m"], "world_month": ["y", "m"]}


STRATA = {"tropics": (0, 23), "midlat": (23, 50), "highlat": (50, 91)}  # by |latitude|
# which scale's band table an area of this size (deg^2) uses in the API
SCALE_MAX_AREA = {"cell_month": 3, "block_month": 60, "region_month": 3000, "world_month": None}


def stratum_of(abs_lat: np.ndarray) -> np.ndarray:
    out = np.full(len(abs_lat), "mixed", dtype=object)
    for name, (lo, hi) in STRATA.items():
        out[(abs_lat >= lo) & (abs_lat < hi)] = name
    return out


def residuals(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly totals at every scale: y, scale, stratum, P (predicted), V (actual), r = |log((V+1)/(P+1))|.
    A unit's latitude is the fire-weighted mean of its cells; the world total is 'mixed'."""
    df = df.assign(block=block_of(df.cell), wl=(np.abs(df.cell // 360 - 90 + 0.5)) * (df.P + df.V + 1), w=df.P + df.V + 1)
    parts = []
    for sc, by in CONF_SCALES.items():
        a = df.groupby(by)[["P", "V", "wl", "w"]].sum().reset_index()
        a = a[(a.P > 0) | (a.V > 0)]
        st = stratum_of((a.wl / a.w).to_numpy()) if sc != "world_month" else np.full(len(a), "mixed", dtype=object)
        parts.append(pd.DataFrame({"y": a.y.to_numpy(), "scale": sc, "stratum": st,
                                   "P": a.P.to_numpy(), "V": a.V.to_numpy()}))
    out = pd.concat(parts, ignore_index=True)
    out["r"] = np.abs(np.log((out.V + 1) / (out.P + 1)))
    out["bin"] = np.searchsorted(EDGES, out.P.to_numpy(), side="right") - 1
    return out


def q_table(res: pd.DataFrame, min_n: int = 20, fallback: list[float] | None = None) -> list[float]:
    """90th percentile of r per size bin; sparse bins take the fallback table (or a neighbour);
    never wider for bigger totals."""
    q = res.groupby("bin").r.agg(lambda r: np.quantile(r, 0.9) if len(r) >= min_n else np.nan)
    q = q.reindex(range(len(EDGES) - 1))
    if fallback is not None:
        q = q.fillna(pd.Series(fallback, index=q.index))
    q = q.bfill().ffill().to_numpy()
    for i in range(len(q) - 2, -1, -1):
        q[i] = max(q[i], q[i + 1])
    return [round(float(v), 4) for v in q]


def band_tables(res: pd.DataFrame) -> dict:
    """Band tables by scale x latitude stratum. Each falls back to its scale's table, which falls
    back to the all-units table, wherever a size bin has too few units."""
    base = q_table(res)
    out = {"all": base}
    for sc in CONF_SCALES:
        r = res[res.scale == sc]
        st_base = q_table(r, fallback=base)
        out[sc] = {"all": st_base} | {st: q_table(r[r.stratum == st], fallback=st_base) for st in STRATA}
    return out


def lookup(tables: dict, scale: str, stratum: str) -> np.ndarray:
    return np.array(tables[scale].get(stratum, tables[scale]["all"]))


def conformal(res: pd.DataFrame) -> dict:
    """Cross-fitted coverage of the scale x latitude band tables: each year is scored with
    tables learned without it."""
    cover = {sc: [] for sc in CONF_SCALES}
    cover_st = {st: [] for st in STRATA}
    for y in sorted(res.y.unique()):
        tabs = band_tables(res[res.y != y])
        t = res[res.y == y]
        qq = np.empty(len(t))
        for (sc, st), idx in t.groupby(["scale", "stratum"]).indices.items():
            qq[idx] = lookup(tabs, sc, st)[t.bin.to_numpy()[idx]]
        lo, hi = (t.P + 1) * np.exp(-qq) - 1, (t.P + 1) * np.exp(qq) - 1
        inside = ((t.V >= lo) & (t.V <= hi)).to_numpy()
        for sc in CONF_SCALES:
            cover[sc].append(inside[(t.scale == sc).to_numpy()])
        for st in STRATA:
            cover_st[st].append(inside[((t.stratum == st) & (t.scale == "block_month")).to_numpy()])
    tabs = band_tables(res)
    return {"table": tabs["all"], "tables": tabs,
            "coverage_crossfit": {sc: round(float(np.concatenate(v).mean()), 3) for sc, v in cover.items()},
            "coverage_block_by_latitude": {st: round(float(np.concatenate(v).mean()), 3) for st, v in cover_st.items()},
            "n": {sc: int((res.scale == sc).sum()) for sc in CONF_SCALES}}


def snpp_link(cc: pd.DataFrame, sub: pd.Series) -> pd.DataFrame:
    """Leave-one-year-out S-NPP -> NOAA-20 predictions, 2018-2025, full method."""
    parts = []
    for y in range(2018, 2026):
        test = held_out(cc, y, ["NOAA20"], ["SNPP"])
        keys = test[["cell", "m", "dn"]]
        win = cc[(cc.y >= 2018) & (cc.y != y)]
        f = keys.merge(fit_factor(win, "k_s", ["NOAA20"], ["SNPP"], 2018, 2025, keys), on=["cell", "m", "dn"], how="left")
        parts.append(predict(test, f.k_s).assign(y=y, region=sub.reindex(test.cell.to_numpy()).fillna("Other").to_numpy()))
    return pd.concat(parts)


# ---------------------------------------------------------------- jump tests

def pettitt(x: np.ndarray) -> tuple[int, float]:
    """Index of the most likely change point (first index of the 'after' part) and its p-value."""
    n = len(x)
    s = np.sign(x[None, :] - x[:, None])
    u = np.array([s[: t + 1, t + 1:].sum() for t in range(n - 1)])
    t = int(np.argmax(np.abs(u)))
    k = float(np.abs(u[t]))
    p = min(1.0, 2 * np.exp(-6 * k ** 2 / (n ** 3 + n ** 2)))
    return t + 1, p


def jump_tests(sub_of_cell: pd.Series) -> list[dict]:
    g = duckdb.connect().execute(f"""
        SELECT (CAST(floor(gy / 4) AS INT) + 90) * 360 + (CAST(floor(gx / 4) AS INT) + 180) AS cell, y,
               sum(v) AS v, sum(raw_modis) AS raw_modis, sum(raw_viirs) AS raw_viirs, sum(raw_snpp) AS raw_snpp,
               sum(v) FILTER (WHERE NOT static) AS v_veg, sum(raw_modis) FILTER (WHERE NOT static) AS modis_veg
        FROM read_parquet('{(DER / "grid.parquet").as_posix()}') WHERE y BETWEEN 2001 AND 2025 GROUP BY ALL
    """).df()
    g["region"] = sub_of_cell.reindex(g.cell.to_numpy()).fillna("Other").to_numpy()
    cols = ["v", "raw_modis", "raw_viirs", "raw_snpp", "v_veg", "modis_veg"]
    g[cols] = g[cols].fillna(0)
    ann = g.groupby(["region", "y"])[cols].sum().reset_index()
    ann = pd.concat([ann, ann.groupby("y")[cols].sum().reset_index().assign(region="World")])
    ann["merged_raw"] = np.where(ann.y < 2012, ann.raw_modis, ann.raw_viirs)

    def step(s: pd.Series, before: range, after: range) -> float:
        b, a = s[s.index.isin(before)].mean(), s[s.index.isin(after)].mean()
        return float(a / b - 1) if b > 0 else float("nan")

    rows = []
    for region, d in ann.groupby("region"):
        d = d.set_index("y").sort_index()
        if d.raw_modis.sum() < 5000 or region == "Other":
            continue
        pre, post = range(2009, 2012), range(2012, 2015)
        pre2, post2 = range(2015, 2018), range(2018, 2021)
        bp_raw, p_raw = pettitt(d.merged_raw.to_numpy())
        bp_h, p_h = pettitt(d.v.to_numpy())
        rows.append({
            "region": region,
            "hotspots_per_year": int(d.v.mean()),
            "step2012_modis": round(step(d.raw_modis, pre, post), 3),
            "step2012_raw_merged": round(step(d.merged_raw, pre, post), 3),
            "step2012_harmonized": round(step(d.v, pre, post), 3),
            "step2012_modis_vegetation": round(step(d.modis_veg, pre, post), 3),
            "step2012_harmonized_vegetation": round(step(d.v_veg, pre, post), 3),
            "industrial_share": round(float(1 - d.v_veg.sum() / d.v.sum()), 3),
            "step2018_snpp": round(step(d.raw_snpp, pre2, post2), 3),
            "step2018_harmonized": round(step(d.v, pre2, post2), 3),
            "pettitt_raw": {"year": int(d.index[bp_raw]), "p": round(p_raw, 4)},
            "pettitt_harmonized": {"year": int(d.index[bp_h]), "p": round(p_h, 4)},
        })
    return sorted(rows, key=lambda r: (r["region"] != "World", -r["hotspots_per_year"]))


# ---------------------------------------------------------------- main

def main() -> None:
    t0 = time.time()
    cc = pd.read_parquet(DER / "cellcounts.parquet",
                         columns=["cell", "y", "m", "dn", "sat", "flags", "n_veg", "lfrp_sum", "larea_sum"])
    cc = cc.astype({"cell": "int64", "y": "int16", "m": "int8", "flags": "int8", "n_veg": "int32"})
    sub = cell_subregions(np.sort(cc.cell.unique()))
    print(f"loaded {len(cc):,} cell counts, {sub.nunique()} subregions  [{time.time() - t0:.0f}s]", flush=True)

    feats = modis_features(cc)
    H = {y: held_out(cc, y) for y in TEST_YEARS}
    allkeys = pd.concat(H.values())[["cell", "m", "dn"]].drop_duplicates().reset_index(drop=True)
    preds: dict[str, list[pd.DataFrame]] = {}
    for y in TEST_YEARS:
        for name, df in variants(cc, y, H, allkeys, feats).items():
            df = df.assign(y=y, region=sub.reindex(df.cell.to_numpy()).fillna("Other").to_numpy())
            preds.setdefault(name, []).append(df)
        print(f"  held out {y}  [{time.time() - t0:.0f}s]", flush=True)

    scales = {"cell_month": ["y", "cell", "m"], "region_month": ["y", "region", "m"], "world_month": ["y", "m"]}
    ablation = []
    for name, parts in preds.items():
        df = pd.concat(parts)
        row = {"step": name}
        for sc, by in scales.items():
            e, b = wape(df, by)
            row[f"wape_{sc}"], row[f"bias_{sc}"] = round(e, 4), round(b, 4)
        ablation.append(row)
    base = next(r for r in ablation if r["step"] == "One global ratio")
    for r in ablation:
        r["error_cut_vs_global_ratio_cell"] = round(1 - r["wape_cell_month"] / base["wape_cell_month"], 3)

    full = pd.concat(preds[FULL])
    cov = {sc: coverage(full, by) for sc, by in scales.items()}

    conf_modis = conformal(residuals(full))
    snpp = snpp_link(cc, sub)
    conf_snpp = conformal(residuals(snpp))
    snpp_err = {sc: round(wape(snpp, by)[0], 4) for sc, by in scales.items()}
    edges = [None if np.isinf(e) else e for e in EDGES]
    (DER / "bands.json").write_text(json.dumps({
        "edges": edges, "modis": conf_modis["table"], "snpp": conf_snpp["table"],
        "strata": {k: list(v) for k, v in STRATA.items()},
        "scale_max_area_deg2": SCALE_MAX_AREA,
        "modis_tables": conf_modis["tables"], "snpp_tables": conf_snpp["tables"],
        "note": "90% band half-width in log space, |log((V+1)/(P+1))|, by size bin of the predicted monthly total",
    }, indent=1), encoding="utf-8")
    print(f"  conformal bands done  [{time.time() - t0:.0f}s]", flush=True)
    jumps = jump_tests(sub)
    jv = jump_variants(cc, sub, feats)

    proof = {
        "method": "leave-one-year-out over 2012-2019; MODIS (Terra+Aqua) -> S-NPP on matched days, vegetation fires",
        "test_years": TEST_YEARS,
        "ablation": ablation,
        "band_coverage_90": cov,
        "conformal_bands": {"edges": edges, "modis_to_snpp": conf_modis, "snpp_to_noaa20": conf_snpp},
        "snpp_to_noaa20_wape": snpp_err,
        "jumps": jumps,
        "jump_gap_by_method": jv,
        "built_seconds": round(time.time() - t0),
    }
    (DER / "proof.json").write_text(json.dumps(proof, indent=1, ensure_ascii=False), encoding="utf-8")

    print("\nABLATION  (WAPE = sum|pred-actual| / sum actual; lower is better)")
    print(f"{'step':42s} {'1° cell×month':>14s} {'region×month':>13s} {'world×month':>12s} {'bias(cell)':>11s}")
    for r in ablation:
        print(f"{r['step']:42s} {100 * r['wape_cell_month']:13.1f}% {100 * r['wape_region_month']:12.1f}% "
              f"{100 * r['wape_world_month']:11.1f}% {100 * r['bias_cell_month']:+10.1f}%")
    print("\nBAND COVERAGE (target 90%)")
    for sc, c in cov.items():
        print(f"  {sc:13s} n={c['n']:>7,}  band {100 * c['coverage']:.0f}%  "
              f"band+count noise {100 * c['coverage_with_count_noise']:.0f}%  widen x{c['conformal_widen_factor']}")
    print("\nCONFORMAL BANDS (cross-fitted coverage, target 90%)")
    for link, c in [("MODIS -> S-NPP", conf_modis), ("S-NPP -> NOAA-20", conf_snpp)]:
        print(f"  {link:17s} " + "  ".join(f"{sc} {100 * v:.0f}%" for sc, v in c["coverage_crossfit"].items()))
        print(f"  {'':17s} 5° block by latitude: " + "  ".join(f"{st} {100 * v:.0f}%" for st, v in c["coverage_block_by_latitude"].items()))
        print(f"  {'':17s} half-width by size bin: " + ", ".join(
            f"<{edges[i + 1] or 'inf'}: ±{100 * (np.exp(q) - 1):.0f}%" for i, q in enumerate(c["table"])))
    print(f"  S-NPP -> NOAA-20 WAPE: " + "  ".join(f"{sc} {100 * v:.1f}%" for sc, v in snpp_err.items()))
    print("\nJUMPS  (change in mean 2012-14 vs 2009-11; MODIS = the real change)")
    for j in jumps[:12]:
        print(f"  {j['region']:26s} MODIS {100 * j['step2012_modis']:+5.0f}%  raw merged {100 * j['step2012_raw_merged']:+6.0f}%  "
              f"harmonized {100 * j['step2012_harmonized']:+5.0f}%  | vegetation only: MODIS "
              f"{100 * j['step2012_modis_vegetation']:+4.0f}% harm {100 * j['step2012_harmonized_vegetation']:+4.0f}% "
              f"(industrial {100 * j['industrial_share']:.0f}%)  | 2018: S-NPP {100 * j['step2018_snpp']:+4.0f}% "
              f"harm {100 * j['step2018_harmonized']:+4.0f}%  | Pettitt raw {j['pettitt_raw']['year']} p={j['pettitt_raw']['p']:.3f}, "
              f"harm {j['pettitt_harmonized']['year']} p={j['pettitt_harmonized']['p']:.3f}")
    print(f"\n2012 JUMP GAP by method (method's step minus MODIS's real step; 0 = perfect), "
          f"FRP slopes {jv['frp_slopes']}")
    print(f"  {'region':26s} {'MODIS':>7s} {'ratio':>7s} {'+FRP':>7s} {'+QM':>7s}")
    for region, r in sorted(jv["regions"].items(), key=lambda kv: kv[0] != "World"):
        print(f"  {region:26s} {100 * r['modis']:+6.0f}% {100 * r['ratio']:+6.0f}  {100 * r['frp']:+6.0f}  {100 * r['qm']:+6.0f}")
    print(f"  mean |gap| over regions:   ratio {100 * jv['mean_abs_gap']['ratio']:.1f}  "
          f"FRP {100 * jv['mean_abs_gap']['frp']:.1f}  QM {100 * jv['mean_abs_gap']['qm']:.1f} points")
    print(f"\nwrote {DER / 'proof.json'}  [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
