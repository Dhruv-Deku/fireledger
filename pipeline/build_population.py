"""People per 0.25 deg cell, from WorldPop 2020 (1 km, UN-adjusted), for "people near fires".

    python pipeline/fetch_extras.py --only population   # the GeoTIFF -> data/external/population
    python pipeline/build_population.py                 # -> data/derived/population_025.parquet

The cells match the harmonized grid (gy = floor(lat*4), gx = floor(lon*4)), so /api/people can tell
how many people live in the cells where fires burned. Read in strips to keep memory small.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "external" / "population" / "worldpop_2020_1km_UNadj.tif"
OUT = ROOT / "data" / "derived" / "population_025.parquet"
STRIP = 600  # raster rows per read


def main() -> None:
    import rasterio
    from rasterio.windows import Window
    t0 = time.time()
    total = np.zeros(720 * 1440)
    with rasterio.open(SRC) as r:
        a, e, x0, y0 = r.transform.a, r.transform.e, r.transform.c, r.transform.f
        gx = np.clip(np.floor((x0 + (np.arange(r.width) + 0.5) * a) * 4).astype(int) + 720, 0, 1439)
        for row in range(0, r.height, STRIP):
            n = min(STRIP, r.height - row)
            block = r.read(1, window=Window(0, row, r.width, n)).astype(np.float64)
            block[(block < 0) | ~np.isfinite(block)] = 0.0
            lat = y0 + (np.arange(row, row + n) + 0.5) * e
            gy = np.clip(np.floor(lat * 4).astype(int) + 360, 0, 719)
            idx = (gy[:, None] * 1440 + gx[None, :]).ravel()
            total += np.bincount(idx, weights=block.ravel(), minlength=720 * 1440)
    cells = np.nonzero(total > 0)[0]
    df = pd.DataFrame({"gy": (cells // 1440 - 360).astype("int16"), "gx": (cells % 1440 - 720).astype("int16"),
                       "pop": total[cells].astype("float32")})
    df.to_parquet(OUT, index=False)
    print(f"{len(df):,} populated cells, {total.sum() / 1e9:.2f} billion people -> {OUT}  [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
