# FireLedger: project report

NASA Space Apps Challenge 2026, "Harmonization of MODIS and VIIRS Hot Spots".
Written Sun 15 Nov 2026, about 14:40 IST, after a full read of every file on the app laptop and fresh
checks against the public API (`https://dhruv-deku--fireledger-web.us-east.modal.run`).

`pipeline/` and `api/` are not on this laptop, so the backend was checked only through its answers.

---

## 1. In one paragraph

FireLedger turns 26 years of MODIS and VIIRS fire hotspots into one record in NOAA-20 VIIRS units.
In held-out years the conversion misses a region's monthly count by **4.0%** on average.
Uncorrected MODIS misses by 77%. The raw record jumps **+335%** worldwide when VIIRS joins in 2012.
After harmonization the jump left over is **−0.9%**. The app (home globe, map app, accuracy page)
builds, type-checks and lints cleanly. The public server is "grid-only", which causes the biggest
problems today:
- no hotspot CSV;
- no single-detection dots;
- areas counted in whole 0.25° cells, so a small area can come back empty;
- a 500 error for any area with no fires.

The app now handles the last two gracefully. The rest needs the data side.

## 2. Key numbers

| What | Result | Target / meaning |
|---|---|---|
| Conversion error, region × month (held-out years 2012–19) | **4.03%** | ≤ 5% ✓ |
| Same, single 1° cell × month | 17.1% | errors average out over larger areas |
| Same, whole world × month | 1.91% | |
| Bias, region × month | **+0.38%** | close to 0 ✓ |
| Uncorrected MODIS, region × month | 77.0% (bias −77%) | what the conversion removes |
| Extra step S-NPP → NOAA-20 units | +1.8% region-month, 9.0% cell-month | |
| 90% likely-range coverage, region × month | **89.1%** | 85–95% ✓ |
| 90% likely-range coverage, cell / world | 89.6% / 89.6% | ✓ |
| 2012 jump, raw record, world | +335% | the problem |
| 2012 jump left over after harmonization, world | **−0.9%** | ✓ |
| World regions within ±5% at 2012 | **17 of 20** | off: Eastern Europe +6.3%, Northern America +8.9%, Northern Europe +23.9% |
| Break test (Pettitt) finds a 2011–13 break | raw 20 of 20 regions → harmonized 1 of 20 (Central Asia) | |
| Follows burned area (Spearman), world | raw −0.46 → harmonized **0.80** | independent check; all 14 regions improved |
| Daily match with India's official crop-fire counts (CREAMS) | Punjab paddy **0.885**, Punjab wheat 0.855, Haryana paddy 0.79, Haryana wheat 0.80 | rank correlation of burning days |
| Hotspot CSV weights = calendar count (full server, 6 cases) | **0.000–0.002%** apart | ≤ 0.1% ✓ (no data lost between the two) |
| Automated checks, full server (laptop, 15 Nov am) | **17 / 18** pass | |
| Automated checks, public server (now) | **11 / 12** pass, 6 CSV checks can't run | grid-only |

### About "precision"

Classic precision/recall needs a list of true fires, and there is none. The closest measures here:
- **Bias (systematic error):** +0.38% per region-month. No sign of over- or under-counting.
- **Calibration of the likely range:** 89.1% of region-months fall inside the 90% range. The ranges
  are honest, neither too narrow nor too wide.
- **Width of the likely range (how sure each number is):** the median (hi − lo) / count for each
  preset is below. MODIS-only years are much less certain than the VIIRS years, as expected.

  | Preset | MODIS years (≤2011) | 2012–17 |
  |---|---|---|
  | Northern Territory | 27% | 13% |
  | Mato Grosso | 37% | 18% |
  | California | 71% | 31% |
  | Yakutia | 70% | 35% |
  | Punjab | 99% | 44% |
  | Haryana | 99% | 44% |
  | Central Kalimantan | 96% | 69% |
  | Córdoba | 99% | 63% |
  | Patagonia | 147% | 63% |

## 3. Accuracy in detail

### 3.1 What each step of the method adds (held-out error, region × month)

| Step | Region | Cell | World | Bias |
|---|---|---|---|---|
| No correction (raw MODIS) | 77.0% | 77.2% | 77.0% | −77.0% |
| One global ratio | 14.7% | 27.4% | 6.8% | 0.0% |
| + per month and day/night | 14.1% | 27.0% | 2.4% | +0.05% |
| + per 5° region (shrunk) | 4.10% | 20.8% | 1.90% | +0.42% |
| **+ per 1° cell (shrunk) = FireLedger** | **4.03%** | **17.1%** | **1.91%** | **+0.38%** |
| + FRP-aware adjustment | 3.95% | 17.0% | 1.89% | +0.42% |
| + quantile mapping (zero-aware) | 4.02% | 16.9% | 1.85% | −0.38% |
| − without shrinkage | 4.89% | 19.5% | 2.80% | +2.45% |
| − without matched days | 4.56% | 17.3% | 2.61% | −0.26% |
| − without drift-aware window | 4.37% | 17.0% | 2.24% | +1.29% |

