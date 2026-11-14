"""Stage 2b: does a candidate keep the season shape right where the app's flagship areas are?

    python pipeline/search2012_season.py        # reads data/eval/grid2012_ranked.parquet

For Punjab and Haryana, leave-one-year-out (2012-2019) predicted / actual S-NPP counts per calendar
month, vegetation fires on matched days. Season start and critical months are what the app shows,
so a setting that passes more 2012 checks but bends these months is not an improvement.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import jump2012 as J  # noqa: E402
import search2012 as S  # noqa: E402
from calibrate import DEFAULT_CFG  # noqa: E402
from search2012_rank import cfg_of  # noqa: E402

EVAL = ROOT / "data" / "eval"
MONTHS = [3, 4, 5, 9, 10, 11]
SHIPPED = {"static_mask": "persistent+typed_vbuffer", "factor_cfg": {"k_s": {"n0": 30000.0, "n0_block": 30000.0}}}


def area_units(E: S.Engine, mask: str):
    ac = pd.read_parquet(EVAL / "area_counts.parquet")
    ac = ac[ac.area.isin(["punjab", "haryana"]) & ~J.static_of(ac, mask) & ac.y.between(2012, 2019) & ((ac["flags"] & 7) == 7)]
    out = {}
    for a, d in ac.groupby("area"):
        ci = np.searchsorted(E.L.cells, d.cell.to_numpy())
        flat = ci * 24 + (d.m.to_numpy().astype(int) - 1) * 2 + d.dn.map(S.DN).to_numpy()
        yi = d.y.to_numpy() - 2012
        M = np.zeros((8, E.L.nc * 24)); V = np.zeros((8, E.L.nc * 24))
        mod = d.sat.isin(["Terra", "Aqua"]).to_numpy()
        np.add.at(M, (yi[mod], flat[mod]), d.n.to_numpy()[mod])
        sn = (d.sat == "SNPP").to_numpy()
        np.add.at(V, (yi[sn], flat[sn]), d.n.to_numpy()[sn])
        out[a] = (M, V)
    return out


def season_ratios(E, units, cfg):
    ks = [S.fit(E.L, "k_ta", False, cfg, drop_year=y).reshape(-1) for y in range(2012, 2020)]
    month_of = np.tile(np.repeat(np.arange(1, 13), 2), E.L.nc)
    res = {}
    for a, (M, V) in units.items():
        P = np.stack([M[i] * ks[i] for i in range(8)])
        for m in MONTHS:
            sel = month_of == m
            res[f"{a}_m{m}"] = P[:, sel].sum() / max(V[:, sel].sum(), 1)
    return res


def main(top: int = 60) -> None:
    c = pd.read_parquet(EVAL / "grid2012_ranked.parquet")
    c = c[c.ok_heldout].sort_values(["leftover_checks", "heldout_region"], ascending=[False, True]).head(top)
    rows = []
    for mask in list(dict.fromkeys(list(c["mask"]) + [SHIPPED["static_mask"]])):
        E = S.Engine(mask)
        units = area_units(E, mask)
        if mask == SHIPPED["static_mask"]:
            rows.append({"name": "SHIPPED", "mask": mask, **season_ratios(E, units, DEFAULT_CFG | SHIPPED)})
            rows.append({"name": "NOV14", "mask": mask, **season_ratios(E, units, DEFAULT_CFG | {"static_mask": mask})})
        for idx, r in c[c["mask"] == mask].iterrows():
            rows.append({"name": str(idx), "mask": mask, **season_ratios(E, units, DEFAULT_CFG | cfg_of(r))})
        del E
    s = pd.DataFrame(rows)
    cols = [k for k in s.columns if "_m" in k]
    s["season_err"] = np.abs(np.log(s[cols].astype(float))).max(1)
    s.to_parquet(EVAL / "grid2012_season.parquet", index=False)
    print(s.round(2).to_string())


if __name__ == "__main__":
    main()
