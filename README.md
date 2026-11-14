# FireLedger

One consistent record of satellite fire hotspots from 2000 to today, harmonized across
MODIS (Terra, Aqua) and VIIRS (S-NPP, NOAA-20), served as a burning-activity calendar
for any area you draw.

NASA Space Apps Challenge 2026: *Harmonization of MODIS and VIIRS Hot Spots*.

## Repo layout

| Path | What |
|---|---|
| `pipeline/ingest.py` | FIRMS archive zips → `data/hotspots/<product>_<year>.parquet` (548M rows, nothing dropped) |
| `pipeline/flags.py` | persistent hot spots → `data/derived/persistent.parquet` |
| `pipeline/calibrate.py` | learns conversion factors from the overlap years → `data/derived/{avail,calib,cellcounts}.parquet` |
| `pipeline/build_regions.py` | country + state presets from Natural Earth → `data/derived/regions.parquet` |
| `pipeline/validate.py` | proof pack (ablation, jump tests, band coverage) and conformal bands → `data/derived/{proof,bands}.json` |
| `pipeline/burned_area.py` | independent check against MODIS burned area (MCD64CMQ, GFED regions) → `data/derived/burned_area.json` |
| `pipeline/build_grid.py` | harmonized 0.25° monthly grid → `data/derived/grid.parquet` (fast path for big areas, map layer) |
| `pipeline/fetch_recent.py` | the current year (no archive zip yet) from the FIRMS area API → `data/hotspots/<product>_<year>.parquet` |
| `pipeline/harmonize_config.json` | the shipped fitting settings, read by calibrate, flags and validate |
| `pipeline/check_2012.py`, `pipeline/jump2012.py` | the app's accuracy checks against the live API; fast what-if runs of the factor table (`docs/experiments_2012.md`) |
| `pipeline/fetch_extras.py` | context and validation data that needs no account (CREAMS bulletins, GFED5, weather, fire weather, population, GOES, protected areas, crop calendars) → `data/external/` |
| `run_api.bat`, `update_data.bat` | Windows: start the API; download this year's hotspots and swap them in |
| `api/` | FastAPI backend (`main.py`), shared weight logic (`weights.py`) |
| `app/` | web app (Next.js) |
| `docs/api-samples/` | real responses from the API (Punjab, Córdoba) to build against |

`nasa-firms-data/` and `data/` are git-ignored (11 GB raw, ~9 GB derived).

## Run it

