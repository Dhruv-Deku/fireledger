"""Independent check: harmonized hotspot counts against MODIS burned area (MCD64CMQ).

Burned area is a different measurement (burn scars, not hot pixels) taken by MODIS alone, so
it has no 2012 sensor change. If the harmonization works, hotspots per km2 burned should run
straight through 2012. Merging the raw records (MODIS before 2012, VIIRS after) should make it
jump. Vegetation fires only (industrial / persistent hot spots don't leave burn scars).

Regions are the 14 GFED basis regions. Years stop at 2019: from ~2020 the MODIS orbits drift,
which changes the burned-area product itself.

Reads  data/external/mcd64cmq/*.hdf      MCD64CMQ C6.1, 0.25 deg monthly burned area (hectares)
                                          from sftp://fuoco.geog.umd.edu (user fire, password burnt)
       data/external/gfed/GFED4.1s_2016.hdf5   ancill/basis_regions only
       data/derived/grid.parquet
Writes data/derived/burned_area.json  (served under "burned_area" by GET /api/proof)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import duckdb
import h5py
import numpy as np
import pandas as pd
from pyhdf.SD import SD
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from validate import pettitt  # noqa: E402

EXT = ROOT / "data" / "external"
DER = ROOT / "data" / "derived"
YEARS = range(2001, 2020)
PRE, POST = range(2008, 2012), range(2012, 2016)
REGIONS = {1: "Boreal North America", 2: "Temperate North America", 3: "Central America",
           4: "Northern South America", 5: "Southern South America", 6: "Europe", 7: "Middle East",
           8: "Northern Africa", 9: "Southern Africa", 10: "Boreal Asia", 11: "Central Asia",
           12: "Southeast Asia", 13: "Equatorial Asia", 14: "Australia and New Zealand"}
SERIES = ["modis", "raw_merged", "harmonized"]


def basis_regions() -> np.ndarray:
    """720 x 1440, row 0 = 89.875N, col 0 = 179.875W (same layout as MCD64CMQ)."""
    with h5py.File(EXT / "gfed" / "GFED4.1s_2016.hdf5") as h:
        return h["ancill/basis_regions"][:].astype(np.int64)


def burned_area(reg: np.ndarray) -> pd.DataFrame:
    """km2 burned per region x year."""
    rows = []
    for f in sorted((EXT / "mcd64cmq").glob("MCD64CMQ.A*.hdf")):
        y = int(re.search(r"\.A(\d{4})\d{3}\.", f.name).group(1))
        if y not in YEARS:
            continue
        ds = SD(str(f)).select("BurnedArea")
        km2 = ds.get().astype(np.float64) * ds.attributes()["scale_factor"] / 100  # ha -> km2
        per = np.bincount(reg.ravel(), weights=km2.ravel(), minlength=len(REGIONS) + 1)
        rows += [{"region": r, "y": y, "ba": per[r]} for r in REGIONS]
    return pd.DataFrame(rows).groupby(["region", "y"], as_index=False).ba.sum()


def hotspots(reg: np.ndarray) -> pd.DataFrame:
    """Vegetation-fire hotspots per region x year: raw MODIS, raw VIIRS, harmonized (VIIRS units)."""
    g = duckdb.connect().execute(f"""
        SELECT gy, gx, y, sum(v) AS harmonized, sum(raw_modis) AS modis, sum(raw_viirs) AS viirs
        FROM read_parquet('{(DER / "grid.parquet").as_posix()}')
        WHERE NOT static AND y BETWEEN {YEARS[0]} AND {YEARS[-1]} GROUP BY ALL
    """).df()
    g["region"] = reg[359 - g.gy.to_numpy(), g.gx.to_numpy() + 720]
    g = g[g.region > 0]
    out = g.groupby(["region", "y"], as_index=False)[["modis", "viirs", "harmonized"]].sum()
    out["raw_merged"] = np.where(out.y < 2012, out.modis, out.viirs)
    return out


def summarise(d: pd.DataFrame) -> dict:
    d = d.set_index("y").sort_index()

    def ratio(col: str, years: range) -> float:
        s = d.loc[d.index.isin(years)]
        return float(s[col].sum() / s.ba.sum())

    ratios = {c: d[c] / d.ba for c in SERIES}
    out = {"burned_km2_per_year": round(float(d.ba.mean())),
           "hotspots_per_km2": {c: round(ratio(c, YEARS), 3) for c in SERIES},
           "step2012": {c: round(ratio(c, POST) / ratio(c, PRE) - 1, 3) for c in SERIES}}
    out["gap_vs_modis"] = round(out["step2012"]["harmonized"] - out["step2012"]["modis"], 3)
    out["pettitt_ratio"] = {}
    for c in ("raw_merged", "harmonized"):
        t, p = pettitt(ratios[c].to_numpy())
        out["pettitt_ratio"][c] = {"year": int(d.index[t]), "p": round(p, 4)}
    out["spearman_vs_burned"] = {c: round(float(stats.spearmanr(d[c], d.ba)[0]), 3) for c in ("raw_merged", "harmonized")}
    out["series"] = [{"year": int(y), "burned_km2": round(float(r.ba)),
                      **{c: round(float(r[c])) for c in SERIES}} for y, r in d.iterrows()]
    return out


def main() -> None:
    reg = basis_regions()
    df = burned_area(reg).merge(hotspots(reg), on=["region", "y"], how="left").fillna(0)
    world = df.groupby("y", as_index=False)[["ba", *SERIES]].sum()
    res = {"note": ("Vegetation-fire hotspots per km2 burned (MODIS MCD64CMQ), 2001-2019, by GFED basis region. "
                    "step2012 compares 2012-2015 with 2008-2011. Burned area has no sensor change in 2012, so "
                    "a consistent hotspot record keeps the ratio flat apart from real changes in how fires "
                    "burn; raw MODIS shows that real change."),
           "years": [YEARS[0], YEARS[-1]], "windows": {"before": [PRE[0], PRE[-1]], "after": [POST[0], POST[-1]]},
           "regions": [{"region": "World", **summarise(world)}]}
    for r, name in REGIONS.items():
        d = df[df.region == r]
        if d.ba.sum() > 0 and d.modis.sum() > 5000:
            res["regions"].append({"region": name, **summarise(d.drop(columns="region"))})
    (DER / "burned_area.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

    print(f"hotspots per km2 burned: step 2012-15 vs 2008-11 (vegetation fires, MCD64 burned area)")
    print(f"  {'region':26s} {'MODIS':>7s} {'raw':>7s} {'harm.':>7s} {'gap':>5s}  Pettitt on ratio (raw | harm.)   rho vs burned (raw | harm.)")
    for r in res["regions"]:
        s, p, rho = r["step2012"], r["pettitt_ratio"], r["spearman_vs_burned"]
        print(f"  {r['region']:26s} {100 * s['modis']:+6.0f}% {100 * s['raw_merged']:+6.0f}% {100 * s['harmonized']:+6.0f}% "
              f"{100 * r['gap_vs_modis']:+4.0f}   {p['raw_merged']['year']} p={p['raw_merged']['p']:.3f} | "
              f"{p['harmonized']['year']} p={p['harmonized']['p']:.3f}     {rho['raw_merged']:+.2f} | {rho['harmonized']:+.2f}")
    print(f"wrote {DER / 'burned_area.json'}")


if __name__ == "__main__":
    main()
