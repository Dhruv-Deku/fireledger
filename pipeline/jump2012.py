"""Leftover 2012 jump: area extracts for fast what-if runs of the factor table.

    python pipeline/jump2012.py extract    # all hotspots per 1 deg cell x year x month x day/night x satellite x
                                           # observing satellites x 3 static labels -> data/eval/cc_x.parquet
    python pipeline/jump2012.py areas      # hotspots of the 9 presets + 6 held-back areas, 2003-2019,
                                           # counted per area x 1 deg cell x year x month x day/night x
                                           # satellite x static flags x observing satellites
                                           # -> data/eval/area_counts.parquet

harmonize() applies a factor table exactly like api/weights.py does per hotspot, so a candidate
calib can be scored on the presets in seconds instead of a 3-minute rebuild + API restart.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipeline"))
DER = ROOT / "data" / "derived"
EVAL = ROOT / "data" / "eval"
HOLDOUT = ["ZMB", "UKR", "IND-3261", "CAN-631", "PRY", "AUS-2657"]  # never scored while tuning
YEARS = range(2003, 2020)
CELL = "(least(CAST(floor(h.lat) AS INT), 89) + 90) * 360 + (least(CAST(floor(h.lon) AS INT), 179) + 180)"


def area_geoms(api: str = "http://localhost:8000") -> dict:
    from api.main import parse_geometry
    with urllib.request.urlopen(api + "/api/presets", timeout=120) as r:
        out = {p["id"]: parse_geometry(p["geometry"]) for p in json.load(r)}
    reg = pd.read_parquet(DER / "regions.parquet", columns=["id", "geojson"]).set_index("id").geojson
    out |= {rid: parse_geometry(json.loads(reg[rid])) for rid in HOLDOUT}
    return out


TYPED_SHARE, TYPED_MIN = 0.5, 5  # a 0.05 deg cell is a static location if either sensor family types
                                 # at least this share of its detections there as FIRMS type 1-3


def typed_table(con) -> None:
    src = (EVAL / "typed05.parquet").as_posix()
    sel = lambda sh: f"""SELECT cy, cx FROM read_parquet('{src}')
        WHERE (n_m >= {TYPED_MIN} AND t_m >= {sh} * n_m) OR (n_v >= {TYPED_MIN} AND t_v >= {sh} * n_v)"""  # noqa: E731
    con.execute(f"CREATE OR REPLACE TABLE typed AS {sel(TYPED_SHARE)}")
    con.execute(f"CREATE OR REPLACE TABLE typed30 AS {sel(0.3)}")
    con.execute("""CREATE OR REPLACE TABLE typedb AS SELECT DISTINCT cy + dy AS cy, cx + dx AS cx
                   FROM typed, range(-1, 2) t1(dy), range(-1, 2) t2(dx)""")
    # margin only around places VIIRS (375 m, precise geolocation) types as static
    con.execute(f"""CREATE OR REPLACE TABLE typedvb AS SELECT cy, cx FROM typed UNION
                   SELECT cy + dy, cx + dx FROM read_parquet('{src}'), range(-1, 2) t1(dy), range(-1, 2) t2(dx)
                   WHERE n_v >= {TYPED_MIN} AND t_v >= {TYPED_SHARE} * n_v""")


LABELS = """coalesce(h.type, 0) <> 0 AS ft, p.iy IS NOT NULL AS pz, t.cy IS NOT NULL AS tz,
                   tb.cy IS NOT NULL AS tzb, t3.cy IS NOT NULL AS tz3, tv.cy IS NOT NULL AS tzv"""
LABEL_JOINS = """LEFT JOIN pers p ON p.iy = CAST(floor(h.lat * 100) AS INT) AND p.ix = CAST(floor(h.lon * 100) AS INT)
            LEFT JOIN typed t ON t.cy = CAST(floor(h.lat * 20) AS INT) AND t.cx = CAST(floor(h.lon * 20) AS INT)
            LEFT JOIN typedb tb ON tb.cy = CAST(floor(h.lat * 20) AS INT) AND tb.cx = CAST(floor(h.lon * 20) AS INT)
            LEFT JOIN typed30 t3 ON t3.cy = CAST(floor(h.lat * 20) AS INT) AND t3.cx = CAST(floor(h.lon * 20) AS INT)
            LEFT JOIN typedvb tv ON tv.cy = CAST(floor(h.lat * 20) AS INT) AND tv.cx = CAST(floor(h.lon * 20) AS INT)"""


def build_cc() -> None:
    """Like calibrate.build_cellcounts, but every static label kept apart so masks can be combined later."""
    from api.weights import create_pers
    t0 = time.time()
    con = duckdb.connect()
    con.execute("SET memory_limit = '3GB'")
    con.execute(f"SET temp_directory = '{(ROOT / 'data' / 'tmp').as_posix()}'")
    con.execute("SET preserve_insertion_order = false")
    con.execute(f"CREATE TABLE avail AS SELECT * FROM read_parquet('{(DER / 'avail.parquet').as_posix()}')")
    create_pers(con, DER)
    typed_table(con)
    files = [p.as_posix() for p in sorted((ROOT / "data" / "hotspots").glob("*.parquet"))
             if int(p.stem.rsplit("_", 1)[1]) <= 2025]
    con.execute(f"""
        COPY (
            SELECT {CELL} AS cell, year(h.acq_date)::SMALLINT AS y, month(h.acq_date)::TINYINT AS m, h.dn, h.sat,
                   (a.has_terra::INT + 2 * a.has_aqua::INT + 4 * a.has_snpp::INT + 8 * a.has_noaa20::INT)::TINYINT AS flags,
                   {LABELS}, count(*)::INT AS n
            FROM read_parquet({files}) h JOIN avail a ON a.d = h.acq_date {LABEL_JOINS}
            WHERE h.sat <> 'NOAA21'
            GROUP BY ALL
        ) TO '{(EVAL / "cc_x.parquet").as_posix()}' (FORMAT parquet, COMPRESSION zstd)
    """)
    n = con.execute(f"SELECT count(*), sum(n) FROM '{(EVAL / 'cc_x.parquet').as_posix()}'").fetchone()
    print(f"wrote cc_x: {n[0]:,} rows, {int(n[1]):,} hotspots  [{time.time() - t0:.0f}s]")


def build_areas() -> None:
    from api.main import MASK_RES, cell_mask, pip_sql
    from api.weights import create_pers
    t0 = time.time()
    EVAL.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET memory_limit = '2GB'")
    con.execute(f"SET temp_directory = '{(ROOT / 'data' / 'tmp').as_posix()}'")
    con.execute(f"CREATE TABLE avail AS SELECT * FROM read_parquet('{(DER / 'avail.parquet').as_posix()}')")
    create_pers(con, DER)
    typed_table(con)
    files = [p.as_posix() for p in sorted((ROOT / "data" / "hotspots").glob("*.parquet"))
             if int(p.stem.rsplit("_", 1)[1]) in YEARS]
    parts = []
    for aid, geom in area_geoms().items():
        minx, miny, maxx, maxy = geom.bounds
        con.register("aoi", cell_mask(geom))
        inv = int(round(1 / MASK_RES))
        df = con.execute(f"""
            SELECT {CELL} AS cell, year(h.acq_date)::SMALLINT AS y, month(h.acq_date)::TINYINT AS m, h.dn, h.sat,
                   {LABELS},
                   (a.has_terra::INT + 2 * a.has_aqua::INT + 4 * a.has_snpp::INT + 8 * a.has_noaa20::INT)::TINYINT AS flags,
                   count(*) AS n
            FROM read_parquet({files}) h
            JOIN aoi m ON m.cy = CAST(floor(h.lat * {inv}) AS INT) AND m.cx = CAST(floor(h.lon * {inv}) AS INT)
            JOIN avail a ON a.d = h.acq_date
            {LABEL_JOINS}
            WHERE h.lat BETWEEN {miny} AND {maxy} AND h.lon BETWEEN {minx} AND {maxx}
              AND (m.inside OR {pip_sql(geom)})
            GROUP BY ALL
        """).df()
        con.unregister("aoi")
        parts.append(df.assign(area=aid, holdout=aid in HOLDOUT))
        print(f"  {aid}: {int(df.n.sum()):,} hotspots  [{time.time() - t0:.0f}s]", flush=True)
    out = pd.concat(parts, ignore_index=True)
    out.to_parquet(EVAL / "area_counts.parquet", index=False)
    print(f"wrote {EVAL / 'area_counts.parquet'} ({len(out):,} rows)  [{time.time() - t0:.0f}s]")


BIT = {"Terra": 1, "Aqua": 2, "SNPP": 4, "NOAA20": 8}


def weights(df: pd.DataFrame, calib: pd.DataFrame, static: pd.Series) -> np.ndarray:
    """api/weights.py weight_sql('viirs'), vectorised: df has cell, m, dn, sat, flags."""
    c = df[["cell", "m", "dn"]].merge(calib, on=["cell", "m", "dn"], how="left")

    def k(name):
        return np.where(static, c[f"{name}_st"], c[name])
    f = df["flags"].to_numpy()
    t, a, s, n = (f & 1) > 0, (f & 2) > 0, (f & 4) > 0, (f & 8) > 0
    sat = df.sat.to_numpy()
    ks = k("k_s")
    w = np.select(
        [sat == "NOAA20", sat == "SNPP", sat == "Terra", sat == "Aqua"],
        [np.ones(len(df)),
         np.where(n, 0.0, ks),
         np.where(s | n, 0.0, np.where(a, k("k_ta") * ks, k("k_t") * ks)),
         np.where(s | n, 0.0, np.where(t, k("k_ta") * ks, k("k_a") * ks))], 0.0)
    return np.nan_to_num(w.astype(float))


def leftover(df: pd.DataFrame, w: np.ndarray, pre=range(2008, 2012), post=range(2012, 2016)) -> float:
    h = pd.Series(w * df.n.to_numpy()).groupby(df.y.to_numpy()).sum()
    mod = df.n.where(df.sat.isin(["Terra", "Aqua"]), 0).groupby(df.y).sum()
    h, mod = h.reindex(list(pre) + list(post), fill_value=0), mod.reindex(list(pre) + list(post), fill_value=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return float((h[list(post)].sum() / h[list(pre)].sum()) / (mod[list(post)].sum() / mod[list(pre)].sum()) - 1)


if __name__ == "__main__":
    if sys.argv[1:2] == ["areas"]:
        build_areas()
    elif sys.argv[1:2] == ["extract"]:
        build_cc()


# ---------------------------------------------------------------- scoring a config

MASK_LABEL = {"persistent+typed": "tz", "persistent+typed_buffer": "tzb", "persistent+typed30": "tz3",
              "persistent+typed_vbuffer": "tzv"}


def static_of(x: pd.DataFrame, mask: str) -> np.ndarray:
    st = (x.ft | x.pz).to_numpy()
    return st | x[MASK_LABEL[mask]].to_numpy() if mask in MASK_LABEL else st


def load_cc(mask: str) -> pd.DataFrame:
    """cc_x collapsed to calibrate.py's cellcounts layout for one static mask."""
    x = pd.read_parquet(EVAL / "cc_x.parquet")
    x["veg"] = np.where(static_of(x, mask), 0, x.n)
    cc = x.groupby(["cell", "y", "m", "dn", "sat", "flags"], as_index=False, observed=True).agg(
        n_all=("n", "sum"), n_veg=("veg", "sum"))
    return cc.astype({"cell": "int64", "y": "int16", "m": "int8", "flags": "int8", "n_all": "int32", "n_veg": "int32"})