```bash
pip install -r requirements.txt

# once, ~7 min total on a laptop
python pipeline/ingest.py          # ~4.5 min, 6 worker processes
python pipeline/flags.py           # ~2.5 min; persistent hot spots + places either sensor types static
python pipeline/calibrate.py       # ~1.5 min
python pipeline/build_grid.py      # ~1.5 min
python pipeline/build_regions.py   # seconds; needs the Natural Earth zips in data/external/boundaries
python pipeline/validate.py        # ~3.5 min; proof pack + conformal error bands
python pipeline/experiments.py     # ~1.5 min; optional: noise floor, backcast, Aqua blend, N. America
python pipeline/burned_area.py     # 30 s; needs MCD64CMQ + one GFED4.1s file in data/external (see the script)

# API on port 8000 (reachable from the LAN); on Windows, double-click run_api.bat instead
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Interactive API docs: http://localhost:8000/docs

**Keeping it current.** FIRMS publishes a year's archive zip only after the year ends, so the
current year comes from the FIRMS area API (free key from
https://firms.modaps.eosdis.nasa.gov/api/map_key/, stored as `FIRMS_MAP_KEY=` in `.env`).
Science-quality data is used where it exists, near-real-time after that, up to yesterday (UTC).
On Windows `update_data.bat` runs all of it; elsewhere:

```bash
python pipeline/fetch_recent.py         # ~2.8 GB, ~1 hour for Jan-Nov (the key's 5000 transactions / 10 min is the limit); resumes
python pipeline/calibrate.py --avail-only   # new days only; factors stay as fitted
python pipeline/build_grid.py --recent      # grid for the new year only -> grid_recent.parquet
python pipeline/build_grid.py --mtd         # this month so far, days 1..D of every year (~30 s)
```

Months from the first near-real-time day on are marked `preliminary` (FIRMS reprocesses NRT
into science-quality data about 3 months later; `/api/health` gives `preliminary_from`).
NOAA-21 can be downloaded with `--products viirs-jpss2 --start 2023-03-01`; it has no factors
yet, so its hotspots count 0 and it is not part of the default run.

The month the data stops in is marked `partial`. Its summary compares days 1..D with the same
days of every other year (`compared_with: "Nov 1–13 in other years"`), not with full months.
When the year's archive zip appears, `ingest.py --force` replaces the downloaded file and a
full `build_grid.py` folds it into `grid.parquet`.

**Grid-only mode:** copy just `data/derived/` (~255 MB) to another machine and the API
runs from the grid alone (calendar, summary and map layer; no hotspot CSV).
`/api/health` reports `"mode": "grid-only"`.

## How the harmonization works

Every hotspot row gets a **weight** in the chosen unit, so the harmonized count for any
polygon and date range is just the sum of the weights of the hotspots inside it. The CSV
download includes each hotspot's weight, so any total can be reproduced by hand.

**Default unit: VIIRS-equivalent (NOAA-20).** S-NPP data ends in November 2026, so the
current month comes from NOAA-20, and the whole history is expressed in NOAA-20 terms.
MODIS is linked through S-NPP (MODIS → S-NPP → NOAA-20), because S-NPP overlaps the
pre-drift MODIS years (2012–2019) much longer than NOAA-20 does.

| Day type | Rows that count | Weight |
|---|---|---|
| NOAA-20 observing (2018 →) | NOAA-20 | 1 |
| no NOAA-20, S-NPP observing (2012–2017, outages) | S-NPP | `k_s` = NOAA-20 / S-NPP |
| before VIIRS, Terra + Aqua observing | Terra, Aqua | `k_ta · k_s`, with `k_ta` = S-NPP / (Terra+Aqua) |
| only Terra (Nov 2000 – Jul 2002) / only Aqua | that one | `k_t · k_s` / `k_a · k_s` |

The error spreads of chained factors add in quadrature. NOAA-21 will plug in the same way,
as one more link (`NOAA-20 / NOAA-21`).

**Alternative unit: MODIS-equivalent (Terra + Aqua).** Terra-only days are scaled up by
`m_t` = (Terra+Aqua) / Terra, which removes the jump when Aqua launched in 2002.

How the factors are learned (`pipeline/calibrate.py`):

- **Matched days only.** Ratios are sums over days when *both* satellites were observing.
  Whether a satellite was observing on a day is detected from its global daily count, so
  outages are found automatically.
- **Local factors.** Factors are computed per 1° cell × calendar month × day/night, then
  shrunk toward their 5° block and toward the global value (empirical Bayes), so sparse
  cells don't produce wild ratios.
- **Drift-aware windows.** Terra is calibrated on 2012–2019 and Aqua on 2012–2021, before
  their orbits drifted. S-NPP → NOAA-20 uses all of 2018–2025, since both VIIRS orbits are
  maintained. In Punjab, Aqua's mean daytime detection moved from 13:36 IST
  (2012) to 15:19 IST (2025), and Aqua's count relative to Terra rose from about 11× to
  about 107×. Months in the MODIS unit from 2021 onward carry `drift: true`.
- **Industrial hot spots get their own factors.** FIRMS already marks some hot spots as
  type 1–3 (volcano, static land source, offshore). `pipeline/flags.py` adds **persistent hot
  spots**: 1 km cells that burn in 9 or more months of a year, in at least 3 years. Crop fields and
  savanna burn once or twice a year, so they don't qualify. It found 15,600 cells. For the typical
  cell, FIRMS already types 92% of its detections as static, and 994 cells are ones FIRMS
  hadn't marked. These hot spots get their own factors (`*_st`), because VIIRS detects far
  more small hot industrial sources than MODIS. Converting them with the vegetation-fire ratio
  invented 2012 jumps in industrial regions: Chinese steel plants, the 2011–14 Bakken gas-flare
  boom, Russian flares. "Hide industrial" removes all of them, and the CSV has a `persistent` column.
- **One static mask for all sensors.** FIRMS's own type disagrees between sensors. At Holuhraun
  (Iceland, 2014), VIIRS typed the lava as vegetation fire and MODIS typed it as a static source.
  At Réunion in 2010, MODIS typed an eruption as vegetation. So a 0.05° place where either sensor
  family types at least half its detections as 1–3 counts as static for every sensor and year,
  plus a one-cell margin around places VIIRS types static (`data/derived/typed_static.parquet`).
- **Settings in one file.** `pipeline/harmonize_config.json` holds the fitting choices (static
  mask, smoothing strengths, month pooling) that `calibrate.py`, `flags.py` and `validate.py` read.
  The S-NPP → NOAA-20 factor is pooled almost to its month × day/night value, since the two
  carry the same instrument. Each 1° cell borrows 10% from its neighbouring months. How they
  were chosen, with the full results table: `docs/experiments_2012.md`.
- **Uncertainty.** Monthly totals get **conformal** 90% bands, learned from the held-out
  errors in `pipeline/validate.py`. The band width depends on the size of the total: bigger
  totals get tighter bands. The NOAA-20 part is observed and has no band. The S-NPP part
  carries the S-NPP → NOAA-20 band. The MODIS part carries both links, added in quadrature.
  Where the per-factor band `w · exp(±1.645·s)` is wider, that one is used instead. This
  happens for the Terra-only months, 2000–2002. The CSV gives each hotspot its per-factor band.

Global medians across cells: `k_s` 1.00, `k_ta` 5.6, `k_t` 12.2, `k_a` 12.0, `m_t` 1.9.

## Proof (`GET /api/proof`)

**Leave-one-year-out test.** For each year from 2012 to 2019, the factors are learned without
that year. MODIS counts for the hidden year are converted to VIIRS, and the result is compared
with what S-NPP actually saw (vegetation fires, matched days). The error is
Σ|predicted − actual| / Σ actual.

| Step | 1° cell × month | region × month | world × month |
|---|---|---|---|
| No correction (raw MODIS) | 77.2% | 77.0% | 77.0% |
| One global ratio | 27.4% | 14.7% | 6.8% |
| + per month and day/night | 27.0% | 14.1% | 2.4% |
| + per 5° region (shrunk) | 20.8% | 4.1% | 1.9% |
| **+ per 1° cell (shrunk) = FireLedger** | **17.1%** | **4.0%** | **1.9%** |
| − without shrinkage | 19.5% | 4.9% | 2.8% |
| − without matched days | 17.3% | 4.6% | 2.6% |
| − without drift-aware window | 17.0% | 4.4% | 2.2% (bias +1.3% vs +0.4%) |
| *tried:* + FRP-aware adjustment | 17.0% | 4.0% | 1.9% |
| *tried:* + zero-aware quantile mapping | 16.9% | 4.0% | 1.8% (bias −0.4%) |

Before the 15 Nov work (`docs/experiments_2012.md`) FireLedger's row was 17.6% / 4.18% / 2.0%.
Most of the gain is the static mask built from both sensors: vegetation factors no longer learn
from lava and flares.

**Tried and not adopted.**

- **FRP-aware adjustment.** In years when a place's MODIS fires are weaker than usual, MODIS
  misses more small fires, so the factor is raised; it's also raised for bigger pixels near the
  swath edge. The physics checks out: the slopes are −0.15 per unit of log FRP and +0.23 per unit
  of log pixel area. But it improves the error by only 0.2 points and leaves the 2012 test
  unchanged.
- **Quantile mapping.** It is just as small a gain, and it makes the 2012 consistency worse:
  a mean gap of 3.8 points across regions, against 2.9 for the ratio.

By the plan's rule, keep a step only if it wins; neither won clearly enough to ship.

The S-NPP → NOAA-20 link was tested the same way over 2018–2025. Its error is 9.0% per 1° cell
× month, 1.8% per region × month and 0.8% per world × month.

**The 2012 jump.** MODIS observes continuously through 2012, so its own change from 2009–11 to
2012–14 is the real change:

| Record | World | South America | Southern Asia | Eastern Asia | Northern America |
|---|---|---|---|---|---|
| MODIS (the real change) | −2% | −18% | −4% | +18% | +15% |
| Raw merged record (MODIS until 2011, VIIRS after) | +335% | +224% | +552% | +511% | +420% |
| Harmonized, all hot spots | −2% | −17% | −2% | +31% | +33% |
| Harmonized, **vegetation fires** vs MODIS vegetation | −3% vs −3% | −19% vs −20% | −1% vs −0% | **+17% vs +17%** | +22% vs +13% |

For the raw merged record, a Pettitt test puts the break at 2012 or 2013 (p < 0.001) in every
region.

For **vegetation fires**, the harmonized record matches MODIS's real change within 3 points in
13 of 20 regions and within 5 points in 17. The exceptions:
- Northern Europe (+24): a few thousand fires a year plus the edges of Icelandic lava fields.
- Northern America (+9): Alaska's 2009 fires vs the Northwest Territories' 2014 fires, places with
  different conversion factors.
- Eastern Europe (+6): Moscow's 2010 peat fires vs Yakutia in 2012–14.

Industrial hot spots are the part that **cannot** be fully harmonized: MODIS misses most small
gas flares and furnaces. When the Bakken flares or Chinese industry grew, MODIS barely saw it,
so no MODIS-based conversion can rebuild their earlier numbers. Giving them their own factors
cut Eastern Asia's gap from +42 to +18 points. Beyond that, the app reports each month's
`industrial` count and warns when industrial sources are more than 10% of an area. "Hide
industrial" then gives the like-for-like record.

At the 2018 S-NPP → NOAA-20 handover, the harmonized record matches S-NPP's own change within
3 points in every region.

**Error bands.** The per-factor bands, on their own, covered 28% (cell), 82% (region) and 100%
(world) of the true values, against a 90% target. The conformal bands are learned separately for
each **scale × latitude band**: 1° cell, 5° block, region and world; tropics, mid-latitudes and
high latitudes. A single table for everything left high-latitude 5° blocks at 84% coverage.
The API picks the table by the drawn area's size and latitude. With coverage measured on years
the tables were not learned from:

| Link | 1° cell | 5° block | region | world | 5° block: tropics / mid-lat / high-lat |
|---|---|---|---|---|---|
| MODIS → S-NPP | 90% | 90% | 89% | 88% | 90% / 90% / 90% |
| S-NPP → NOAA-20 | 90% | 90% | 90% | 89% | 90% / 92% / 92% |

Months mostly seen by a single MODIS satellite (Terra-only, Nov 2000 – Jul 2002) keep the wider
per-factor band, because the conformal tables were learned on Terra + Aqua years.

## Experiments (`pipeline/experiments.py` → `data/derived/experiments.json`)

| Experiment | Result | Verdict |
|---|---|---|
| **Noise floor.** Score a perfect-rate model with both counts drawn as Poisson | 7.3% error at 1° cell, 2.2% at 5° block, 0.2% at world (FireLedger: 17.6% / 9.2% / 2.0%) | About 40% of the local error is unavoidable counting noise. The rest is real year-to-year change in the VIIRS/MODIS ratio. |
| **Backcast.** Learn on 2015–2019 only, predict 2012–2014 | 18.6% cell / 4.6% region / 2.3% world, bias +1.3% (leave-one-year-out on the same years: 17.9% / 4.2% / 2.3%, bias +0.8%) | The factors hold over time, which supports applying them to 2000–2011. |
| **Ratio drift.** World VIIRS/MODIS by year, 2012–2019 | +0.33%/yr; tropics +0.27, mid-latitudes +0.85 (mostly 2018–19), high latitudes −0.24 | No clean trend to correct. If it extends back, the oldest years (2001–05) may read 2–5% high. Treat this as a systematic uncertainty. |
| **Aqua-anchored blend** (Aqua passes at 13:30 like VIIRS): a·k_a·Aqua + (1−a)·k_t·Terra, with a chosen on training years only | Best a = 0.8: 19.4% cell / 5.2% region, bias +2.9% | ❌ Worse than pooling Terra + Aqua (17.6% / 4.2%). More counts beat matched overpass times. |
| **Northern America gap** | USA −3 points; **Canada + Alaska +26** (VIIRS/MODIS swings 4.5 → 6.2 with the 2014 megafire year) | The remaining vegetation gap is boreal megafires, where the ratio depends on the fire year. |

**Next ideas, not built:**

- **Normalized (locally adaptive) conformal bands.** Mid-latitude 5° bands are about ±37%,
  driven by eastern China. That's honest on average but probably too wide for steadier places
  like Punjab.
- **Same-day de-duplication** of overlapping-orbit detections at high latitudes.
- **Land-cover-specific factors**, which need the land-cover download.
- **Virtual MODIS (VIIRS → MODIS)**, so the MODIS-equivalent unit survives after MODIS ends in 2027.

## Burned-area check (`pipeline/burned_area.py`, in `GET /api/proof` as `burned_area`)

An outside check that doesn't use our own factors. MODIS burned area (MCD64CMQ, burn scars at
0.25° per month) is a different measurement taken by MODIS alone, so it has no 2012 sensor
change. We count vegetation-fire hotspots **per km² burned** in the 14 GFED regions, 2001–2019
(burned area itself changes once the MODIS orbits drift, so later years are left out).

| | Raw MODIS | Raw merged (MODIS → VIIRS) | FireLedger |
|---|---|---|---|
| World: change in hotspots per km² burned, 2012–15 vs 2008–11 | +4% | **+343%** | **+3%** |
| Regions within 8 points of MODIS's own change | | 0 of 14 | **13 of 14** (Boreal Asia +8.3; mean gap 3.1) |
| Change point in the ratio (Pettitt) | | 2011–12 in 14 of 14, p = 0.003 | none at p < 0.05 |
| Year-to-year agreement with burned area (Spearman ρ, World) | | −0.46 | **+0.80** |
| Same, range over the 14 regions | | −0.45 to +0.62 | **+0.43 to +0.93** |

The raw merged record says fires suddenly got 4× denser per km² burned in 2012, and its ups and
downs don't follow burned area. The harmonized record follows burned area through the switch.
(The 2012 step and the gap to MODIS are the jump test seen through burned area; the
year-to-year agreement is the new, independent part.)

## Official counts check (`pipeline/creams.py`, in `GET /api/proof` as `creams`)

India's ICAR-IARI CREAMS lab publishes a daily bulletin of crop-residue fire counts per state
during the wheat (April–May) and paddy (Sept–Nov) seasons. They use their own protocol: cropland
only, their own satellite mix. So the absolute numbers differ from raw FIRMS, but the seasons
should match.

| Punjab, paddy season (15 Sep → 30 Nov) | CREAMS (official) | FireLedger |
|---|---|---|
| 2023 | 36,488 | 33,382 |
| 2025 | 5,114 | 4,446 |
| 2026, to 13 Nov | 5,785 | 4,170 (S-NPP ended 2 Nov) |
| Day-by-day rank agreement (Spearman) | | 0.89 |

Both show the 2023 → 2025 collapse in stubble burning: −86% official, −87% FireLedger. Haryana and
the 2022 wheat season agree at 0.79–0.86 day to day. The bulletins themselves are fetched by
`pipeline/fetch_extras.py --only creams`.

## API

All POST endpoints take the same body:

```json
{
  "region": "IND-3249",
  "geometry": { "type": "Polygon", "coordinates": [[[73.9,30.0],[76.9,30.0],[76.9,32.5],[73.9,32.5],[73.9,30.0]]] },
  "start": "2000-11",
  "end": "2026-11",
  "unit": "viirs",
  "daynight": "both",
  "hide_industrial": false,
  "harmonized": true,
  "ref_month": "2025-11",
  "precision": "auto"
}
```

- `geometry`: a Polygon or MultiPolygon, or a Feature or FeatureCollection. Holes work.
- `region`: a preset id from `/api/regions` (e.g. `IND`, `ARG`, `IND-3249` = Punjab). It is
  used when `geometry` is empty.
- `start` and `end`: `YYYY-MM` or `YYYY-MM-DD`. `end` defaults to the latest day with data.
- `unit`: `viirs` or `modis`.
- `daynight`: `D`, `N` or `both`.
- `harmonized`: `false` returns the record as it stands (raw MODIS before VIIRS, raw VIIRS after),
  the "before" view with the 2012 jump.
- `ref_month`: used by the summary only. It defaults to the last month in the range.

The app's contract (`mock.json` + its HANDOFF) and how the API meets it are in
[`docs/API_FOR_APP.md`](docs/API_FOR_APP.md). A bad body returns 400.
- `precision`: `auto` uses exact points for areas under 50 deg² (about 7° × 7°) and the grid above that.
  You can also force `exact` or `grid`.

| Endpoint | Returns |
|---|---|
| `POST /api/calendar` | `{unit, method, months: [{year, month, count, lo, hi, flags, raw_modis, raw_viirs, raw_snpp, raw_noaa20, industrial, source, drift, partial, through_day, preliminary}]}`, ending at the latest day with data. `flags`: `M` counted from MODIS, `C` cross-calibrated VIIRS, `D` MODIS orbit drift, `B` blind spot (VIIRS would see ≥ 8× what MODIS did). `industrial` is the part of `count` that comes from industrial or persistent hot spots. `count: null` means no satellite was observing. `raw_viirs` is the raw VIIRS record as it stands (NOAA-20, or S-NPP when NOAA-20 is missing). `source` is `VIIRS`, `S-NPP→VIIRS`, `MODIS→VIIRS`, `MODIS` or `no data`. `partial: true` marks the month the data stops in (`through_day` = its last day). |
| `POST /api/summary` | `this_month {year, month, count, median, p25, p75, pct_diff, anomaly (robust z), anomaly_label, days_covered, compared_with, same_days_past, full_month_median, flags, label, lo, hi, percentile, rank, of_years, record, preliminary}`, `busiest_months [{month, median}]` (top 3 by median, complete months), `busiest_years [{year, total}]` (top 3 full years), `season {start_month, peak_month, end_month}`, `critical_next [{month, median, note}]` (busy months in the next 6), `trend {sen_slope_per_year, p_value, ...}`, `industrial {share, warning}`, `season_timing` (see below), `climatology` (12 months), `annual` |
| `POST /api/hotspots.csv` | every hotspot in the area: `acq_date,acq_time,latitude,longitude,satellite,instrument,frp,confidence,daynight,type,weight`; `?full=true` adds brightness, pixel size, persistent flag, version, country, `weight_lo`, `weight_hi`, `counted`; streamed; 413 above 3M rows |
| `GET /api/presets` | demo areas for the app: `[{id, name, geometry}]` (one Polygon each: Punjab, Haryana, Northern Patagonia, Córdoba, Mato Grosso, California, Northern Territory, Yakutia, Central Kalimantan) |
| `GET /api/grid?year=2019&month=&unit=viirs&res=0.5` | map layer: `{max, cells: [[lat, lon, count]]}` (res 0.25 / 0.5 / 1 / 2°) |
| `GET /api/proof` | the validation results above, as JSON (`ablation`, `jumps`, `band_coverage_90`, `conformal_bands`) |
| `GET /api/regions?q=punjab&kind=state` | presets (247 countries, 4,596 states/provinces; Natural Earth, India point of view): `[{id, name, kind, country, subregion, bbox}]` |
| `GET /api/regions/{id}` | one preset as a GeoJSON Feature |
| `GET /api/health` | `{ok, coverage {start, end}, mode, month_to_date {month, through_day}, preliminary_from {viirs, modis}}` |

`season_timing` in the summary covers the **main season**, meaning the run of months around
the busiest month. The run stops at a trough, so separate seasons stay separate. It gives:

- `months` and `share_of_year`.
- `start`, `peak` and `end`: when 10% of the season's hotspots have burned, the busiest month,
  and when 90% have burned. Values look like "early Oct".
- `second_season`: the same fields for a second season, if there is one. Punjab, for example:
  main Oct–Nov (74%), second May (21%, wheat residue). Seasons that cross New Year (Sahel,
  Nov–Feb) stay whole.
- `core_3_months`: the busiest three consecutive months and their share of the year.
- `shift`: Sen's slope of the main season's start, centre and end in days per decade, with
  Mann-Kendall p values. Punjab: "main season starts 4 days later per decade". A synthetic
  test season moved 12.7 days per decade, and the method recovered 13.
- `by_year`: the timing for each year.

`anomaly_label` comes from where this month ranks among the same calendar month in the
other years: ≤10th percentile is "well below normal", ≤25th is "below", ≥75th is "above",
≥90th is "well above".

Typical response times on a laptop: an area the size of Punjab takes about 1 s with
exact points; India, Africa or the whole world takes under 1.5 s from the grid.

## Data

Boundaries: Natural Earth 1:10m admin-0 (India point of view) and admin-1, public domain.

Extra data (`pipeline/fetch_extras.py`, each folder in `data/external/` has a `SOURCE.txt` with
its link and licence; about 10 GB, the fire-weather part runs overnight):

| Folder | What | For |
|---|---|---|
| `creams/` | ICAR-IARI CREAMS daily crop-residue fire bulletins, 2022–2026 (PDF) | official Punjab/Haryana counts to compare with ours |
| `gfed5/` | GFED5 burned area, 0.25°, monthly, to 2022 (Zenodo, CC BY 4.0) | second burned-area check |
| `weather/` | NOAA PSL monthly precipitation (GPCP, PREC/L), temperature (GHCN_CAMS), soil moisture (CPC) | "driest month in 10 years" notes |
| `fwi/` | NASA GFWED fire weather (MERRA-2), reduced to monthly means | fire danger next to fire activity |
| `population/` | WorldPop 2020, 1 km (CC BY 4.0) | people living near fires |
| `goes16_fdc/` | GOES-16 full-disk fire product, hourly, Aug–Sep 2024 | the daily fire cycle between overpasses |
| `wdpa/` | World Database on Protected Areas (non-commercial, no redistribution) | fires inside protected areas |
| `crop_calendar/` | GGCMI Phase 3 crop calendars, 0.5° (Zenodo) | why residue burning peaks when it does |

Not downloaded because they need an account: MODIS land cover MCD12C1 and cloud fraction
MOD08_M3 (Earthdata), ERA5 and Copernicus fire danger (CDS), Sentinel-5P (Copernicus).

NASA FIRMS archive (standard products): MODIS C6.1 MCD14ML, VIIRS 375 m VNP14IMG (S-NPP)
and VJ114IMG (NOAA-20). https://firms.modaps.eosdis.nasa.gov/. FIRMS data is provided
by NASA's LANCE/ESDIS "as is"; see the FIRMS disclaimer.
