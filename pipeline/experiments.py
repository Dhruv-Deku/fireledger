"""Harmonization experiments beyond the proof pack. Writes data/derived/experiments.json.

E1 noise floor   How much error is pure counting noise? Simulate the actual VIIRS count as
                 Poisson(predicted) and score that "perfect-rate" model the same way.
E2 backcast      Learn factors on 2015-2019 only and predict 2012-2014 (the real job is
                 predicting the past); compare with leave-one-year-out on the same years.
E3 Aqua anchor   Aqua passes at ~13:30 like VIIRS; Terra at ~10:30. Predict VIIRS as
                 a*k_a*Aqua + (1-a)*k_t*Terra instead of k_ta*(Terra+Aqua); a picked on the
                 training years only (nested), scored on the held-out year.
E4 N. America    Where does its 2012 vegetation gap come from: US or Canada?
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from calibrate import BIT, fit_factor  # noqa: E402
from validate import DER, MODIS, TEST_YEARS, cell_subregions, wape  # noqa: E402

ALPHAS = [0.0, 0.25, 0.5, 0.65, 0.8, 1.0]
SCALES = {"cell_month": ["y", "cell", "m"], "region_month": ["y", "region", "m"], "world_month": ["y", "m"]}


def units(cc: pd.DataFrame, y: int) -> pd.DataFrame:
    """S-NPP (V), Terra (T), Aqua (A) per cell x month x day/night in year y, days all three observed."""
    need = BIT["Terra"] | BIT["Aqua"] | BIT["SNPP"]
    t = cc[(cc.y == y) & ((cc["flags"] & need) == need)]
    t = t.assign(V=np.where(t.sat == "SNPP", t.n_veg, 0), T=np.where(t.sat == "Terra", t.n_veg, 0),
                 A=np.where(t.sat == "Aqua", t.n_veg, 0))
    return t.groupby(["cell", "m", "dn"], as_index=False)[["V", "T", "A"]].sum().assign(y=y)


def factors(win: pd.DataFrame, keys: pd.DataFrame) -> pd.DataFrame:
    out = keys
    for name, den in [("k_ta", MODIS), ("k_t", ["Terra"]), ("k_a", ["Aqua"])]:
        f = fit_factor(win, name, ["SNPP"], den, 2012, 2019, keys)[["cell", "m", "dn", name]]
        out = out.merge(f, on=["cell", "m", "dn"], how="left")
    return out


def blend(df: pd.DataFrame, a: float) -> np.ndarray:
    """a*k_a*A + (1-a)*k_t*T, falling back to the one satellite that saw fire."""
    T, A = df["T"], df["A"]  # df.T would be the transpose
    both = a * df.k_a * A + (1 - a) * df.k_t * T
    only_t = (A == 0) & (T > 0)
    only_a = (T == 0) & (A > 0)
    return np.where(only_t, df.k_t * T, np.where(only_a, df.k_a * A, both))


def main() -> None:
    t0 = time.time()
    cc = pd.read_parquet(DER / "cellcounts.parquet", columns=["cell", "y", "m", "dn", "sat", "flags", "n_veg"])
    cc = cc.astype({"cell": "int64", "y": "int16", "m": "int8", "flags": "int8", "n_veg": "int32"})
    sub = cell_subregions(np.sort(cc.cell.unique()))
    U = {y: units(cc, y) for y in TEST_YEARS}
    keys = pd.concat(U.values())[["cell", "m", "dn"]].drop_duplicates()
    region = lambda df: df.assign(region=sub.reindex(df.cell.to_numpy()).fillna("Other").to_numpy())  # noqa: E731
    res: dict = {}

    # ---- E1 + E3: leave-one-year-out with the current method, Aqua-anchored blends, noise floor
    rng = np.random.default_rng(0)
    rows = {"ratio (current)": [], "aqua-anchored (nested a)": [], "noise floor": []}
    rows.update({f"blend a={a}": [] for a in ALPHAS})
    chosen = {}
    for y in TEST_YEARS:
        win = cc[(cc.y >= 2012) & (cc.y <= 2019) & (cc.y != y)]
        f = factors(win, keys)
        test = U[y].merge(f, on=["cell", "m", "dn"], how="left")
        test["M"] = test["T"] + test["A"]
        p_ratio = test.k_ta * test.M
        rows["ratio (current)"].append(test.assign(P=p_ratio))
        rows["noise floor"].append(test.assign(P=p_ratio, V=rng.poisson(p_ratio.clip(lower=0))))
        for a in ALPHAS:
            rows[f"blend a={a}"].append(test.assign(P=blend(test, a)))
        # nested choice of a: best on the training years, judged at region x month
        tr = pd.concat([U[t] for t in TEST_YEARS if t != y]).merge(f, on=["cell", "m", "dn"], how="left")
        tr = region(tr)
        err = {a: wape(tr.assign(P=blend(tr, a)), SCALES["region_month"])[0] for a in ALPHAS}
        best = min(err, key=err.get)
        chosen[y] = best
        rows["aqua-anchored (nested a)"].append(test.assign(P=blend(test, best)))
        print(f"  {y}: nested a = {best}  [{time.time() - t0:.0f}s]", flush=True)

    loyo = {}
    for name, parts in rows.items():
        df = region(pd.concat(parts))
        loyo[name] = {sc: round(wape(df, by)[0], 4) for sc, by in SCALES.items()}
        loyo[name]["bias_cell"] = round(wape(df, SCALES["cell_month"])[1], 4)
    res["loyo"] = loyo
    res["nested_alpha"] = chosen

    # ---- E2: backcast (learn 2015-2019, predict 2012-2014) vs leave-one-year-out on 2012-2014
    win = cc[(cc.y >= 2015) & (cc.y <= 2019)]
    f = factors(win, keys)
    back = []
    for y in [2012, 2013, 2014]:
        test = U[y].merge(f, on=["cell", "m", "dn"], how="left")
        back.append(test.assign(M=test["T"] + test["A"], P=test.k_ta * (test["T"] + test["A"])))
    back = region(pd.concat(back))
    loyo_same = region(pd.concat([d for d in rows["ratio (current)"] if d.y.iloc[0] in (2012, 2013, 2014)]))
    res["backcast"] = {
        "learn_2015_2019_predict_2012_2014": {sc: round(wape(back, by)[0], 4) for sc, by in SCALES.items()}
        | {"bias": round(wape(back, SCALES["world_month"])[1], 4)},
        "loyo_on_2012_2014": {sc: round(wape(loyo_same, by)[0], 4) for sc, by in SCALES.items()}
        | {"bias": round(wape(loyo_same, SCALES["world_month"])[1], 4)},
    }
    print(f"  backcast done  [{time.time() - t0:.0f}s]", flush=True)

    # ---- E4: Northern America split, 2012 vegetation step (Feb-Dec)
    lat = cc.cell // 360 - 90
    lon = cc.cell % 360 - 180
    na = cc[(sub.reindex(cc.cell.to_numpy()).to_numpy() == "Northern America")]
    na = na.assign(part=np.where((lat.loc[na.index] >= 49) | (lon.loc[na.index] < -141), "Canada + Alaska", "USA (lower 48)"))
    f = factors(cc[(cc.y >= 2012) & (cc.y <= 2019)], na[["cell", "m", "dn"]].drop_duplicates())
    split = {}
    for part, d in na[(na.y >= 2009) & (na.y <= 2014) & (na.m >= 2)].groupby("part"):
        mod = d[d.sat.isin(MODIS)].groupby(["cell", "y", "m", "dn"], as_index=False).n_veg.sum()
        mod = mod.merge(f, on=["cell", "m", "dn"], how="left")
        mod["P"] = mod.n_veg * mod.k_ta
        a = mod.groupby("y")[["n_veg", "P"]].sum()
        v = d[d.sat == "SNPP"].groupby("y").n_veg.sum()
        pre, post = [2009, 2010, 2011], [2012, 2013, 2014]
        real = a.n_veg.loc[post].mean() / a.n_veg.loc[pre].mean() - 1
        harm = v.loc[post].mean() / a.P.loc[pre].mean() - 1
        yearly_ratio = (d[d.sat == "SNPP"].groupby("y").n_veg.sum() /
                        d[d.sat.isin(MODIS)].groupby("y").n_veg.sum()).loc[2012:2014].round(2).to_dict()
        split[part] = {"modis_step": round(real, 3), "harmonized_step": round(harm, 3), "gap": round(harm - real, 3),
                       "modis_per_year": int(a.n_veg.mean()),
                       "viirs_modis_ratio_2012_2014": {int(k): v_ for k, v_ in yearly_ratio.items()}}
    res["northern_america_split"] = split

    (DER / "experiments.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

    print("\nE1/E3  leave-one-year-out WAPE (2012-2019)")
    for name, r in loyo.items():
        print(f"  {name:26s} cell {100 * r['cell_month']:5.1f}%  region {100 * r['region_month']:4.1f}%  "
              f"world {100 * r['world_month']:4.1f}%  bias {100 * r['bias_cell']:+5.1f}%")
    print(f"  nested a per held-out year: {chosen}")
    print("\nE2  backcast")
    for k_, r in res["backcast"].items():
        print(f"  {k_:36s} " + "  ".join(f"{sc} {100 * v:.1f}%" for sc, v in r.items()))
    print("\nE4  Northern America 2012 vegetation step")
    for part, r in split.items():
        print(f"  {part:16s} MODIS {100 * r['modis_step']:+4.0f}%  harmonized {100 * r['harmonized_step']:+4.0f}%  "
              f"gap {100 * r['gap']:+4.0f}  ({r['modis_per_year']:,} MODIS/yr)  VIIRS/MODIS by year {r['viirs_modis_ratio_2012_2014']}")
    print(f"\nwrote {DER / 'experiments.json'}  [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
