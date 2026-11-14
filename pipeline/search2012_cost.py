"""Stage 4 of the full-grid search: what would it cost to pass each check that still fails?

    python pipeline/search2012_cost.py

For each failing preset, the combinations that pass it with no regression (Punjab, Haryana, Northern
Territory, Patagonia, California still passing; the shipped industrial settings on top) are scored on
the guard rails: held-out error, the 6 held-back areas and Punjab / Haryana season months.
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
from calibrate import CFG, DEFAULT_CFG  # noqa: E402
from search2012_rank import cfg_of  # noqa: E402
from search2012_season import area_units, season_ratios  # noqa: E402

EVAL = ROOT / "data" / "eval"
FAILING = ["cordoba", "mato-grosso", "yakutia", "central-kalimantan"]
KEEP = ["punjab", "haryana", "northern-territory", "patagonia", "california"]
H = ["ZMB", "UKR", "IND-3261", "CAN-631", "PRY", "AUS-2657"]


def main(per: int = 40) -> None:
    mask = CFG["static_mask"]
    g = pd.read_parquet(EVAL / f"grid2012_{mask}.parquet")
    E = S.Engine(mask)
    units = area_units(E, mask)
    st = {False: None, True: S.fit(E.L, "k_ta", True, DEFAULT_CFG | CFG).reshape(-1)}  # shipped industrial fit
    fast = {k: E.fast(E.ks(json.loads(k))[0]) for k in g.ks.unique()}
    ship_season = season_ratios(E, units, DEFAULT_CFG | CFG)
    ship_err = max(abs(np.log(v)) for v in ship_season.values())
    out = []
    seen = set()
    for target in FAILING:
        cand = g[g[f"L_{target}"].abs() <= 0.08].copy()  # rescored below with the shipped industrial fit
        cand["d"] = cand[f"L_{target}"].abs()
        for _, r in cand.sort_values("d").head(4 * per).iterrows():
            key = (int(r.i), r.ks)
            if key in seen:
                continue
            cfg = DEFAULT_CFG | cfg_of(r) | {"static_cfg": CFG["static_cfg"]}
            veg = S.fit(E.L, "k_ta", False, cfg).reshape(-1)
            lo, rg = E.score_fast(fast[r.ks], {False: veg, True: st[True]})
            if abs(lo[target]) > 0.05 or any(abs(lo[k]) > 0.05 for k in KEEP):
                continue
            seen.add(key)
            e_r, e_c, _ = E.heldout(cfg)
            sr = season_ratios(E, units, cfg)
            out.append({"target": target, "i": int(r.i), "ks": r.ks, "cfg": json.dumps({k: cfg[k] for k in
                        ["matched", "fit_years", "year_tau", "n0", "n0_block", "mid", "month_pool", "pool_mode", "pool_levels", "dn_pool", "mass", "n0_mass"]}),
                        "presets_pass": int(sum(abs(v) <= 0.05 for a, v in lo.items() if a not in H)),
                        "regions_within5": int(sum(abs(v) <= 0.05 for v in rg.values())),
                        "heldout_region": e_r, "heldout_cell": e_c,
                        "hold_within5": int(sum(abs(lo[h]) <= 0.05 for h in H)), "hold_mean": float(np.mean([abs(lo[h]) for h in H])),
                        "season_err": float(max(abs(np.log(v)) for v in sr.values())), "punjab_apr": sr["punjab_m4"], "punjab_sep": sr["punjab_m9"],
                        **{f"L_{a}": float(v) for a, v in lo.items()}})
            if sum(o["target"] == target for o in out) >= per:
                break
    d = pd.DataFrame(out)
    d.to_parquet(EVAL / "grid2012_cost.parquet", index=False)
    print(f"shipped: held-out 4.03% region, season error {ship_err:.2f} (Punjab Apr {ship_season['punjab_m4']:.2f}), held-back 5/6 mean 2.35%")
    for t, x in d.groupby("target"):
        best = x.sort_values(["heldout_region"]).iloc[0]
        clean = x[(x.heldout_region <= 0.0418) & (x.hold_within5 >= 5) & (x.season_err <= ship_err + 0.05)]
        print(f"\n{t}: {len(x)} combinations pass it with no regression; with every guard rail held: {len(clean)}")
        print(f"   lowest held-out among them: {100 * best.heldout_region:.2f}% region, held-back {best.hold_within5}/6 mean "
              f"{100 * best.hold_mean:.1f}%, season error {best.season_err:.2f} (Punjab Apr {best.punjab_apr:.2f}, Sep {best.punjab_sep:.2f}) | {best.cfg}")


if __name__ == "__main__":
    main()
