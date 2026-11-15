"""Validate mock.json against the API contract in docs/API_FOR_APP.md, and check it is
internally consistent (summary vs calendar). Usage: python -I validate_mock.py <mock.json> [<copy> ...]"""
import hashlib
import json
import statistics
import sys

errors, warnings, info = [], [], []
err = errors.append
warn = warnings.append


def is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_polygon(g, where):
    if not isinstance(g, dict) or g.get("type") != "Polygon":
        return err(f"{where}: geometry must be a GeoJSON Polygon")
    rings = g.get("coordinates")
    if not rings or not isinstance(rings, list):
        return err(f"{where}: missing coordinates")
    for r in rings:
        if len(r) < 4:
            err(f"{where}: ring has fewer than 4 points")
        if r[0] != r[-1]:
            err(f"{where}: ring is not closed")
        for p in r:
            if not (len(p) == 2 and is_num(p[0]) and is_num(p[1]) and -180 <= p[0] <= 180 and -90 <= p[1] <= 90):
                err(f"{where}: bad point {p}")
                break


def main(path, copies):
    raw = open(path, "rb").read()
    m = json.loads(raw)

    # ---- request example ----
    rq = m.get("request_example", {})
    check_polygon(rq.get("geometry"), "request_example")
    for k, allowed in (("unit", {"viirs", "modis"}), ("daynight", {"D", "N", "both"})):
        if rq.get(k) not in allowed:
            err(f"request_example.{k}={rq.get(k)!r} not in {sorted(allowed)}")
    for k in ("hide_industrial", "harmonized"):
        if not isinstance(rq.get(k), bool):
            err(f"request_example.{k} must be boolean")

    # ---- calendar ----
    cal = m["calendar"]
    if cal.get("unit") not in {"viirs", "modis"}:
        err(f"calendar.unit={cal.get('unit')!r}")
    months = cal["months"]
    keys = ["year", "month", "count", "lo", "hi", "raw_modis", "raw_viirs", "flags"]
    prev = None
    for i, r in enumerate(months):
        missing = [k for k in keys if k not in r]
        extra = [k for k in r if k not in keys]
        if missing:
            err(f"months[{i}] missing {missing}")
            continue
        if extra:
            warn(f"months[{i}] has extra keys {extra}")
        if not (isinstance(r["year"], int) and isinstance(r["month"], int) and 1 <= r["month"] <= 12):
            err(f"months[{i}] bad year/month {r['year']}-{r['month']}")
        for k in ("count", "lo", "hi", "raw_modis", "raw_viirs"):
            if not is_num(r[k]) or r[k] < 0:
                err(f"months[{i}] {k}={r[k]!r} must be a number >= 0")
        if not (r["lo"] <= r["count"] <= r["hi"]):
            err(f"{r['year']}-{r['month']:02d}: count {r['count']} outside lo-hi {r['lo']}-{r['hi']}")
        if not isinstance(r["flags"], str) or any(c not in "MCDB" for c in r["flags"]):
            err(f"{r['year']}-{r['month']:02d}: flags {r['flags']!r} must be letters from MCDB")
        if r["year"] < 2012 and r["raw_viirs"] != 0:
            warn(f"{r['year']}-{r['month']:02d}: raw_viirs {r['raw_viirs']} before VIIRS existed (2012)")
        if r["year"] < 2012 and "M" not in r["flags"]:
            warn(f"{r['year']}-{r['month']:02d}: pre-2012 month without flag M")
        idx = r["year"] * 12 + r["month"]
        if prev is not None and idx != prev + 1:
            err(f"months not continuous/ordered at {r['year']}-{r['month']:02d}")
        prev = idx
    first, last = months[0], months[-1]
    info.append(f"calendar: {len(months)} months, {first['year']}-{first['month']:02d} .. {last['year']}-{last['month']:02d}")
    if (first["year"], first["month"]) != (2000, 11):
        warn(f"calendar starts {first['year']}-{first['month']:02d}; the record starts Nov 2000 (docs/API_FOR_APP.md)")

    # ---- summary ----
    s = m["summary"]
    tm = s["this_month"]
    for k in ("year", "month", "count", "median", "p25", "p75", "pct_diff", "anomaly"):
        if not is_num(tm.get(k)):
            err(f"summary.this_month.{k} missing or not a number")
    by = {(r["year"], r["month"]): r for r in months}
    cur = by.get((tm["year"], tm["month"]))
    if not cur:
        err(f"summary.this_month {tm['year']}-{tm['month']} is not in the calendar")
    elif abs(cur["count"] - tm["count"]) > 0.5:
        err(f"this_month.count {tm['count']} != calendar {tm['year']}-{tm['month']:02d} count {cur['count']}")
    same = [r["count"] for r in months if r["month"] == tm["month"] and r["year"] != tm["year"]]
    # A month in progress is compared with the same days of other years
    # (docs/API_FOR_APP.md); check against those numbers when the API sends them.
    days_in = [31, 29 if tm["year"] % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][tm["month"] - 1]
    if tm.get("days_covered") and tm["days_covered"] < days_in and tm.get("same_days_past"):
        same = [p["count"] for p in tm["same_days_past"]]
        info.append(f"partial month (day {tm['days_covered']}): comparing with same_days_past")
    if same:
        med = statistics.median(same)
        q = statistics.quantiles(same, n=4)
        info.append(
            f"from calendar, past {len(same)} Novembers: median {med:.1f}, p25 {q[0]:.1f}, p75 {q[2]:.1f}"
            f" | summary says median {tm['median']}, p25 {tm['p25']}, p75 {tm['p75']}"
        )
        if abs(med - tm["median"]) > max(5, 0.05 * med):
            err(f"this_month.median {tm['median']} differs from calendar-derived median {med:.1f}")
        if tm["median"]:
            pct = (tm["count"] - tm["median"]) / tm["median"] * 100
            if abs(pct - tm["pct_diff"]) > 1:
                err(f"this_month.pct_diff {tm['pct_diff']} != (count-median)/median = {pct:.1f}")
    if not (tm["p25"] <= tm["median"] <= tm["p75"]):
        err("this_month: p25 <= median <= p75 does not hold")

    # busiest months / years vs calendar
    full_years = sorted({r["year"] for r in months if sum(1 for x in months if x["year"] == r["year"]) == 12})
    # Complete months only: the current (last) month is still in progress.
    done = months[:-1]
    med_by_month = {mo: statistics.median([r["count"] for r in done if r["month"] == mo]) for mo in range(1, 13)}
    top_months = sorted(med_by_month, key=med_by_month.get, reverse=True)[:3]
    tot_by_year = {y: sum(r["count"] for r in months if r["year"] == y) for y in full_years}
    top_years = sorted(tot_by_year, key=tot_by_year.get, reverse=True)[:3]
    info.append(f"from calendar: busiest months {[(mo, round(med_by_month[mo])) for mo in top_months]}")
    info.append(f"from calendar: busiest full years {[(y, round(tot_by_year[y])) for y in top_years]}")
    info.append(f"summary says: busiest_months {[(b['month'], b['median']) for b in s['busiest_months']]}")
    info.append(f"summary says: busiest_years {[(b['year'], b['total']) for b in s['busiest_years']]}")
    if [b["month"] for b in s["busiest_months"]] != top_months:
        err("summary.busiest_months order differs from the calendar")
    if [b["year"] for b in s["busiest_years"]] != top_years:
        err("summary.busiest_years differs from the calendar's full-year totals")
    for b in s["busiest_years"]:
        if b["year"] in tot_by_year and abs(b["total"] - tot_by_year[b["year"]]) > max(10, 0.02 * tot_by_year[b["year"]]):
            err(f"busiest_years {b['year']} total {b['total']} != calendar sum {tot_by_year[b['year']]:.0f}")

    se = s["season"]
    for k in ("start_month", "peak_month", "end_month"):
        if not (isinstance(se.get(k), int) and 1 <= se[k] <= 12):
            err(f"summary.season.{k} invalid")
    for c in s["critical_next"]:
        if not (1 <= c["month"] <= 12 and is_num(c["median"]) and isinstance(c["note"], str)):
            err(f"critical_next item invalid: {c}")
    tr = s["trend"]
    if not (is_num(tr.get("sen_slope_per_year")) and is_num(tr.get("p_value")) and 0 <= tr["p_value"] <= 1):
        err("summary.trend invalid")

    # ---- CSV sample ----
    csv = m["hotspots_csv_sample"].strip().splitlines()
    want = "acq_date,acq_time,latitude,longitude,satellite,instrument,frp,confidence,daynight,type,weight"
    if csv[0] != want:
        err(f"CSV header differs from contract:\n  got  {csv[0]}\n  want {want}")
    for line in csv[1:]:
        if len(line.split(",")) != 11:
            err(f"CSV row has {len(line.split(','))} columns: {line}")
    info.append(f"CSV sample: {len(csv) - 1} rows")

    # ---- presets ----
    for p in m["presets"]:
        if not (p.get("id") and p.get("name")):
            err(f"preset missing id/name: {p}")
        check_polygon(p.get("geometry"), f"preset {p.get('id')}")
    info.append(f"presets: {[p['id'] for p in m['presets']]}")

    # ---- copies identical ----
    h = hashlib.sha256(raw).hexdigest()[:12]
    for c in copies:
        hc = hashlib.sha256(open(c, "rb").read()).hexdigest()[:12]
        (info.append if hc == h else err)(f"copy {c}: {'identical' if hc == h else 'DIFFERENT'} ({hc} vs {h})")

    for line in info:
        print("INFO ", line)
    for line in warnings[:15]:
        print("WARN ", line)
    if len(warnings) > 15:
        print(f"WARN  ... {len(warnings) - 15} more warnings")
    for line in errors:
        print("ERROR", line)
    print(f"\n{len(errors)} errors, {len(warnings)} warnings")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
