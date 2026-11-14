"""Stage 1b of the full-grid search: separate settings for the industrial / persistent (*_st) factors.

    python pipeline/search2012_static.py [mask]     # after search2012.py grid

Static factors only touch static rows, so they change the preset leftovers (industrial sites are 3-18%
of a preset's hotspots) but not the vegetation region check or the held-out error. For the best
vegetation settings from the main grid, every static setting is tried on top.
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
sys.path.insert(0, str(ROOT / "pipeline"))
import search2012 as S  # noqa: E402
from calibrate import DEFAULT_CFG  # noqa: E402
from search2012_rank import CFG_KEYS, PASSING_BEFORE, cfg_of  # noqa: E402

EVAL = ROOT / "data" / "eval"


def static_grid():
    for (ms, nm), n0, mid, (pw, pm), dnw, fy in itertools.product(
            [(False, 300.0), (True, 100.0), (True, 300.0), (True, 1000.0), (True, 3000.0), (True, 10000.0)],
            [3.0, 30.0, 300.0], [None, 20], [(0.0, "sum"), (0.25, "ratio")], [0.0, 0.5], [None, [2012, 2015]]):
        yield {"matched": True, "fit_years": fy, "year_tau": None, "n0": n0, "n0_block": 30.0, "mid": mid,
               "month_pool": pw, "pool_mode": pm, "pool_levels": ["block", "cell"], "dn_pool": dnw, "mass": ms, "n0_mass": nm}


def main(mask: str, top: int = 150) -> None:
    t0 = time.time()
    g = pd.read_parquet(EVAL / f"grid2012_{mask}.parquet")
    g["leftover_checks"] = g.n_presets + (g.n_regions == 20).astype(int)
    # best vegetation settings: by region checks first (static factors can't move them), then presets
    g["no_regress_veg"] = np.all([g[f"L_{p}"].abs() <= 0.08 for p in PASSING_BEFORE], axis=0)
    base = g[g.no_regress_veg].sort_values(["n_regions", "leftover_checks", "mean_presets"], ascending=[False, False, True])
    base = base.drop_duplicates("i").head(top)
    E = S.Engine(mask)
    ks_list = {k: E.fast(E.ks(json.loads(k))[0]) for k in g.ks.unique()}
    statics = list(static_grid())
    kst = [S.fit(E.L, "k_ta", True, DEFAULT_CFG | sc).reshape(-1) for sc in statics]
    print(f"[{mask}] {len(base)} vegetation settings x {len(statics)} static settings x {len(ks_list)} k_s  [{time.time() - t0:.0f}s]", flush=True)
    rows = []
    for _, r in base.iterrows():
        cfg = DEFAULT_CFG | cfg_of(r)
        kveg = S.fit(E.L, "k_ta", False, cfg).reshape(-1)
        for ksk, F in ks_list.items():
            for j, (sc, ks_t) in enumerate(zip(statics, kst)):
                lo, rg = E.score_fast(F, {False: kveg, True: ks_t})
                pres = {a: v for a, v in lo.items() if not E.holdout[a]}
                rows.append({"mask": mask, "i": int(r.i), "ks": ksk, "j": j, "static_cfg": json.dumps(sc),
                             **{k: r[k] for k in CFG_KEYS},
                             "n_presets": int(sum(abs(v) <= 0.05 for v in pres.values())),
                             "n_regions": int(sum(abs(v) <= 0.05 for v in rg.values())),
                             "mean_presets": float(np.mean([abs(v) for v in pres.values()])),
                             **{f"L_{a}": float(v) for a, v in lo.items()}})
    out = pd.DataFrame(rows)
    out.to_parquet(EVAL / f"grid2012_static_{mask}.parquet", index=False)
    out["lc"] = out.n_presets + (out.n_regions == 20)
    print(f"[{mask}] {len(out):,} combinations; best leftover checks {out.lc.max()} | distribution "
          f"{out.lc.value_counts().sort_index(ascending=False).head(5).to_dict()}  [{time.time() - t0:.0f}s]", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "persistent+typed_vbuffer")
