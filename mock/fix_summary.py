"""Make mock.json internally consistent: derive every `summary` number from the
mock's own `calendar`, the way the real API should derive it from real data.

Usage (from the repo root):
  python -I mock/fix_summary.py mock/mock.json

Rules (also listed in mock/HANDOFF.md):
- The current month is the last calendar month. Its count is set to CURRENT_COUNT
  in the calendar too (lo/hi/raw scaled in proportion), so both places agree.
- median / p25 / p75: the same calendar month in all earlier years.
- pct_diff = (count - median) / median * 100.
- anomaly: robust z = (count - median) / (1.4826 * MAD) over those years.
- busiest_months: 3 months with the highest median count (complete months only).
- busiest_years: 3 full years (12 months) with the highest summed count.
- season: peak = month with the highest median; start/end = the run of months
  around the peak whose median is at least 25 % of the peak median.
- critical_next: months in the next 6 whose median is at least 25 % of the peak.
- trend: Sen's slope of full-year totals (hotspots per year) and a two-sided
  Mann-Kendall p-value.
"""
import json
import math
import statistics
import sys

CURRENT_COUNT = 1520  # Nov 1–14, 2026 so far (the app's demo number)
DAYS_COVERED = 14
BUSY_SHARE = 0.25
LOOKAHEAD = 6


def r1(x):
    return round(x, 1)


def main(path):
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    months = m["calendar"]["months"]
    cur = months[-1]

    # 1. Current month: one number in both places.
    k = CURRENT_COUNT / cur["count"]
    cur["lo"] = r1(cur["lo"] * k)
    cur["hi"] = r1(cur["hi"] * k)
    cur["raw_modis"] = round(cur["raw_modis"] * k)
    cur["raw_viirs"] = round(cur["raw_viirs"] * k)
    cur["count"] = float(CURRENT_COUNT)

    # 2. This month vs the same month in earlier years.
    past = [r["count"] for r in months if r["month"] == cur["month"] and r["year"] < cur["year"]]
    med = statistics.median(past)
    q = statistics.quantiles(past, n=4)
    mad = statistics.median([abs(x - med) for x in past])
    this_month = {
        "year": cur["year"],
        "month": cur["month"],
        "count": CURRENT_COUNT,
        "median": r1(med),
        "p25": r1(q[0]),
        "p75": r1(q[2]),
        "pct_diff": r1((CURRENT_COUNT - med) / med * 100),
        "anomaly": r1((CURRENT_COUNT - med) / (1.4826 * mad)) if mad else 0.0,
        "days_covered": DAYS_COVERED,
    }

    # 3. Busiest months and years.
    # Complete months only: the current month is still in progress.
    done = [r for r in months if r is not cur]
    med_by_month = {mo: statistics.median([r["count"] for r in done if r["month"] == mo]) for mo in range(1, 13)}
    top_months = sorted(med_by_month, key=med_by_month.get, reverse=True)[:3]
    years = sorted({r["year"] for r in months})
    full = [y for y in years if sum(1 for r in months if r["year"] == y) == 12]
    totals = {y: sum(r["count"] for r in months if r["year"] == y) for y in full}
    top_years = sorted(totals, key=totals.get, reverse=True)[:3]

    # 4. Fire season around the peak month.
    peak = max(med_by_month, key=med_by_month.get)
    busy = {mo for mo, v in med_by_month.items() if v >= BUSY_SHARE * med_by_month[peak]}
    start = end = peak
    while (start - 2) % 12 + 1 in busy and (start - 2) % 12 + 1 != peak:
        start = (start - 2) % 12 + 1
    while end % 12 + 1 in busy and end % 12 + 1 != peak:
        end = end % 12 + 1

    # 5. Coming up: historically busy months in the next LOOKAHEAD months.
    critical = []
    for i in range(1, LOOKAHEAD + 1):
        mo = (cur["month"] - 1 + i) % 12 + 1
        if mo in busy:
            critical.append({"month": mo, "median": r1(med_by_month[mo]), "note": "historically busy"})

    # 6. Trend on full-year totals: Sen's slope + Mann-Kendall.
    ys = [totals[y] for y in full]
    n = len(ys)
    slopes = [(ys[j] - ys[i]) / (full[j] - full[i]) for i in range(n) for j in range(i + 1, n)]
    s = sum((ys[j] > ys[i]) - (ys[j] < ys[i]) for i in range(n) for j in range(i + 1, n))
    var = n * (n - 1) * (2 * n + 5) / 18
    z = (s - 1) / math.sqrt(var) if s > 0 else (s + 1) / math.sqrt(var) if s < 0 else 0.0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))

    m["summary"] = {
        "this_month": this_month,
        "busiest_months": [{"month": mo, "median": r1(med_by_month[mo])} for mo in top_months],
        "busiest_years": [{"year": y, "total": r1(totals[y])} for y in top_years],
        "season": {"start_month": start, "peak_month": peak, "end_month": end},
        "critical_next": critical,
        "trend": {"sen_slope_per_year": r1(statistics.median(slopes)), "p_value": round(p, 3)},
    }
    m["_note"] = (
        "FAKE DATA for building the UI only. Shape matches the real API. Do not show as real. "
        "The summary is derived from this calendar by mock/fix_summary.py."
    )

    with open(path, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(json.dumps(m["summary"], indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
