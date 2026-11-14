"""Compare FireLedger with India's official crop-residue fire counts (ICAR-IARI CREAMS bulletins).

    python pipeline/fetch_extras.py --only creams     # the bulletin PDFs -> data/external/creams
    python pipeline/creams.py                         # -> data/derived/creams_check.json (in /api/proof)

Each daily bulletin says how many residue-burning events satellites detected in each state that day
("... were 50 in Punjab, 01 in Haryana, ..."). CREAMS counts with its own protocol (cropland only,
its own satellite mix), so the absolute numbers differ from raw FIRMS; what should agree is the
shape: which days and seasons burn, and how one season compares with another.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SRC = ROOT / "data" / "external" / "creams"
DER = ROOT / "data" / "derived"
STATES = {"Punjab": "IND-3249", "Haryana": "IND-2430"}
SENTENCE = re.compile(r"detected on\s*(\d{1,2})-([A-Za-z]{3,9})-\d{4}\s*were\s*(.*?)(?:\.\s|\n\s*\n| respectively)", re.S)


def parse() -> pd.DataFrame:
    import fitz
    rows = []
    for f in sorted(SRC.rglob("*.pdf")):
        year = int(re.search(r"Fire_bulletin_(\d{4})", str(f)).group(1))  # the text's year has typos
        doc = fitz.open(f)
        text = re.sub(r"\s+", " ", " ".join(doc[i].get_text() for i in range(min(3, doc.page_count))))
        m = SENTENCE.search(text)
        if not m:
            continue
        day = datetime.strptime(f"{m.group(1)} {m.group(2)[:3]} {year}", "%d %b %Y").date()
        for state in STATES:
            c = re.search(rf"(\d+) in {state}", m.group(3))
            if c:
                rows.append({"date": day, "state": state, "season": "wheat" if "Wheat" in f.name else "rice",
                             "creams": int(c.group(1))})
    return pd.DataFrame(rows).drop_duplicates(["date", "state"])


def ours(days: pd.DataFrame) -> pd.DataFrame:
    """Daily hotspots inside each state: raw by satellite (daytime and all) and FireLedger's count."""
    from api.main import MASK_RES, cell_mask, parse_geometry, pip_sql
    from api.weights import JOINS, create_pers, weight_sql
    con = duckdb.connect()
    con.execute("SET memory_limit = '2GB'")
    for t in ["avail", "calib"]:
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM read_parquet('{(DER / f'{t}.parquet').as_posix()}')")
    create_pers(con, DER)
    reg = pd.read_parquet(DER / "regions.parquet", columns=["id", "geojson"]).set_index("id").geojson
    years = sorted({d.year for d in days.date})
    files = [p.as_posix() for p in (ROOT / "data" / "hotspots").glob("*.parquet")
             if int(p.stem.rsplit("_", 1)[1]) in years]
    w, _ = weight_sql("viirs")
    parts = []
    for state, rid in STATES.items():
        geom = parse_geometry(json.loads(reg[rid]))
        minx, miny, maxx, maxy = geom.bounds
        con.register("aoi", cell_mask(geom))
        inv = int(round(1 / MASK_RES))
        df = con.execute(f"""
            SELECT h.acq_date AS date,
                   count(*) FILTER (WHERE h.sat = 'NOAA20' AND h.dn = 'D') AS noaa20_day,
                   count(*) FILTER (WHERE h.sat = 'SNPP' AND h.dn = 'D') AS snpp_day,
                   count(*) FILTER (WHERE h.sat IN ('Terra', 'Aqua') AND h.dn = 'D') AS modis_day,
                   count(*) FILTER (WHERE h.sat = 'NOAA20') AS noaa20_all,
                   sum({w}) AS fireledger
            FROM read_parquet({files}) h
            JOIN aoi m ON m.cy = CAST(floor(h.lat * {inv}) AS INT) AND m.cx = CAST(floor(h.lon * {inv}) AS INT)
            {JOINS}
            WHERE h.lat BETWEEN {miny} AND {maxy} AND h.lon BETWEEN {minx} AND {maxx} AND (m.inside OR {pip_sql(geom)})
            GROUP BY ALL
        """).df()
        con.unregister("aoi")
        parts.append(df.assign(state=state))
    out = pd.concat(parts)
    out["date"] = pd.to_datetime(out.date).dt.date
    return out


