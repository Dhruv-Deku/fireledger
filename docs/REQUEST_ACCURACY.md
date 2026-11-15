# Request: accuracy fixes for the pipeline/API

From the app side, 14 Nov 2026. Everything below was measured on the live API with
`mock/evaluate_api.py` (stdlib only, same endpoints as the app). Run it before and after a
change; it prints PASS/FAIL and exits 1 if anything fails:

```powershell
python -I mock/evaluate_api.py --api http://localhost:8000 --out eval_after.json
```

Baseline today: **20 passed, 7 failed.** Targets are at the top of the script.

## What already holds (don't lose these)

| Check | Now |
|---|---|
| Conversion error, region × month (held-out 2012–19) | 4.18% (raw MODIS 77%) |
| 90% band coverage, region × month | 89% |
| World jump at 2012, vegetation, harmonized vs MODIS alone | −0.7% |
| CSV weights = calendar count | 6/6 cases, max 0.001% |
| Contract (no gaps, nothing negative, count inside band) | 9/9 presets, 0 problems |
| 2012–17 months carry a range | all of them |

## 1. Leftover jump at the 2012 switch (biggest problem)

The held-out error is low, but several places still change at 2012 more (or less) than MODIS alone did:

| Where | Left over | Source |
|---|---|---|
| Northern Europe | +267% | `/api/proof` jumps, vegetation |
| Western Europe | −37% | same |
| Northern America | +9.4% | same |
| Eastern Europe | +5.9% | same |
| California preset | +26.6% | script, all types, ratio of changes 2012–15 vs 2008–11 |
| C. Kalimantan / Patagonia / Yakutia / Córdoba / Mato Grosso | +10.1 / +8.0 / +7.6 / +6.9 / −5.9% | script |

Both can be true because the held-out test scores MODIS→S-NPP on *matched days within 2012–19*, while the
jump test scores the whole record *across* the switch (all days, the S-NPP→NOAA-20 step, and the
conversion applied back to 2001–11).

Suggestions, in order of expected gain:

1. **Choose the model by both scores.** Today the 1° shrunk model is picked on held-out error alone.
   Add the leftover jump (per region) to the selection. The ablation's FRP-aware variant already scores
   4.11% vs 4.18%; ship it if the jump test doesn't get worse.
2. **Shrink small regions toward their continent, not the world.** Northern/Western Europe have ~21k/45k
   hotspots a year and 43%/73% industrial share, so local ratios rest on few vegetation fires. Use a local
   ratio only above a minimum number of matched pairs; otherwise fall back one level up.
3. **Fit vegetation ratios on vegetation only** (type 0, not persistent). Convert industrial and
   persistent sources with their own ratio (see 2).
4. **Stratify mixed regions** (California: crop burns next to forest fires) by FRP class or land cover
   inside the 1° cell.

Done when: `2012 jump removed, world regions` and every preset's `2012 jump` PASS (within 5%), and
region-month error stays ≤ 4.18%.

## 2. Industrial sources get the vegetation weight

Haryana, April 2010 (MODIS only): "Hide industrial" removes **10% of rows but 24% of the count**.
Industrial MODIS pixels average a weight of **10.8**, while vegetation pixels average **4.0**. A flare or brick kiln is
seen by both sensors far more often than a crop fire, so the vegetation ratio overstates it.

Fix: a separate MODIS→VIIRS ratio for type ≠ 0 and persistent cells, from the overlap years.
Done when: in MODIS years, the share of the count that "Hide industrial" removes is close to its share of rows.

## 3. Fires only MODIS saw (data loss in VIIRS years)

Nothing is deleted: these rows stay in the CSV with weight 0. But they don't count. Share of uncounted
MODIS detections with **no VIIRS detection within their own pixel on the same UTC day**:

| Case | MODIS-only | of uncounted MODIS rows |
|---|---|---|
| Punjab, Nov 2016 | 4,044 | 44.7% |
| Haryana, Oct 2021 | 334 | 42.1% |
| Patagonia, Jan 2015 | 44 | 8.2% |
| California, Aug 2020 | 137 | 1.2% |

Likely short crop burns that started or ended between passes (NOAA-20 crosses about 50 minutes before
S-NPP and Aqua). Long wildfires are seen by both. This is our reading, not yet checked.

Counting them in the harmonized number would bring back the sensor dependence (more satellites later
means more fires), so we suggest leaving the count as is and adding an **optional extra field per month**,
e.g. `modis_only` (detections no VIIRS satellite saw that day), so the app can show "plus N fires only MODIS
saw" without mixing it into the calendar.

## 4. Precision: ranges for state-sized areas

Band coverage is 89% for UN regions, and cell-level bands are widened ×4.72. Nobody has measured coverage
for state-sized areas (what users draw). In Punjab the median range is 110% of the count in MODIS years.
If an area's band is the sum of its cells' bands, it is too wide, because cell errors partly cancel.
Suggest: add coverage and width for state-sized areas to `/api/proof`, and build area bands from held-out
errors at that scale.

## 5. Small additions that help the app (optional fields, contract unchanged)

- `/api/summary` → `reliability: {region, leftover_2012, verdict}` for the area. Today the app guesses the
  region from country bounding boxes (`app/lib/reliability.ts`).
- Finer grid cells: see `docs/REQUEST_FINER_GRID.md`.
