"""Accuracy, consistency and data-loss checks for the live FireLedger API.

Run it after every pipeline/API change: it prints PASS/FAIL per check and exits
non-zero if anything fails, so a change that makes things worse shows up at once.
Save the results with --out and compare two runs to see what improved.

    python -I mock/evaluate_api.py --api http://10.1.0.7:8000 --out eval_before.json

Only the standard library. Uses the same endpoints the app uses.

The 2012 jump: gated for world regions, reported for presets (decided 15 Nov 2026).
  World regions are large, so MODIS's own change is a fair yardstick there:
  each must stay within MAX_LEFTOVER (±5%) of it, and that check passes or fails.
  For state-sized presets the yardstick itself is noisy: across 323 areas of
  this size, MODIS's change lands within 5 points of VIIRS's real change only
  59% of the time (data side, docs/experiments_2012.md), so a ±5% gate would
  fail about 4 in 10 areas even for a perfect harmonization. Each preset's
  leftover is therefore printed as INFO next to the API's own verdict
  (summary.reliability, the "2012 switch" badge in the app: good ≤ 8%,
  fair ≤ 15%, else caution) and doesn't count toward PASS/FAIL. The app shows
  that verdict to users for any area, so the limit is reported, not hidden.
"""
import argparse, csv, io, json, math, sys, time, urllib.request

# ---- What "good" means (edit here if the team agrees on other targets) ----
MAX_WAPE_REGION_MONTH = 0.05  # average miss per region and month, held-out years
BAND_COVERAGE = (0.85, 0.95)  # share of region-months inside the 90% band
MAX_LEFTOVER = 0.05  # jump left at the 2012 switch vs MODIS alone; gates world regions only
MAX_WEIGHT_MISMATCH = 0.001  # CSV weights vs calendar count
CSV_CASES = [  # (preset, first day, last day): a MODIS-only month and VIIRS months
    ("punjab", "2005-10-01", "2005-10-31"),
    ("punjab", "2016-11-01", "2016-11-30"),
    ("haryana", "2010-04-01", "2010-04-30"),
    ("haryana", "2021-10-01", "2021-10-31"),
    ("california", "2020-08-01", "2020-08-31"),
    ("patagonia", "2015-01-01", "2015-01-31"),
]

results = {"checks": [], "presets": {}, "csv": [], "proof": {}}