def compare(df: pd.DataFrame) -> dict:
    res = {}
    cols = ["noaa20_day", "snpp_day", "modis_day", "noaa20_all", "fireledger"]
    for (state, season), d in df.groupby(["state", "season"]):
        seasons = []
        for y, s in d.groupby(d.date.map(lambda x: x.year)):
            tot = {c: round(float(s[c].sum()), 1) for c in ["creams"] + cols}
            seasons.append({"year": int(y), "days": int(len(s)), "first": str(min(s.date)), "last": str(max(s.date)), **tot})
        ok = d[d.creams + d.noaa20_day > 0]
        r = stats.pearsonr(np.log1p(ok.creams), np.log1p(ok.fireledger))[0] if len(ok) > 5 else None
        rho = stats.spearmanr(ok.creams, ok.fireledger)[0] if len(ok) > 5 else None
        rho_n = stats.spearmanr(ok.creams, ok.noaa20_day)[0] if len(ok) > 5 else None
        yrs = pd.DataFrame(seasons)
        season_rho = float(stats.spearmanr(yrs.creams, yrs.fireledger)[0]) if len(yrs) >= 3 else None
        res[f"{state} {season}"] = {
            "seasons": seasons,
            "daily_spearman_fireledger": round(float(rho), 3) if rho is not None else None,
            "daily_spearman_noaa20_day": round(float(rho_n), 3) if rho_n is not None else None,
            "daily_pearson_log_fireledger": round(float(r), 3) if r is not None else None,
            "season_total_spearman_fireledger": round(season_rho, 3) if season_rho is not None else None,
            "ratio_noaa20_day_to_creams": round(float(d.noaa20_day.sum() / max(d.creams.sum(), 1)), 2),
            "ratio_fireledger_to_creams": round(float(d.fireledger.sum() / max(d.creams.sum(), 1)), 2),
        }
    return res


def main() -> None:
    t0 = time.time()
    c = parse()
    print(f"bulletins: {len(c)} state-days, {c.date.min()} .. {c.date.max()}  [{time.time() - t0:.0f}s]", flush=True)
    o = ours(c)
    df = c.merge(o, on=["date", "state"], how="left").fillna(0)
    df = df[pd.to_datetime(df.date) <= pd.Timestamp(max(o.date))]  # our data ends a day before CREAMS'
    res = compare(df)
    out = {"source": "ICAR-IARI CREAMS crop residue burning bulletins, https://creams.iari.res.in/?page_id=1123",
           "note": ("CREAMS counts residue-burning events with its own protocol (cropland, its satellite mix), so "
                    "absolute numbers differ from FIRMS; compare timing and season-to-season changes."),
           "through": str(max(df.date)), "by_state_season": res}
    (DER / "creams_check.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    for k, v in res.items():
        print(f"\n{k}: daily Spearman vs FireLedger {v['daily_spearman_fireledger']}, vs NOAA-20 day {v['daily_spearman_noaa20_day']}; "
              f"FireLedger / CREAMS = {v['ratio_fireledger_to_creams']}")
        for s in v["seasons"]:
            print(f"   {s['year']} ({s['first']}..{s['last']}, {s['days']} days): CREAMS {s['creams']:>8,.0f}  "
                  f"NOAA-20 day {s['noaa20_day']:>8,.0f}  S-NPP day {s['snpp_day']:>8,.0f}  FireLedger {s['fireledger']:>9,.0f}")
    print(f"\nwrote {DER / 'creams_check.json'}  [{time.time() - t0:.0f}s]")


if __name__ == "__main__":
    main()