Each part of the shipped model earns its place. Removing shrinkage, matched days or the drift window
makes it worse. The FRP-aware variant is slightly better (3.95%) but wasn't shipped. That's a free
0.08-point gain the data side could take.

### 3.2 The 2012 switch, by world region (vegetation fires, 2012–15 vs 2008–11)

| Region | Raw | MODIS alone (truth) | Harmonized | Left over |
|---|---|---|---|---|
| World | +335% | −2.5% | −3.4% | −0.9% |
| Middle Africa | +273% | −4.2% | −6.3% | −2.1% |
| Eastern Africa | +369% | +0.9% | −1.5% | −2.4% |
| South America | +224% | −19.5% | −18.8% | +0.7% |
| Western Africa | +299% | −9.7% | −12.6% | −2.9% |
| **Eastern Europe** | +367% | +15.0% | +21.3% | **+6.3%** |
| Australia and NZ | +433% | +14.0% | +13.1% | −0.9% |
| South-Eastern Asia | +438% | +9.1% | +5.3% | −3.8% |
| **Northern America** | +420% | +12.9% | +21.8% | **+8.9%** |
| Southern Asia | +552% | −0.4% | −0.8% | −0.4% |
| Eastern Asia | +511% | +16.7% | +17.3% | +0.6% |
| Central America | +288% | −17.8% | −17.2% | +0.6% |
| Southern Africa | +241% | −22.5% | −27.4% | −4.9% |
| Northern Africa | +672% | +11.9% | +9.5% | −2.4% |
| Central Asia | +293% | −10.2% | −13.9% | −3.7% |
| Western Asia | +380% | −40.1% | −41.5% | −1.4% |
| Southern Europe | +304% | −0.7% | +3.0% | +3.7% |
| Western Europe | +662% | −2.9% | −0.6% | +2.3% |
| Caribbean | +250% | −26.7% | −25.4% | +1.3% |
| Melanesia | +433% | −1.0% | +2.0% | +3.0% |
| **Northern Europe** | +1264% | −26.6% | −2.7% | **+23.9%** |

Presets: the leftover is reported next to the API's own "2012 switch" badge, not used as a pass/fail
gate (decided 15 Nov).

| Preset | Leftover | Badge |
|---|---|---|
| Punjab | +0.8% | good |
| Haryana | −3.7% | good |
| Northern Territory | +1.4% | good |
| California | +4.0% | good |
| Mato Grosso | −5.7% | good |
| Córdoba | +7.5% | good |
| Yakutia | +8.0% | good |
| Patagonia | +8.6% | fair |
| Central Kalimantan | +11.7% | fair |

### 3.3 Independent checks

- **Burned area (MODIS burned-area maps, no sensor change in 2012).** The rank correlation between
  yearly hotspots and burned area went from −0.46 (raw) to 0.80 (harmonized) worldwide, and up in all
  14 regions. The weakest is Southern Africa at 0.435.
- **India's official counts (ICAR-IARI CREAMS, data to 13 Nov 2026).** The burning days line up
  (Spearman 0.79–0.885). Season totals:
  - Punjab paddy: 2023 official 36,488 vs FireLedger 33,382; 2025 5,114 vs 4,446; 2026 so far
    5,785 vs 4,170.
  - Haryana: FireLedger runs higher than the official count (2023: 3,270 vs 2,252).

  CREAMS uses its own protocol, so the totals are expected to differ.
- **Industrial sites, own conversion (since 15 Nov).**
  - Per region, the error went from 23.4% to 21.0% and the bias from −17% to +2.7%.
  - Per single cell it got worse, 61.9% → 78.8%. The accuracy page says so.

### 3.4 Data loss

- **Between the CSV and the calendar: none.** The sum of hotspot weights equals the calendar count
  within 0.002% in all 6 test cases on the full server.
- **Fires only MODIS saw: real loss in VIIRS units.** From 2012 a MODIS detection with no VIIRS
  detection nearby counts 0.
  - Punjab Nov 2016: 4,044 of 9,057 such rows (45%).
  - Haryana Oct 2021: 42%.
  - Patagonia Jan 2015: 8%.
  - California Aug 2020: 1%.

  They stay in the full CSV. The API doesn't report them per month yet (`modis_only` is null), so
  the app's slot for them stays hidden. These shares came from the check before today's fix to its
  neighbour search (see 4.1); they may be slightly high where MODIS pixels are wide.

