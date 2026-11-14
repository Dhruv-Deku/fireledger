"""Our copy of the app's accuracy checks (mock/evaluate_api.py), built from PROMPT_FIX_2012_JUMP.md,
so we can measure against the live API while the app person's script isn't on this laptop.
Theirs is the judge; this one mirrors its definitions.

    python pipeline/check_2012.py                       # all checks except the CSV part
    python pipeline/check_2012.py --csv                 # + CSV weights vs calendar
    python pipeline/check_2012.py --holdout             # + the 6 held-back areas (only at the end)
    python pipeline/check_2012.py --out eval_x.json

Checks (27 with --csv):
  1      every world region (/api/proof jumps, vegetation): |harmonized step - MODIS step| <= 5 points
  2-10   each preset: 2012 leftover within +-5%, (H 2012-15 / H 2008-11) / (raw_modis 2012-15 / 2008-11) - 1,
         from /api/calendar, harmonized, unit viirs, day+night, industrial included
  11     held-out error, region x month (proof ablation, full method) <= 5%
  12     90% band coverage, region x month (cross-fitted conformal) within 85-95%
  13-21  each preset: contract clean (311 months, continuous, nothing negative, lo <= count <= hi)
  22-27  CSV weights add up to the calendar count (<= 0.1% off), 6 cases (--csv)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.request
from datetime import date

PRE, POST = range(2008, 2012), range(2012, 2016)
START, END = "2001-01-01", "2026-11-30"
N_MONTHS = 311
HOLDOUT = {"ZMB": "Zambia", "UKR": "Ukraine", "IND-3261": "Madhya Pradesh", "CAN-631": "Saskatchewan",
           "PRY": "Paraguay", "AUS-2657": "Queensland"}  # chosen before tuning; never looked at while tuning
CSV_CASES = [("punjab", "2016-11-01", "2016-11-30"), ("punjab", "2010-10-01", "2010-10-31"),
             ("haryana", "2014-04-01", "2014-04-30"), ("california", "2008-06-01", "2008-07-31"),
             ("cordoba", "2013-08-01", "2013-09-30"), ("patagonia", "2011-01-01", "2011-02-28")]


def call(api: str, path: str, body: dict | None = None, raw: bool = False):
    req = urllib.request.Request(api + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = r.read()
    return data.decode("utf-8") if raw else json.loads(data)


def body(area: dict, start=START, end=END) -> dict:
    return area | {"start": start, "end": end, "unit": "viirs", "daynight": "both",
                   "hide_industrial": False, "harmonized": True}


def leftover(months: list[dict]) -> float:
    def tot(key, yrs):
        return sum(m[key] for m in months if m["year"] in yrs)
    return (tot("count", POST) / tot("count", PRE)) / (tot("raw_modis", POST) / tot("raw_modis", PRE)) - 1


def contract(months: list[dict]) -> list[str]:
    errs = []
    if len(months) != N_MONTHS:
        errs.append(f"{len(months)} months, want {N_MONTHS}")
    ym = [(m["year"], m["month"]) for m in months]
    want = [(2001 + i // 12, 1 + i % 12) for i in range(len(ym))]
    if ym != want:
        errs.append("months not continuous from Jan 2001")
    for m in months:
        if min(m["count"], m["lo"], m["hi"], m["raw_modis"], m["raw_viirs"]) < 0:
            errs.append(f"negative in {m['year']}-{m['month']}")
        if not m["lo"] <= m["count"] + 1e-6 and m["count"] <= m["hi"] + 1e-6:
            errs.append(f"band order in {m['year']}-{m['month']}")
    return errs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    res: dict = {"date": str(date.today()), "checks": [], "leftovers": {}, "regions": {}}

    def check(name: str, ok: bool, detail: str) -> None:
        res["checks"].append({"name": name, "pass": bool(ok), "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}  {name:42s} {detail}", flush=True)

    proof = call(a.api, "/api/proof")
    gaps = {j["region"]: j["step2012_harmonized_vegetation"] - j["step2012_modis_vegetation"]
            for j in proof["jumps"] if j["region"] != "World"}
    res["regions"] = {k: round(v, 4) for k, v in gaps.items()}
    off = {k: v for k, v in gaps.items() if abs(v) > 0.05}
    check("world regions 2012 jump (vegetation)", not off,
          f"{len(gaps) - len(off)}/{len(gaps)} within 5%" + ("; off: " + ", ".join(
              f"{k} {100 * v:+.1f}%" for k, v in sorted(off.items(), key=lambda kv: -abs(kv[1]))) if off else ""))

    presets = call(a.api, "/api/presets")
    cals = {}
    for p in presets:
        cals[p["id"]] = call(a.api, "/api/calendar", body({"geometry": p["geometry"]}))["months"]
        lo = leftover(cals[p["id"]])
        res["leftovers"][p["id"]] = round(lo, 4)
        check(f"preset {p['id']} 2012 leftover", abs(lo) <= 0.05, f"{100 * lo:+.1f}%")

    full = next(r for r in proof["ablation"] if "FireLedger" in r["step"])
    check("held-out error region x month", full["wape_region_month"] <= 0.05,
          f"{100 * full['wape_region_month']:.2f}% (cell x month {100 * full['wape_cell_month']:.2f}%, "
          f"bias {100 * full['bias_cell_month']:+.2f}%)")
    cov = proof["conformal_bands"]["modis_to_snpp"]["coverage_crossfit"]["region_month"]
    check("90% band coverage region x month", 0.85 <= cov <= 0.95, f"{100 * cov:.1f}%")
    res["heldout_region_month"] = full["wape_region_month"]
    res["coverage_region_month"] = cov

    for pid, months in cals.items():
        errs = contract(months)
        check(f"preset {pid} contract", not errs, "; ".join(errs) or f"{len(months)} months clean")

    if a.csv:
        geo = {p["id"]: p["geometry"] for p in presets}
        for pid, d0, d1 in CSV_CASES:
            b = body({"geometry": geo[pid]}, d0, d1)
            cal = sum(m["count"] for m in call(a.api, "/api/calendar", b)["months"])
            rows = csv.DictReader(io.StringIO(call(a.api, "/api/hotspots.csv", b, raw=True)))
            w = sum(float(r["weight"]) for r in rows)
            off_ = abs(w - cal) / max(cal, 1e-9)
            check(f"CSV {pid} {d0[:7]}", off_ <= 0.001, f"weights {w:,.1f} vs calendar {cal:,.1f} ({100 * off_:.3f}%)")

    if a.holdout:
        for rid, name in HOLDOUT.items():
            lo = leftover(call(a.api, "/api/calendar", body({"region": rid}))["months"])
            res.setdefault("holdout", {})[name] = round(lo, 4)
            print(f"      held-back {name:16s} 2012 leftover {100 * lo:+.1f}%")

    n = sum(c["pass"] for c in res["checks"])
    res["passed"], res["total"] = n, len(res["checks"])
    print(f"\n{n}/{len(res['checks'])} checks pass")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)


if __name__ == "__main__":
    main()
