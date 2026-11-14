"""Full-grid search for the 2012 checks: every combination of the fitting settings, scored in milliseconds.

    python pipeline/search2012.py verify          # the array fit must equal calibrate.fit_factor
    python pipeline/search2012.py grid            # all combinations -> data/eval/grid2012.parquet

calibrate.fit_factor (pandas) is what ships; this module re-implements the same arithmetic on dense
arrays (cell x month x day/night, one slab per year), so one fit takes ~10 ms instead of ~3 s, and
leave-one-year-out fits are a subtraction. It only screens: any winner is re-run through the real
pipeline (jump2012.Scorer, then calibrate/build_grid/validate and the live API).
"""
from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipeline"))
import jump2012 as J  # noqa: E402
from calibrate import BIT, DEFAULT_CFG, FACTORS, MODIS_LINKS, block_of, mid_of  # noqa: E402

EVAL = ROOT / "data" / "eval"
DN = {"D": 0, "N": 1}


class Links:
    """Dense per-year numerator/denominator slabs for each factor link of one static mask."""

    def __init__(self, cc: pd.DataFrame):
        self.cells = np.sort(cc.cell.unique())
        nc = len(self.cells)
        self.nc = nc
        bl = block_of(self.cells)
        self.bcodes, self.block_of_cell = np.unique(bl, return_inverse=True)
        corner = (self.bcodes // 72) * 5 * 360 + (self.bcodes % 72) * 5
        self.mid_cache = {}
        self._corner = corner
        ci = np.searchsorted(self.cells, cc.cell.to_numpy())
        self.flat = ci * 24 + (cc.m.to_numpy().astype(int) - 1) * 2 + cc.dn.map(DN).to_numpy()
        self.cc = cc
        self.slabs = {}

    def mid(self, deg: int):
        if deg not in self.mid_cache:
            m_cell = mid_of(self.cells, deg)
            codes, inv = np.unique(np.concatenate([m_cell, mid_of(self._corner, deg)]), return_inverse=True)
            self.mid_cache[deg] = (len(codes), inv[:self.nc], inv[self.nc:])
        return self.mid_cache[deg]

    def slab(self, name: str, static: bool, matched: bool):
        """num, den [Y, nc, 12, 2] and the list of years, for factor `name`."""
        key = (name, static, matched)
        if key in self.slabs:
            return self.slabs[key]
        num_s, den_s, y0, y1 = FACTORS[name]
        cc = self.cc
        need = sum(BIT[s] for s in set(num_s) | set(den_s)) if matched else 0
        sel = ((cc.y >= y0) & (cc.y <= y1) & ((cc["flags"] & need) == need)).to_numpy()
        val = (cc.n_all - cc.n_veg if static else cc.n_veg).to_numpy()
        years = list(range(y0, y1 + 1))
        num = np.zeros((len(years), self.nc * 24))
        den = np.zeros((len(years), self.nc * 24))
        yi = cc.y.to_numpy() - y0
        isn, isd = cc.sat.isin(num_s).to_numpy(), cc.sat.isin(den_s).to_numpy()
        for arr, m in [(num, sel & isn), (den, sel & isd)]:
            np.add.at(arr, (yi[m], self.flat[m]), val[m])
        out = (num.reshape(len(years), self.nc, 12, 2), den.reshape(len(years), self.nc, 12, 2), years)
        self.slabs[key] = out
        return out


def _mpool(N, E, w, mode):
    if not w:
        return N, E
    if mode == "sum":
        return (N + w * (np.roll(N, 1, -2) + np.roll(N, -1, -2)),
                E + w * (np.roll(E, 1, -2) + np.roll(E, -1, -2)))
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(E > 0, N / E, np.nan)
    for sh in (1, -1):  # calibrate: the month before first, then the month after
        rn = np.roll(r, sh, -2)
        has = ~np.isnan(rn) & (E > 0)
        N = np.where(has, N + w * E * np.nan_to_num(rn), N)
        E = np.where(has, E * (1 + w), E)
    return N, E


def _dnpool(N, E, w):
    if not w:
        return N, E
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(E > 0, N / E, np.nan)
    ro = r[..., ::-1]
    has = ~np.isnan(ro) & (E > 0)
    return np.where(has, N + w * E * np.nan_to_num(ro), N), np.where(has, E * (1 + w), E)


def _pool(N, E, w, mode, dn_w):
    N, E = _mpool(N, E, w, mode)
    return _dnpool(N, E, dn_w)


def _group(arr, codes, n):
    """Sum [..., nc, 12, 2] over cells into [..., n, 12, 2] by codes."""
    out = np.zeros(arr.shape[:-3] + (n, 12, 2))
    if arr.ndim == 3:
        np.add.at(out, codes, arr)
    else:
        np.add.at(out, (slice(None), codes), arr)
    return out


def fit(L: Links, name: str, static: bool, cfg: dict, drop_year: int | None = None) -> np.ndarray:
    """calibrate.fit_factor on dense slabs -> k [nc, 12, 2]."""
    cfg = {**DEFAULT_CFG, **cfg}
    if name in cfg["n0_factor"]:
        cfg = cfg | dict(zip(["n0", "n0_block"], cfg["n0_factor"][name]))
    cfg = cfg | cfg["factor_cfg"].get(name, {})
    if static:
        cfg = cfg | cfg["static_cfg"]
    num, den, years = L.slab(name, static, cfg["matched"])
    ys = np.array(years)
    sel = np.ones(len(ys), bool)
    if cfg["fit_years"] and name in MODIS_LINKS:
        sel &= (ys >= cfg["fit_years"][0]) & (ys <= cfg["fit_years"][1])
    if drop_year is not None:
        sel &= ys != drop_year
    wy = np.exp(-(ys - 2012) / cfg["year_tau"]) if (cfg["year_tau"] and name in MODIS_LINKS) else np.ones(len(ys))
    w = (wy * sel)[:, None, None, None]
    N0, E0 = (num * w).sum(0), (den * w).sum(0)           # raw sums, cell x m x dn
    pool, dn_w, mode = cfg["month_pool"], cfg["dn_pool"], cfg["pool_mode"]

    Ng, Eg = N0.sum(0), E0.sum(0)
    g_all = N0.sum() / max(E0.sum(), 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        kg = np.where(Eg > 0, Ng / Eg, g_all)
    nb = len(L.bcodes)
    if cfg["mid"]:
        n_mid, mid_cell, mid_block = L.mid(int(cfg["mid"]))
        Nm, Em = _pool(_group(N0, mid_cell, n_mid), _group(E0, mid_cell, n_mid), pool, mode, dn_w)
        kp_mid = (Nm + cfg["n0_mid"] * kg) / (Em + cfg["n0_mid"])
        parent_b = kp_mid[mid_block]
        parent_cell_fallback = kp_mid[mid_cell]
    else:
        parent_b = np.broadcast_to(kg, (nb, 12, 2))
        parent_cell_fallback = None
    Nb0, Eb0 = _group(N0, L.block_of_cell, nb), _group(E0, L.block_of_cell, nb)
    bw = pool if "block" in cfg["pool_levels"] else 0.0
    Nb, Eb = _pool(Nb0, Eb0, bw, mode, dn_w)
    present_b = (Eb0 + Nb0).sum((1, 2)) >= 0  # every block row exists in the dense form
    kb = (Nb + cfg["n0_block"] * parent_b) / (Eb + cfg["n0_block"])
    cw = pool if "cell" in cfg["pool_levels"] else 0.0
    Nc, Ec = _pool(N0, E0, cw, mode, dn_w)
    kbc = kb[L.block_of_cell]
    k = (Nc + cfg["n0"] * kbc) / (Ec + cfg["n0"])
    del present_b, parent_cell_fallback
    if cfg["mass"]:
        P = E0 * k
        Pg, Ng_ = P.sum(0), N0.sum(0)
        with np.errstate(divide="ignore", invalid="ignore"):
            rg = np.where(Pg > 0, Ng_ / Pg, 1.0)
        Pb, Nbm = _group(P, L.block_of_cell, nb), Nb0
        rb = (Nbm + cfg["n0_mass"] * rg) / (Pb + cfg["n0_mass"])
        k = k * rb[L.block_of_cell]
    return k


# ---------------------------------------------------------------- verification against calibrate

def verify() -> None:
    from calibrate import fit_factor
    cc = J.load_cc("persistent+typed_vbuffer")
    L = Links(cc)
    cells = pd.DataFrame({"cell": np.repeat(L.cells, 24), "m": np.tile(np.repeat(np.arange(1, 13), 2), L.nc),
                          "dn": np.tile(["D", "N"], L.nc * 12)}).astype({"m": "int8"})
    tests = [{}, {"n0": 10.0, "n0_block": 300.0}, {"mid": 20}, {"month_pool": 0.25, "pool_levels": ["cell"]},
             {"month_pool": 0.25, "pool_mode": "ratio", "pool_levels": ["block", "cell"]}, {"dn_pool": 0.5},
             {"mass": True, "n0_mass": 1000.0}, {"fit_years": [2012, 2015]}, {"year_tau": 3.0}, {"matched": False},
             {"mid": 20, "month_pool": 0.1, "dn_pool": 0.2, "mass": True, "year_tau": 4.0, "n0": 100.0}]
    for t in tests:
        cfg = DEFAULT_CFG | t
        for name, static in [("k_ta", False), ("k_ta", True), ("k_s", False)]:
            num, den, y0, y1 = FACTORS[name]
            src = cc.assign(n_veg=cc.n_all - cc.n_veg) if static else cc
            ref = fit_factor(src, name, num, den, y0, y1, cells, cfg)[name].to_numpy()
            got = fit(L, name, static, cfg).reshape(-1)
            d = np.nanmax(np.abs(ref - got) / np.maximum(np.abs(ref), 1e-9))
            print(f"{json.dumps(t):70s} {name}{'_st' if static else '':3s} max rel diff {d:.2e}", flush=True)


# ---------------------------------------------------------------- scoring

class Engine:
    """Everything the checks look at, for one static mask, as array gathers."""

    def __init__(self, mask: str):
        from validate import cell_subregions
        self.mask = mask
        cc = J.load_cc(mask)
        self.L = L = Links(cc)
        sub = cell_subregions(L.cells)
        self.regions = J.REGIONS20
        rid = {r: i for i, r in enumerate(self.regions)}
        self.region_of_cell = np.array([rid.get(s, -1) for s in sub.to_numpy()])
        self.sub_codes, self.sub_of_cell = np.unique(sub.to_numpy(), return_inverse=True)
        base = DEFAULT_CFG | {"static_mask": mask}
        self.kt = {st: fit(L, "k_t", st, base).reshape(-1) for st in (False, True)}  # rare days; screening only
        self.ka = {st: fit(L, "k_a", st, base).reshape(-1) for st in (False, True)}
        # preset / held-back area rows, 2008-2015
        ac = pd.read_parquet(EVAL / "area_counts.parquet")
        ac = ac[ac.y.between(2008, 2015)].reset_index(drop=True)
        self.areas = list(dict.fromkeys(ac.area))
        self.holdout = {a: bool(h) for a, h in zip(ac.area, ac.holdout)}
        self.a_area = ac.area.map({a: i for i, a in enumerate(self.areas)}).to_numpy()
        self.a_post = (ac.y >= 2012).to_numpy().astype(int)
        self.a_st = J.static_of(ac, mask)
        self.a_flat, self.a_role = self._roles(ac)
        self.a_n = ac.n.to_numpy().astype(float)
        self.a_mod = np.where(ac.sat.isin(["Terra", "Aqua"]).to_numpy(), self.a_n, 0.0)
        na = len(self.areas)
        self.M_area = np.bincount(self.a_area * 2 + self.a_post, self.a_mod, 2 * na).reshape(na, 2)
        # region rows, 2009-2014, vegetation only
        t = cc[(cc.y >= 2009) & (cc.y <= 2014)].reset_index(drop=True)
        flat, role = self._roles(t)
        ci = np.searchsorted(L.cells, t.cell.to_numpy())
        reg = self.region_of_cell[ci]
        ok = reg >= 0
        is_mod = t.sat.isin(["Terra", "Aqua"]).to_numpy()
        self.r_flat, self.r_role, self.r_reg = flat[ok], role[ok], reg[ok]
        self.r_y = (t.y.to_numpy() - 2009)[ok]
        self.r_n = t.n_veg.to_numpy().astype(float)[ok]
        mod_n = np.where(is_mod[ok], self.r_n, 0.0)
        self.M_reg = np.bincount(self.r_reg * 6 + self.r_y, mod_n, 20 * 6).reshape(20, 6)
        # dense count slabs per weight role, [key, year], so region totals are a few matrix products
        nk = L.nc * 24
        self.C = np.zeros((6, nk, 6))
        np.add.at(self.C, (self.r_role.astype(int), self.r_flat, self.r_y), self.r_n)
        self.key_region = np.repeat(self.region_of_cell, 24)
        self.key_ok = self.key_region >= 0
        # held-out units (matched days, vegetation)
        self.Vt, self.Mt, self.yt = L.slab("k_ta", False, True)
        self.Vs, self.Ms, self.ys = L.slab("k_s", False, True)
        self.ks_cache = {}

    def _roles(self, df):
        L = self.L
        ci = np.searchsorted(L.cells, df.cell.to_numpy())
        flat = ci * 24 + (df.m.to_numpy().astype(int) - 1) * 2 + df.dn.map(DN).to_numpy()
        f = df["flags"].to_numpy()
        t_, a_, s_, n_ = (f & 1) > 0, (f & 2) > 0, (f & 4) > 0, (f & 8) > 0
        sat = df.sat.to_numpy()
        role = np.zeros(len(df), np.int8)
        role[sat == "NOAA20"] = 1
        role[(sat == "SNPP") & ~n_] = 2
        mod_ok = ~(s_ | n_)
        role[(sat == "Terra") & mod_ok & a_] = 3
        role[(sat == "Terra") & mod_ok & ~a_] = 4
        role[(sat == "Aqua") & mod_ok & t_] = 3
        role[(sat == "Aqua") & mod_ok & ~t_] = 5
        return flat, role

    def _w(self, flat, role, st, kta, ks):
        """Per-row weight as api/weights.py; kta / ks map False -> vegetation, True -> static flat arrays."""
        def g(d):
            return np.where(st, d[True][flat], d[False][flat])
        ksr = g(ks)
        return np.select([role == 1, role == 2, role == 3, role == 4, role == 5],
                         [np.ones(len(flat)), ksr, g(kta) * ksr, g(self.kt) * ksr, g(self.ka) * ksr], 0.0)

    def ks(self, ks_cfg: dict):
        key = json.dumps(ks_cfg, sort_keys=True)
        if key not in self.ks_cache:
            cfg = DEFAULT_CFG | ks_cfg
            ks = {st: fit(self.L, "k_s", st, cfg).reshape(-1) for st in (False, True)}
            P = np.stack([self.Ms[i] * fit(self.L, "k_s", False, cfg, drop_year=y) for i, y in enumerate(self.ys)])
            self.ks_cache[key] = (ks, self._wape(P, self.Vs)[0])
        return self.ks_cache[key]

    def fast(self, ks):
        """For a fixed k_s: everything that doesn't depend on k_ta as constants, the rest as sparse matrices,
        so leftovers and region gaps for any k_ta are two sparse products."""
        from scipy import sparse
        nk = self.L.nc * 24
        na = len(self.areas)
        # areas: rows area*2 + period, columns flat key (+ nk for static rows)
        st = self.a_st
        ksr = np.where(st, ks[True][self.a_flat], ks[False][self.a_flat])
        g = lambda d: np.where(st, d[True][self.a_flat], d[False][self.a_flat])  # noqa: E731
        r = self.a_role
        const_w = np.select([r == 1, r == 2, r == 4, r == 5], [np.ones(len(r)), ksr, g(self.kt) * ksr, g(self.ka) * ksr], 0.0)
        a_row = self.a_area * 2 + self.a_post
        a_const = np.bincount(a_row, const_w * self.a_n, 2 * na)
        m3 = r == 3
        A = sparse.csr_matrix(((ksr * self.a_n)[m3], (a_row[m3], (self.a_flat + st * nk)[m3])), shape=(2 * na, 2 * nk))
        # regions (vegetation): rows region*6 + year, columns flat key
        C, s = self.C, ks[False]
        const = C[1] + s[:, None] * (C[2] + self.kt[False][:, None] * C[4] + self.ka[False][:, None] * C[5])
        ok = self.key_ok
        r_const = np.stack([np.bincount(self.key_region[ok], const[ok, j], 20) for j in range(6)], 1)
        keys = np.nonzero(ok)[0]
        rows = (self.key_region[keys][:, None] * 6 + np.arange(6)[None, :]).ravel()
        cols = np.repeat(keys, 6)
        vals = (s[keys][:, None] * C[3][keys]).ravel()
        R = sparse.csr_matrix((vals, (rows, cols)), shape=(120, nk))
        return a_const.reshape(na, 2), A, r_const, R

    def score_fast(self, F, kta):
        a_const, A, r_const, R = F
        na = len(self.areas)
        H = a_const + (A @ np.concatenate([kta[False], kta[True]])).reshape(na, 2)
        lo = (H[:, 1] / H[:, 0]) / (self.M_area[:, 1] / self.M_area[:, 0]) - 1
        Hr = r_const + (R @ kta[False]).reshape(20, 6)
        with np.errstate(divide="ignore", invalid="ignore"):
            sh = Hr[:, 3:].mean(1) / Hr[:, :3].mean(1) - 1
            sm = self.M_reg[:, 3:].mean(1) / self.M_reg[:, :3].mean(1) - 1
        return dict(zip(self.areas, lo)), dict(zip(self.regions, sh - sm))

    def _wape(self, P, V):
        """Region x month and cell x month WAPE and bias over held-out years; P, V are [Y, nc, 12, 2]."""
        Pc, Vc = P.sum(-1), V.sum(-1)
        e_cell = np.abs(Pc - Vc).sum() / Vc.sum()
        n = len(self.sub_codes)
        Pr = np.zeros((P.shape[0], n, 12))
        Vr = np.zeros_like(Pr)
        np.add.at(Pr, (slice(None), self.sub_of_cell), Pc)
        np.add.at(Vr, (slice(None), self.sub_of_cell), Vc)
        return np.abs(Pr - Vr).sum() / Vr.sum(), e_cell, P.sum() / V.sum() - 1

    def leftovers(self, kta, ks):
        w = self._w(self.a_flat, self.a_role, self.a_st, kta, ks) * self.a_n
        na = len(self.areas)
        H = np.bincount(self.a_area * 2 + self.a_post, w, 2 * na).reshape(na, 2)
        lo = (H[:, 1] / H[:, 0]) / (self.M_area[:, 1] / self.M_area[:, 0]) - 1
        return dict(zip(self.areas, lo))

    def region_gaps(self, kta, ks):
        C, s = self.C, ks[False]
        v = C[1] + s[:, None] * (C[2] + (kta[False][:, None] * C[3]) + (self.kt[False][:, None] * C[4])
                                 + (self.ka[False][:, None] * C[5]))
        ok = self.key_ok
        H = np.stack([np.bincount(self.key_region[ok], v[ok, j], 20) for j in range(6)], 1)
        with np.errstate(divide="ignore", invalid="ignore"):
            sh = H[:, 3:].mean(1) / H[:, :3].mean(1) - 1
            sm = self.M_reg[:, 3:].mean(1) / self.M_reg[:, :3].mean(1) - 1
        return dict(zip(self.regions, sh - sm))

    def heldout(self, cfg):
        P = np.stack([self.Mt[i] * fit(self.L, "k_ta", False, cfg, drop_year=y) for i, y in enumerate(self.yt)])
        return self._wape(P, self.Vt)


def kta_grid():
    windows = [(None, None), ([2012, 2015], None), ([2012, 2017], None), (None, 3.0), (None, 6.0)]
    pools = [(0.0, "sum", ["block", "cell"]), (0.1, "sum", ["cell"]), (0.25, "sum", ["cell"]),
             (0.25, "sum", ["block", "cell"]), (0.5, "sum", ["cell"]), (0.25, "ratio", ["cell"]),
             (0.5, "ratio", ["block", "cell"])]
    masses = [(False, 300.0), (True, 1000.0), (True, 3000.0)]
    for matched, (fy, tau), n0, n0b, mid, (pw, pm, pl), dnw, (ms, nm) in itertools.product(
            [True, False], windows, [3.0, 10.0, 30.0, 100.0, 300.0], [30.0, 300.0], [None, 20], pools,
            [0.0, 0.2, 0.5, 1.0], masses):
        yield {"matched": matched, "fit_years": fy, "year_tau": tau, "n0": n0, "n0_block": n0b, "mid": mid,
               "month_pool": pw, "pool_mode": pm, "pool_levels": pl, "dn_pool": dnw, "mass": ms, "n0_mass": nm}


KS_SETTINGS = [{}, {"factor_cfg": {"k_s": {"n0": 3000.0, "n0_block": 3000.0}}},
               {"factor_cfg": {"k_s": {"n0": 30000.0, "n0_block": 30000.0}}},
               {"factor_cfg": {"k_s": {"n0": 1e9, "n0_block": 1e9}}}]


def run_grid(masks: list[str], limit: int | None = None) -> None:
    for mask in masks:
        t0 = time.time()
        E = Engine(mask)
        kss = [(s, *E.ks(s)) for s in KS_SETTINGS]
        kss = [(s, E.fast(k), e) for s, k, e in kss]
        for st in (False, True):
            for matched in (True, False):
                E.L.slab("k_ta", st, matched)
        E.L.cc = None  # slabs are built; free the row table
        print(f"[{mask}] ready in {time.time() - t0:.0f}s; k_s held-out: " + ", ".join(f"{e:.4f}" for _, _, e in kss), flush=True)
        rows = []
        for i, cfg in enumerate(kta_grid()):
            if limit and i >= limit:
                break
            kta = {st: fit(E.L, "k_ta", st, cfg).reshape(-1) for st in (False, True)}
            for ks_cfg, F, ks_err in kss:
                lo, rg = E.score_fast(F, kta)
                pres = {a: v for a, v in lo.items() if not E.holdout[a]}
                rows.append({"mask": mask, "i": i, "ks": json.dumps(ks_cfg),
                             **{k: (json.dumps(v) if isinstance(v, list) else v) for k, v in cfg.items()},
                             "n_presets": int(sum(abs(v) <= 0.05 for v in pres.values())),
                             "n_regions": int(sum(abs(v) <= 0.05 for v in rg.values())),
                             "mean_presets": float(np.mean([abs(v) for v in pres.values()])),
                             "mean_regions": float(np.mean([abs(v) for v in rg.values()])),
                             "ks_heldout": ks_err,
                             **{f"L_{a}": float(v) for a, v in lo.items()}, **{f"R_{r}": float(v) for r, v in rg.items()}})
            if i % 500 == 0:
                print(f"  [{mask}] {i} settings  [{time.time() - t0:.0f}s]", flush=True)
                pd.DataFrame(rows).to_parquet(EVAL / f"grid2012_{mask}.parquet", index=False)
        pd.DataFrame(rows).to_parquet(EVAL / f"grid2012_{mask}.parquet", index=False)
        print(f"[{mask}] done: {len(rows)} combinations  [{time.time() - t0:.0f}s]", flush=True)
        del E


if __name__ == "__main__":
    if sys.argv[1:2] == ["verify"]:
        verify()
    elif sys.argv[1:2] == ["grid"]:
        run_grid(sys.argv[2:] or ["persistent+typed_vbuffer", "persistent+typed_buffer", "persistent+typed",
                                  "persistent+typed30", "persistent"])