## 4. Errors found

### 4.1 Fixed today (app side, verified)

| # | Where | Problem | Fix |
|---|---|---|---|
| 1 | Map app, results panel | Drawing an area with no fires (ocean, desert, a very small area) showed **"Results did not load"**. The API answers 500 to `/api/summary` in that case. | The app now loads the calendar and summary separately. If the calendar has no fire at all, the panel says "No hotspots here. No satellite detected a fire in this area from … to …". Verified with a 1° Sahara box. |
| 2 | Map app, small areas | On the public grid-only server, an area smaller than one 0.25° cell returns **0** even in Punjab in November. Larger small areas are counted in whole cells. Nothing told the user. | Small areas now get a note: the server counts whole 0.25° cells, edges are approximate, and an area under one cell can come back empty. The empty state adds "Draw a larger area". The accuracy page lists it as a known limit while the server is grid-only. Verified on a 663 km² box in Punjab. |
| 3 | Download menu | An error message (e.g. a failed download) **never went away** and kept covering the panel. | It clears itself after 6 seconds. |
| 4 | Month-by-month chart | The zoom slider used red-orange `#ff4d00`. That breaks the design rule that amber is the only UI accent. | Uses the theme's neutral band colour. |
| 5 | `mock/evaluate_api.py` | (a) It crashed on a preset with no month over 50 hotspots (formatting `None`). (b) It crashed on a grid-only server unless `--skip-csv` was passed. (c) Its "fires only MODIS saw" search looked only one 0.05° cell around each MODIS pixel. That's too narrow away from the equator. `int()` also merged cells across 0° latitude and longitude. | (a) prints `n/a`; (b) detects grid-only from `/api/health` and skips the CSV part with a note; (c) uses `floor` and searches as far as the pixel reaches. Re-run: 11 pass, 1 fail, same as before, with no crash. |
| 6 | Studio intro (fixed earlier today) | It replayed on every page load (a server file read a constant from a client file). Stray dots showed before each stroke. | The constant moved to `lib/intro.ts`; the dash offset now goes past the line cap. |

### 4.2 Needs you (can't be done from code)

| # | Problem | What to do |
|---|---|---|
| 7 | **`app/.env.local` points to `http://192.168.1.9:8000`**, the laptop address from the old Wi-Fi. A production build (`npm run build`, or a deploy that uses this file) bakes that dead address in. I confirmed it inside the built files. The dev server only works because the dev launch config overrides it. | Set `NEXT_PUBLIC_API_URL=https://dhruv-deku--fireledger-web.us-east.modal.run` in `app/.env.local`, and the same variable in Vercel's project settings. This file is protected by your hook, so you have to edit it yourself. |
| 8 | A second `app/app/.env.local` exists. Next.js never reads it (it only reads `app/.env.local`), so it's confusing at best. | Delete it once item 7 is done. |
| 9 | **No git repository on this laptop.** There's no history and no backup, and nothing is pushed. The brief says "small commits, push often". | Run `git init` (or clone the team repo) and commit the app before submitting. |

### 4.3 Needs the data person (API)

| # | Problem | Seen as |
|---|---|---|
| 10 | **`POST /api/summary` returns 500 for any area with zero hotspots.** The 500 also has no CORS header, so browsers report it as a CORS error. | Sahara 1° box, a Pacific box, a 0.05° box in Punjab. The app now copes, but the API should return a summary with zeros. |
| 11 | **The public server is grid-only.** There's no `hotspots.csv` (503), no single detections for the close-up map, and areas are counted in whole 0.25° cells. In Punjab, 2016: a 0.05° box gives 0, a 0.25° box 875, a 0.7° box 14,583. | Full mode on Modal, or area-weighted cells, would fix the CSV, the dots and small areas. |
| 12 | `GET /api/grid` refuses `res` below 0.25 and **ignores `bbox`**: asked for 75–76°E, 30–31°N, it returned cells in Ukraine. | Each map load logs two 400s while the app falls back to 0.25° (expected, harmless). |
| 13 | 3 of 20 world regions are still more than 5% off at 2012: Northern Europe +23.9%, Northern America +8.9%, Eastern Europe +6.3%. | The one failing automated check. |
| 14 | `modis_only` is null in every month, so fires only MODIS saw can't be shown per month. | The app slot is ready and hidden. |
| 15 | The industrial conversion is worse per single cell (61.9% → 78.8%). | Shown on the accuracy page. |
| 16 | The FRP-aware variant tested better (3.95% vs 4.03%) but isn't the shipped model. | Optional. |