def fit_all(cc: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    from calibrate import FACTORS, STATIC_FACTORS, fit_factor
    cells = cc[["cell", "m", "dn"]].drop_duplicates().reset_index(drop=True)
    calib = cells
    for name, (num, den, y0, y1) in FACTORS.items():
        calib = calib.merge(fit_factor(cc, name, num, den, y0, y1, cells, cfg), on=["cell", "m", "dn"], how="left")
    static = cc.assign(n_veg=cc.n_all - cc.n_veg)
    for name in STATIC_FACTORS:
        num, den, y0, y1 = FACTORS[name]
        if cfg.get("industrial", True):
            f = fit_factor(static, name, num, den, y0, y1, cells, cfg, static=True)
        else:
            f = calib[["cell", "m", "dn", name, f"s_{name}", f"den_{name}"]]
        calib = calib.merge(f.rename(columns={name: f"{name}_st", f"s_{name}": f"s_{name}_st",
                                              f"den_{name}": f"den_{name}_st"}), on=["cell", "m", "dn"], how="left")
    return calib


def heldout(cc: pd.DataFrame, cfg: dict, sub: pd.Series) -> dict:
    """validate.py's leave-one-year-out FULL method and its cross-fitted conformal coverage."""
    from calibrate import fit_factor
    from validate import MODIS, TEST_YEARS, conformal, held_out, residuals, wape
    H = {y: held_out(cc, y) for y in TEST_YEARS}
    allkeys = pd.concat(H.values())[["cell", "m", "dn"]].drop_duplicates().reset_index(drop=True)
    parts = []
    for y in TEST_YEARS:
        win = cc[(cc.y >= 2012) & (cc.y <= 2019) & (cc.y != y)]
        f = H[y][["cell", "m", "dn"]].merge(fit_factor(win, "k_ta", ["SNPP"], MODIS, 2012, 2019, allkeys, cfg),
                                            on=["cell", "m", "dn"], how="left")
        parts.append(H[y].assign(P=H[y].M * f.k_ta.to_numpy(), y=y,
                                 region=sub.reindex(H[y].cell.to_numpy()).fillna("Other").to_numpy()))
    full = pd.concat(parts)
    e_r, _ = wape(full, ["y", "region", "m"])
    e_c, b_c = wape(full, ["y", "cell", "m"])
    cov = conformal(residuals(full))["coverage_crossfit"]
    # the S-NPP -> NOAA-20 link, leave-one-year-out 2018-2025 (validate.snpp_link with this cfg)
    sp = []
    for y in range(2018, 2026):
        test = held_out(cc, y, ["NOAA20"], ["SNPP"])
        keys = test[["cell", "m", "dn"]]
        f = keys.merge(fit_factor(cc[(cc.y >= 2018) & (cc.y != y)], "k_s", ["NOAA20"], ["SNPP"], 2018, 2025, keys, cfg),
                       on=["cell", "m", "dn"], how="left")
        sp.append(test.assign(P=test.M * f.k_s.to_numpy(), y=y,
                              region=sub.reindex(test.cell.to_numpy()).fillna("Other").to_numpy()))
    sp = pd.concat(sp)
    return {"heldout_region": e_r, "heldout_cell": e_c, "bias_cell": b_c, "coverage_region": cov["region_month"],
            "coverage": cov, "snpp_region": wape(sp, ["y", "region", "m"])[0], "snpp_cell": wape(sp, ["y", "cell", "m"])[0]}


def region_gaps(cc: pd.DataFrame, calib: pd.DataFrame, sub: pd.Series) -> dict:
    t = cc[(cc.y >= 2009) & (cc.y <= 2014)].reset_index(drop=True)
    w = weights(t, calib, np.zeros(len(t), bool)) * t.n_veg.to_numpy()
    mod = np.where(t.sat.isin(["Terra", "Aqua"]), t.n_veg, 0)
    reg = sub.reindex(t.cell.to_numpy()).fillna("Other").to_numpy()
    a = pd.DataFrame({"region": reg, "y": t.y, "h": w, "m": mod}).groupby(["region", "y"])[["h", "m"]].sum()
    out = {}
    for r, d in a.groupby(level=0):
        d = d.droplevel(0)
        if r == "Other":
            continue
        st = lambda s: s.loc[2012:2014].mean() / s.loc[2009:2011].mean() - 1  # noqa: E731
        out[r] = float(st(d.h) - st(d.m))
    return out


def preset_leftovers(ac: pd.DataFrame, calib: pd.DataFrame, mask: str, holdout: bool = False) -> dict:
    out = {}
    for aid, d in ac[ac.holdout == holdout].groupby("area", sort=False):
        d = d.reset_index(drop=True)
        out[aid] = leftover(d, weights(d, calib, static_of(d, mask)))
    return out


REGIONS20 = ["Middle Africa", "Eastern Africa", "South America", "Western Africa", "Eastern Europe",
             "Australia and New Zealand", "South-Eastern Asia", "Northern America", "Southern Asia", "Eastern Asia",
             "Central America", "Southern Africa", "Northern Africa", "Central Asia", "Western Asia", "Southern Europe",
             "Caribbean", "Western Europe", "Melanesia", "Northern Europe"]
LOG = EVAL / "experiments_2012.jsonl"


class Scorer:
    """Scores configs on everything the checks look at; caches the per-mask count tables."""

    def __init__(self):
        from validate import cell_subregions
        self.cc: dict[str, pd.DataFrame] = {}
        self.ac = pd.read_parquet(EVAL / "area_counts.parquet")
        x = pd.read_parquet(EVAL / "cc_x.parquet", columns=["cell"])
        self.sub = cell_subregions(np.sort(x.cell.unique()))

    def run(self, name: str, flags: str, cfg: dict, holdout: bool = False) -> dict:
        from calibrate import DEFAULT_CFG
        t0 = time.time()
        cfg = {**DEFAULT_CFG, **cfg}
        mask = cfg["static_mask"]
        if mask not in self.cc:
            self.cc[mask] = load_cc(mask)
        cc = self.cc[mask]
        calib = fit_all(cc, cfg)
        rg = region_gaps(cc, calib, self.sub)
        rg = {r: rg[r] for r in REGIONS20}
        pl = preset_leftovers(self.ac, calib, mask)
        ho = heldout(cc, cfg, self.sub)
        checks = {"regions": all(abs(v) <= 0.05 for v in rg.values()),
                  **{f"preset {k}": abs(v) <= 0.05 for k, v in pl.items()},
                  "heldout": ho["heldout_region"] <= 0.05, "coverage": 0.85 <= ho["coverage_region"] <= 0.95}
        res = {"run": name, "flags": flags, "cfg": cfg, "passed": sum(checks.values()) + 9, "of": 21,
               "failing": [k for k, v in checks.items() if not v],
               "presets": {k: round(v, 4) for k, v in pl.items()}, "regions": {k: round(v, 4) for k, v in rg.items()},
               "regions_within_5": sum(abs(v) <= 0.05 for v in rg.values()),
               "mean_abs_presets": float(np.mean([abs(v) for v in pl.values()])),
               "mean_abs_regions": float(np.mean([abs(v) for v in rg.values()])),
               **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in ho.items()},
               "seconds": round(time.time() - t0)}
        if holdout:
            res["holdout"] = {k: round(v, 4) for k, v in preset_leftovers(self.ac, calib, mask, True).items()}
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(res) + "\n")
        bad = {k: v for k, v in rg.items() if abs(v) > 0.05}
        print(f"{name:28s} {res['passed']:2d}/21  held-out {100 * ho['heldout_region']:.2f}% cell {100 * ho['heldout_cell']:.2f}% "
              f"cov {100 * ho['coverage_region']:.1f}%  k_s {100 * ho['snpp_region']:.2f}/{100 * ho['snpp_cell']:.1f}%  regions {res['regions_within_5']}/20 "
              f"(mean|gap| {100 * res['mean_abs_regions']:.1f})  presets mean| | {100 * res['mean_abs_presets']:.1f}  [{res['seconds']}s]\n"
              f"{'':28s} presets " + " ".join(f"{k[:6]} {100 * v:+.1f}" for k, v in pl.items())
              + ("\n" + " " * 28 + " regions off: " + ", ".join(f"{k} {100 * v:+.1f}" for k, v in bad.items()) if bad else ""),
              flush=True)
        return res