def check(name, ok, detail):
    results["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)


def call(api, path, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(api + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        b = r.read()
    return b.decode("utf-8") if raw else json.loads(b)


def body(geom, start="2001-01-01", end=None, harmonized=True, hide=False):
    return {"geometry": geom, "start": start, "end": end or time.strftime("%Y-%m-%d"), "unit": "viirs",
            "daynight": "both", "hide_industrial": hide, "harmonized": harmonized}


def pct(f):
    return "n/a" if f is None else f"{f * 100:+.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--out", help="save all numbers as JSON, to compare runs")
    ap.add_argument("--skip-csv", action="store_true", help="skip the slow CSV checks")
    a = ap.parse_args()
    api = a.api.rstrip("/")

    # ---- 1. The API's own validation (/api/proof) ----
    print("\n1. Held-out-year validation (/api/proof)")
    p = call(api, "/api/proof")
    ours = next((s for s in p["ablation"] if "FireLedger" in s["step"]), p["ablation"][-1])
    best = min(p["ablation"], key=lambda s: s["wape_region_month"])
    cov = p["conformal_bands"]["modis_to_snpp"]["coverage_crossfit"]["region_month"]
    check("conversion error, region-month", ours["wape_region_month"] <= MAX_WAPE_REGION_MONTH,
          f"{ours['wape_region_month']:.2%} (cell-month {ours['wape_cell_month']:.2%}, world {ours['wape_world_month']:.2%}, "
          f"bias {ours['bias_region_month']:+.2%}); best variant tested: '{best['step']}' {best['wape_region_month']:.2%}")
    check("90% band coverage, region-month", BAND_COVERAGE[0] <= cov <= BAND_COVERAGE[1], f"{cov:.1%}")
    regions = [j for j in p["jumps"] if j["region"] != "World"]
    bad = []
    for j in regions:
        gap = j["step2012_harmonized_vegetation"] - j["step2012_modis_vegetation"]
        if abs(gap) > MAX_LEFTOVER:
            bad.append(f"{j['region']} {pct(gap)}")
    check("2012 jump removed, world regions", not bad,
          f"{len(regions) - len(bad)}/{len(regions)} within {MAX_LEFTOVER:.0%}" + (f"; off: {', '.join(bad)}" if bad else ""))
    results["proof"] = {"wape_region_month": ours["wape_region_month"], "wape_cell_month": ours["wape_cell_month"],
                        "band_coverage_region_month": cov, "regions_off": bad}

    # ---- 2. Every preset: contract, 2012 jump vs MODIS alone, bands ----
    print("\n2. Presets: contract (gated); 2012 jump (2012-15 vs 2008-11, harmonized vs MODIS alone) and ranges (reported)")
    presets = call(api, "/api/presets")
    geo = {pr["id"]: pr["geometry"] for pr in presets}
    for pr in presets:
        h = call(api, "/api/calendar", body(pr["geometry"]))["months"]
        full = [m for m in h if not m.get("partial")]
        issues = 0
        for i, m in enumerate(h):
            if not (m["lo"] - 1e-6 <= m["count"] <= m["hi"] + 1e-6): issues += 1
            if min(m["count"], m["lo"], m["hi"], m["raw_modis"], m["raw_viirs"]) < 0: issues += 1
            if i and (m["year"] * 12 + m["month"]) - (h[i - 1]["year"] * 12 + h[i - 1]["month"]) != 1: issues += 1

        def mean_year(key, years):
            return sum(m[key] for m in full if m["year"] in years) / len(years)

        hb, ha = mean_year("count", range(2008, 2012)), mean_year("count", range(2012, 2016))
        mb, ma = mean_year("raw_modis", range(2008, 2012)), mean_year("raw_modis", range(2012, 2016))
        # Leftover as a ratio of changes: how much bigger the harmonized change is than MODIS's own.
        left = ((ha / hb) / (ma / mb) - 1) if hb and mb and ma else None

        def rel_width(ms):
            w = sorted((m["hi"] - m["lo"]) / m["count"] for m in ms if m["count"] > 50)
            return w[len(w) // 2] if w else None

        zero_band_snpp = sum(1 for m in full if 2012 <= m["year"] <= 2017 and m["count"] > 50 and m["hi"] == m["lo"])
        n_snpp = sum(1 for m in full if 2012 <= m["year"] <= 2017 and m["count"] > 50)
        results["presets"][pr["id"]] = {"issues": issues, "leftover": left, "band_modis": rel_width([m for m in full if m["year"] <= 2011]),
                                        "band_2012_17": rel_width([m for m in full if 2012 <= m["year"] <= 2017]),
                                        "zero_band_2012_17": f"{zero_band_snpp}/{n_snpp}"}
        check(f"{pr['id']}: contract", issues == 0, f"{len(h)} months, {issues} problems")
        # Reported, not gated (see the docstring): our leftover next to the API's badge verdict.
        rel = call(api, "/api/summary", body(pr["geometry"])).get("reliability") or {}
        results["presets"][pr["id"]]["api_verdict"] = rel.get("verdict")
        results["presets"][pr["id"]]["api_leftover"] = rel.get("leftover_2012")
        # None when the preset has no month over 50 hotspots in that period
        width = lambda w: "n/a" if w is None else f"{w:.0%}"
        print(f"  INFO  {pr['id']}: 2012 jump: {pct(left)} left over; API badge: {rel.get('verdict', 'none')} "
              f"({pct(rel.get('leftover_2012'))}); range width (hi-lo)/count, median: MODIS years "
              f"{width(results['presets'][pr['id']]['band_modis'])}, 2012-17 {width(results['presets'][pr['id']]['band_2012_17'])} "
              f"({zero_band_snpp}/{n_snpp} months with no range)", flush=True)

    # ---- 3. CSV vs calendar, and what doesn't count ----
    # a grid-only server has no single hotspots (hotspots.csv answers 503)
    if not a.skip_csv and call(api, "/api/health").get("mode") == "grid-only":
        print("\n3. Hotspots CSV: skipped, this server is grid-only (monthly totals, no single hotspots)")
    elif not a.skip_csv:
        print("\n3. Hotspots CSV: weights add up to the calendar; detections that don't count")
        for pid, s, e in CSV_CASES:
            if pid not in geo:
                continue
            b = body(geo[pid], s, e)
            cal = call(api, "/api/calendar", b)["months"]
            rows = list(csv.DictReader(io.StringIO(call(api, "/api/hotspots.csv?full=true", b, raw=True))))
            wsum = sum(float(r["weight"]) for r in rows)
            count = cal[0]["count"] if cal else 0
            mism = abs(wsum - count) / count if count else 0
            check(f"{pid} {s[:7]}: CSV weights = calendar", mism <= MAX_WEIGHT_MISMATCH,
                  f"{wsum:,.1f} vs {count:,.1f} ({mism:.3%})")
            orphan = modis_without_viirs(rows)
            hidden = call(api, "/api/calendar", body(geo[pid], s, e, hide=True))["months"]
            ind_share = 1 - (hidden[0]["count"] / count) if count and hidden else 0
            rec = {"case": f"{pid} {s[:7]}", "rows": len(rows), "counted": sum(1 for r in rows if float(r["weight"]) > 0),
                   "industrial_share_of_count": ind_share, **orphan}
            results["csv"].append(rec)
            print(f"        rows {rec['rows']:,}, counted {rec['counted']:,}; hide-industrial removes {ind_share:.1%} of the count; "
                  + (f"MODIS detections with no VIIRS detection within their pixel that day: "
                     f"{orphan['modis_only']:,} of {orphan['modis_weight0']:,} not counted ({orphan['modis_only_share']:.1%})"
                     if orphan["modis_weight0"] else "no uncounted MODIS rows"), flush=True)

    fails = [c for c in results["checks"] if not c["ok"]]
    print(f"\n{len(results['checks']) - len(fails)} passed, {len(fails)} failed")
    if a.out:
        json.dump(results, open(a.out, "w", encoding="utf-8"), indent=1)
        print(f"saved {a.out}")
    sys.exit(1 if fails else 0)


def modis_without_viirs(rows):
    """MODIS rows that count 0 (VIIRS years) with no VIIRS detection within their pixel on the same UTC day.

    These are fires only MODIS saw: kept in the CSV, but not in the VIIRS-unit count.
    """
    cell = 0.05  # degrees; floor, not int(), so cells don't merge across 0°
    viirs = {}
    for r in rows:
        if r["satellite"] not in ("Terra", "Aqua"):
            lat, lon = float(r["latitude"]), float(r["longitude"])
            viirs.setdefault((r["acq_date"], math.floor(lat / cell), math.floor(lon / cell)), []).append((lat, lon))
    n = only = 0
    for r in rows:
        if r["satellite"] in ("Terra", "Aqua") and float(r["weight"]) == 0:
            n += 1
            lat, lon = float(r["latitude"]), float(r["longitude"])
            radius_km = max(1.0, float(r.get("scan") or 1.0))  # the MODIS pixel, at least 1 km
            kx = 1 / (111.32 * max(0.01, math.cos(math.radians(lat))))
            found = False
            ci, cj = math.floor(lat / cell), math.floor(lon / cell)
            # search far enough for the whole pixel: a 0.05° cell is narrower in km away from the equator
            ni = math.ceil(radius_km / 110.57 / cell)
            nj = math.ceil(radius_km * kx / cell)
            for di in range(-ni, ni + 1):
                for dj in range(-nj, nj + 1):
                    for vlat, vlon in viirs.get((r["acq_date"], ci + di, cj + dj), ()):
                        if math.hypot((vlat - lat) * 110.57, (vlon - lon) / kx) <= radius_km:
                            found = True
                            break
                    if found: break
                if found: break
            only += not found
    return {"modis_weight0": n, "modis_only": only, "modis_only_share": only / n if n else 0}


if __name__ == "__main__":
    main()