### 4.4 Minor, not fixed (low risk, noted for honesty)

- `ANOMALY` in `lib/chartTheme.ts` is no longer used and still holds the old grey (`#27272a`). The
  live colours come from CSS tokens.
- A drawn area that crosses the 180° meridian would be framed and labelled using a box the wrong way
  round the globe. That's an edge case, not tested in the app.
- The home page "Burning in …" feed makes 6 OpenStreetMap name lookups per visit, spaced 1.1 s
  apart. That's fine for a demo but not for heavy traffic.
- Three identical copies of `mock.json` exist (root, `mock/`, `app/lib/`). The root also holds a
  stale `eval_api_final.json` and a 3.7 MB photo (`1000016122.jpg`).
- For the demo: Punjab's full-period trend is "no clear trend" (p = 0.34), while the VIIRS-only trend
  since 2012 is falling about 50% per decade (p = 0.019). Both are correct. Say which one you're
  showing.

## 5. What was built (app side)

- **Home (`/`):**
  - a 3D night globe with last month's harmonized hotspots;
  - the five fire satellites on their real sun-synchronous orbits (height, tilt, equator-crossing
    time, sped up 120×);
  - "Open the app" and "Accuracy and trust";
  - a live **"Burning in …" feed**: the six busiest fire areas of the last full month, each against
    the same month in other years, with that day's NOAA-20 image;
  - five historic fire seasons;
  - the satellite timeline.
- **Map app (`/app`):**
  - **Map:** a 2D map or 3D globe; six base maps (physical, satellite, terrain, political with India's
    official borders, street, night); a fire layer per month with a play-through timeline.
  - **Areas:** search, draw a shape or box, a circle by coordinates, learned places, presets; deep
    links (`?place=`, `?month=`, `?area=`).
  - **Filters:** units, day/night, raw vs harmonized, hide industrial.
  - **Results panel:**
    - this month vs normal, with the same-days comparison;
    - the last-30-days card;
    - the burning calendar, with blind-spot marks and the "2012 switch" badge;
    - busiest months and years (blind-spot years optional), fire season, coming up;
    - the trend, including VIIRS-only from 2012;
    - the month-by-month chart with the likely range;
    - industrial and small-area notes.
  - **Downloads:** hotspot CSV (contract or every field), monthly CSV with every API field, and a
    calendar PNG.
- **Accuracy and trust (`/accuracy`):** every number from `/api/proof`:
  - error per method step;
  - band coverage;
  - the 2012 table per region and the break test;
  - the burned-area check;
  - the CREAMS comparison;
  - the industrial line;
  - known limits, each with what the app does about it.
- **Design:** the "Night pass" identity (dark blue-black, amber accent, Archivo), a light theme,
  reduced-motion support, phone layouts, and the "Wolfram Devs" studio intro (Montserrat, once per
  visit, skippable).
- **For the data person:** `docs/REQUEST_FINER_GRID.md`, `REQUEST_ACCURACY.md`,
  the 2012-jump and data-limits requests.
- **Checks:** `mock/evaluate_api.py` (accuracy, consistency and data-loss checks) and
  `mock/validate_mock.py` (mock vs contract).

## 6. How this was checked

- **App:** `tsc --noEmit` passes, `eslint` passes, and `next build` passes (all 4 pages prerender).
- **Python:** all 5 scripts compile. `validate_mock.py mock/mock.json` gives 0 errors and 1 warning
  (the mock starts Jan 2001; the record starts Nov 2000).
- **`evaluate_api.py` against the public API:** 11 pass, 1 fail (2012 world regions). The CSV part
  is skipped because the server is grid-only.
- **API edge cases probed:**
  - empty areas;
  - a tiny area;
  - MODIS units;
  - raw counts;
  - a past-only date range;
  - a box across 180°.
- **In the browser:**
  - the Sahara box shows the new empty state;
  - a 663 km² Punjab box shows the small-area and grid notes;
  - the accuracy page shows the grid-only limit;
  - no new console errors.

## 7. Before 16:00, in order

1. Fix `app/.env.local` (item 7) and set the same variable in Vercel.
2. Put the app under git and commit (item 9).
3. Ask the data person for item 10 (summary on empty areas) and, if possible, full mode on Modal
   (item 11).
4. Deploy, then open `/`, `/app?place=punjab&month=2016-11` and `/accuracy` on the deployed URL.
