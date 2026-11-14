"""Country and state presets for the area picker, from Natural Earth.

Reads  data/external/boundaries/ne_10m_admin_0_countries_ind/*.shp   (India point of view)
       data/external/boundaries/ne_10m_admin_1_states_provinces/*.shp (if downloaded)
Writes data/derived/regions.parquet: id, name, kind, country, subregion, bbox, geojson

Geometries are simplified for the web (and so the SQL point-in-polygon test stays
small); tiny islands below the tolerance may drop out.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pandas as pd
import shapefile
import shapely
from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "data" / "external" / "boundaries"
OUT = ROOT / "data" / "derived" / "regions.parquet"


def read_shp(stem: str):
    folder = EXT / stem
    if not folder.exists():
        z = EXT / f"{stem}.zip"
        if not z.exists():
            return []
        try:
            zipfile.ZipFile(z).extractall(folder)
        except zipfile.BadZipFile:
            print(f"skip {z.name}: incomplete download")
            return []
    r = shapefile.Reader(str(next(folder.glob("*.shp"))), encoding="utf-8")
    return list(r.iterShapeRecords())


def simplify(geom, tol: float):
    g = shapely.make_valid(geom.simplify(tol, preserve_topology=True))
    if g.geom_type == "GeometryCollection":
        g = shapely.union_all([p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon")])
    return g


def main() -> None:
    rows = []
    for sr in read_shp("ne_10m_admin_0_countries_ind"):
        rec = sr.record.as_dict()
        if rec["ADM0_A3"] == "ATA":
            continue
        g = simplify(shape(sr.shape.__geo_interface__), 0.02)
        if g.is_empty:
            continue
        rows.append({"id": rec["ADM0_A3"], "name": rec["NAME"], "kind": "country", "country": rec["NAME"],
                     "subregion": rec["SUBREGION"], "geom": g})
    countries = {r["id"]: r for r in rows}

    for sr in read_shp("ne_10m_admin_1_states_provinces"):
        rec = sr.record.as_dict()
        g = simplify(shape(sr.shape.__geo_interface__), 0.01)
        if g.is_empty:
            continue
        parent = countries.get(rec.get("adm0_a3"))
        rows.append({"id": rec.get("adm1_code") or f"{rec.get('adm0_a3')}-{rec.get('name')}", "name": rec.get("name"),
                     "kind": "state", "country": rec.get("admin"),
                     "subregion": parent["subregion"] if parent else None, "geom": g})

    df = pd.DataFrame(rows)
    df["bbox"] = df.geom.map(lambda g: [round(v, 3) for v in g.bounds])
    df["geojson"] = df.geom.map(lambda g: json.dumps(mapping(g), separators=(",", ":")))
    df = df.drop(columns="geom").drop_duplicates("id")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"regions: {(df.kind == 'country').sum()} countries, {(df.kind == 'state').sum()} states -> {OUT}")


if __name__ == "__main__":
    main()
