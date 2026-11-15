"""Save the API's answers for the presets as static files, so the app works without the API.

    python pipeline/snapshot.py            # -> data/snapshot/ and docs/fireledger_snapshot.zip

After the hackathon the API lives on one laptop; judges opening the app need data anyway. This writes
exactly what the API answers (same code, imported, not re-implemented) for every preset and every
combination of the app's toggles, plus the map layer per month at 1 degree, and a manifest that says
which file answers which request.

    snapshot/manifest.json
    snapshot/presets.json, proof.json, health.json
    snapshot/calendar/<preset>/<variant>.json      variant = <unit>_<daynight>_<harmonized|raw>_<all|noind>
    snapshot/summary/<preset>/<variant>.json
    snapshot/layers/<yyyy>-<mm>.json               /api/grid?year=&month=&res=1 (VIIRS-equivalent, day+night)
"""
from __future__ import annotations

import itertools
import json
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "snapshot"
ZIP = ROOT / "docs" / "fireledger_snapshot.zip"
START = "2000-11-01"
LAYER_RES = 1.0


def variant(unit: str, dn: str, harmonized: bool, hide: bool) -> str:
    return f"{unit}_{dn}_{'harmonized' if harmonized else 'raw'}_{'noind' if hide else 'all'}"


def dump(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)
    path.write_text(data, encoding="utf-8")
    return len(data)


def main() -> None:
    import api.main as A
    from fastapi.encoders import jsonable_encoder
    t0 = time.time()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    A.db()
    presets = A.preset_list()
    dump(OUT / "presets.json", presets)
    dump(OUT / "proof.json", jsonable_encoder(A.proof()))
    dump(OUT / "health.json", jsonable_encoder(A.health()))
    tag = subprocess.run(["git", "describe", "--tags", "--always"], cwd=ROOT, capture_output=True, text=True).stdout.strip()

    # most-used views first, so a partial snapshot is already useful
    combos = sorted(itertools.product(["viirs", "modis"], ["both", "D", "N"], [True, False], [False, True]),
                    key=lambda c: (c[0] != "viirs", c[1] != "both", not c[2], c[3]))
    variants = []
    nbytes = 0
    for unit, dn, harmonized, hide in combos:
        v = variant(unit, dn, harmonized, hide)
        for p in presets:
            q = dict(geometry=p["geometry"], start=START, unit=unit, daynight=dn, harmonized=harmonized, hide_industrial=hide)
            nbytes += dump(OUT / "calendar" / p["id"] / f"{v}.json", jsonable_encoder(A.calendar(A.Query(**q))))
            nbytes += dump(OUT / "summary" / p["id"] / f"{v}.json", jsonable_encoder(A.summary(A.Query(**q))))
        variants.append(v)
        print(f"  {v}: 9 presets  [{time.time() - t0:.0f}s, {nbytes / 1e6:.0f} MB]", flush=True)
        write_manifest(presets, variants, [], tag)

    # map layer: one file per month, 1 degree
    first = date.fromisoformat(START)
    last = A.LAST_DAY
    months = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        nbytes += dump(OUT / "layers" / f"{y}-{m:02d}.json", jsonable_encoder(A.grid_layer(year=y, month=m, res=LAYER_RES)))
        months.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    print(f"  layers: {len(months)} months at {LAYER_RES} deg  [{time.time() - t0:.0f}s, {nbytes / 1e6:.0f} MB]", flush=True)
    write_manifest(presets, variants, months, tag)

    ZIP.unlink(missing_ok=True)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(OUT.rglob("*.json")):
            z.write(f, Path("snapshot") / f.relative_to(OUT))
    print(f"wrote {OUT} ({nbytes / 1e6:.0f} MB) and {ZIP} ({ZIP.stat().st_size / 1e6:.1f} MB)  [{time.time() - t0:.0f}s]")


def write_manifest(presets, variants, months, tag) -> None:
    import api.main as A
    dump(OUT / "manifest.json", {
        "about": "FireLedger API answers saved as files, for when the live API can't be reached.",
        "created": date.today().isoformat(), "api_build": tag,
        "data_through": A.LAST_DAY.isoformat(),
        "request": {"start": START, "end": "latest day with data",
                    "note": "every answer is for the full record; date changes in the app can filter the months client-side"},
        "presets": [p["id"] for p in presets],
        "variants": variants,
        "variant_pattern": "<unit>_<daynight>_<harmonized|raw>_<all|noind>, e.g. viirs_both_harmonized_all",
        "files": {"presets": "presets.json", "proof": "proof.json", "health": "health.json",
                  "calendar": "calendar/{preset}/{variant}.json", "summary": "summary/{preset}/{variant}.json",
                  "layer": "layers/{yyyy}-{mm}.json"},
        "layer": {"res_deg": LAYER_RES, "unit": "viirs", "daynight": "both", "hide_industrial": False, "months": months},
        "not_in_snapshot": ["drawn areas and searched regions (need the live API)", "CSV downloads",
                            "map layers at finer than 1 degree"],
    })


if __name__ == "__main__":
    main()
