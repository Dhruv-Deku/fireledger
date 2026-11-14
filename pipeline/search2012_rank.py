"""Stage 2 of the full-grid search: rank every combination, add held-out errors for the best ones.

    python pipeline/search2012_rank.py          # reads data/eval/grid2012_*.parquet

A combination's leftover checks come from the grid (10: the region check + 9 presets). The best
ones get leave-one-year-out held-out errors (region x month, cell x month) from the same engine; the
partner's limits are held-out <= 5% (aim <= 4.18%) and band coverage 85-95% (checked in stage 3 by
the real pipeline, jump2012.Scorer).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import search2012 as S  # noqa: E402
from calibrate import DEFAULT_CFG  # noqa: E402

EVAL = ROOT / "data" / "eval"
PRESETS = ["punjab", "haryana", "patagonia", "cordoba", "mato-grosso", "california", "northern-territory",
           "yakutia", "central-kalimantan"]
HOLD = ["ZMB", "UKR", "IND-3261", "CAN-631", "PRY", "AUS-2657"]
BEFORE_HOLD = {"ZMB": -0.0039, "UKR": -0.0148, "IND-3261": -0.0637, "CAN-631": 0.1078, "PRY": -0.0205, "AUS-2657": -0.0109}
PASSING_BEFORE = ["punjab", "haryana", "northern-territory"]  # must not regress
CFG_KEYS = ["matched", "fit_years", "year_tau", "n0", "n0_block", "mid", "month_pool", "pool_mode", "pool_levels",
            "dn_pool", "mass", "n0_mass"]


def cfg_of(row) -> dict:
    cfg = {}
    for k in CFG_KEYS:
        v = row[k]
        if isinstance(v, str) and v.startswith("["):
            v = json.loads(v)
        if isinstance(v, float) and np.isnan(v):
            v = None
        cfg[k] = v
    cfg["static_mask"] = row["mask"]
    return cfg | json.loads(row["ks"])


def main(top: int = 400) -> None:
    g = pd.concat([pd.read_parquet(f) for f in sorted(EVAL.glob("grid2012_*.parquet"))], ignore_index=True)
    g["region_check"] = g.n_regions == 20
    g["leftover_checks"] = g.n_presets + g.region_check.astype(int)
    g["no_regress"] = np.all([g[f"L_{p}"].abs() <= 0.05 for p in PASSING_BEFORE], axis=0)
    g["hold_within5"] = np.sum([g[f"L_{h}"].abs() <= 0.05 for h in HOLD], axis=0)
    g["hold_mean"] = np.mean([g[f"L_{h}"].abs() for h in HOLD], axis=0)
    print(f"{len(g):,} combinations scored over {g['mask'].nunique()} masks")
    print("best leftover checks (of 10):", int(g.leftover_checks.max()), "| distribution:",
          g.leftover_checks.value_counts().sort_index(ascending=False).head(6).to_dict())
    print("per preset, share of combinations that pass it:",
          {p: f"{100 * (g[f'L_{p}'].abs() <= 0.05).mean():.0f}%" for p in PRESETS})
    print("region check ever passes:", bool(g.region_check.any()), "| best regions within 5:", int(g.n_regions.max()))
    cand = g[g.no_regress].sort_values(["leftover_checks", "mean_presets"], ascending=[False, True]).head(top)
    print(f"held-out for the top {len(cand)} (no regressions) ...", flush=True)
    out = []
    for mask, part in cand.groupby("mask"):
        E = S.Engine(mask)
        for _, r in part.iterrows():
            cfg = DEFAULT_CFG | cfg_of(r)
            e_r, e_c, b = E.heldout(cfg)
            out.append(r.to_dict() | {"heldout_region": e_r, "heldout_cell": e_c, "bias": b})
        del E
    c = pd.DataFrame(out)
    c["ok_heldout"] = c.heldout_region <= 0.05
    c["ok_aim"] = c.heldout_region <= 0.0418
    c["hold_not_worse"] = (c.hold_within5 >= 4) & (c.hold_mean <= np.mean([abs(v) for v in BEFORE_HOLD.values()]))
    c = c.sort_values(["ok_heldout", "leftover_checks", "ok_aim", "heldout_region"], ascending=[False, False, False, True])
    c.to_parquet(EVAL / "grid2012_ranked.parquet", index=False)
    show = ["mask", "ks", "matched", "fit_years", "year_tau", "n0", "n0_block", "mid", "month_pool", "pool_mode",
            "pool_levels", "dn_pool", "mass", "n0_mass", "leftover_checks", "n_regions", "heldout_region", "heldout_cell",
            "hold_within5", "hold_mean"] + [f"L_{p}" for p in PRESETS]
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print(c[show].head(25).to_string())


if __name__ == "__main__":
    main()
