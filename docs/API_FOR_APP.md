# API ↔ app: reply to HANDOFF.md

## 15 Nov, part 2: the known limits as data (reply to PROMPT_FOR_DATA_LIMITS.md)

New **optional** fields. No field was renamed, removed or changed in meaning. Every check result is
identical to before: 22/27 on our copy, the same leftovers, regions, held-out error and CSV sums.
Examples below are the live Punjab preset, full range.

| Field | Where | What | Example (Punjab) |
|---|---|---|---|
| `modis_only` | every month, and `this_month` | MODIS detections (any type, same area and filters) with **no VIIRS detection within their own pixel**, max(1 km, `scan`), on the same UTC day. Raw count. `null` before 2012, and for areas answered from the grid (bigger than 50 deg², e.g. Yakutia, Mato Grosso, N. Territory, whole countries); single detections are needed. | Nov 2016: **4,053** of 9,057 MODIS detections |
| `reliability.leftover_2012_range` | summary | 90% range of `leftover_2012`, resampling whole years before (2008–11) and after (2012–15), 1,000 draws, fixed seed | `[-0.060, +0.081]` around +0.009 |
| `reliability.noise_2012` | summary | The same check inside the overlap years: \|log((MODIS change)/(S-NPP change))\| from 2012–15 to 2016–19, vegetation fires, days all three satellites observed. How noisy MODIS's own change is here. `null` if the request doesn't cover 2012–2019. | `0.017` |
| `trend_viirs` | summary | Same shape as `trend`, full years from 2012 only (no MODIS conversion). `null` if fewer than 6 full years, or `harmonized` is false. | 2012–2025, falling 45% per decade, p = 0.014 |
| `busiest_years_no_blind` | summary | Like `busiest_years`, skipping years with a `B` month | 2016 (107,150), 2020 (95,831), 2014 (95,527). 2001 and 2002 are gone. |
| `industrial` | every month, and `this_month` | Already there; now confirmed numeric on every month with data (0 when there's none), including the month in progress | Nov 2016: 85.6 |

**Acceptance checks:**
- `modis_only` against your script:
  - Punjab 2016-11: 4,053 vs 4,044 (+0.2%)
  - Haryana 2021-10: 334 vs 334
  - California 2020-08: 137 vs 137
  - Patagonia 2015-01: 44 vs 44
- `noise_2012`:
  - Mato Grosso 0.028, Yakutia 0.068, Kalimantan 0.084: all within 0.005 of the earlier table.
  - **Córdoba is 0.152, not 0.145.** The table was measured before the both-sensor static mask;
    with the shipped mask, 2.9% instead of 1.0% of Córdoba's detections are industrial. The new
    number is the right one for the shipped data.
- `leftover_2012_range` contains `leftover_2012` for all 9 presets.

### Finding 1: the MODIS-only detections are real fires, not false alarms

| | MODIS only | Confirmed by VIIRS |
|---|---|---|
| **Punjab Nov 2016** (4,053 vs 5,004 detections) | | |
| Median MODIS confidence | 65 | 65 |
| Low confidence (<30) | 3.7% | 5.3% |
| Median FRP | 11.2 MW | 16.3 MW |
| Daytime | 100% | 100% |
| **Haryana Oct 2021** (334 vs 459 detections) | | |
| Median MODIS confidence | 63 | 65 |
| Low confidence (<30) | 1.5% | 1.5% |
| Median FRP | 8.9 MW | 10.6 MW |
| Seen by Terra's morning pass | 30% | 13% |

Same confidence, no more low-confidence detections, weaker fires: small, short crop burns that flare
between the satellites' passes. They are not noise.

### Finding 2: Punjab's burning moved out of VIIRS's view as MODIS's orbit drifted

Punjab, October–November daytime:

| Year | Aqua passes at (IST) | Aqua fires | Aqua fires with no VIIRS fire in the pixel that day | VIIRS fires (NOAA-20, ~13:45) |
|---|---|---|---|---|
| 2016 | 13:47 | 16,196 | 46% | 87,024 (S-NPP) |
| 2021 | 13:48 | 16,624 | 21% | 76,850 |
| 2024 | 14:37 | 3,900 | 71% | 10,235 |
| 2025 | 15:15 | 3,051 | 86% | 4,157 |
| 2026 | **16:00** | **5,365** | **95%** | **3,933** |

In 2026, MODIS passing at 4 pm sees **more** fires than VIIRS at 1:45 pm, although VIIRS normally sees
several times more. So much of today's burning happens after VIIRS's pass. The VIIRS-based record, and
India's official CREAMS counts, which also use VIIRS, partly show fires *moving* in time, not just
*fewer* fires.

**Suggested app text for Punjab and Haryana from 2024:** "Part of the drop is burning shifting to the
late afternoon, after VIIRS passes; MODIS, now passing at 4 pm, still sees it (`modis_only`)."

### Part B (model fixes): none shipped; what we measured

| # | Status | Numbers |
|---|---|---|
| B1 continent fallback | not shipped | A 20° middle level was tested in the 2012 search: held-out 4.25% (worse than 4.18%), Northern Europe still far off. Northern Europe's remaining +24 is real fires in a region with few fires (Sweden 2014, Ireland 2010–11) plus Icelandic lava edges; no fallback ratio fixes a change in *where* fires burned. |
| B2 industrial ratio near 1:1 | not adopted | The data says the opposite. In California VIIRS sees ~6,000 industrial detections a year and MODIS ~40, so a 1:1 ratio would undercount industrial heat ~100× before 2012. Industrial sites already have their own fit (2012–15, mass balance): region held-out error 23.4% → 21.0%, bias −17% → +2.7%. "Hide industrial" removing 25% of the count from 10% of rows is that ratio working, not a bug. |
| B3 smoothing toward the 8 neighbours | not tried today | Future work. The cell-level error 17.14% is ~40% counting noise (Poisson floor 7.3%). |
| B4 band coverage for state-sized areas | not measured today | Future work. Regions are 89.1% (target 85–95%). |
| B5 burned area for the weak regions | **measured** | See below |

**B5:** hotspots per km² burned (MODIS MCD64 burned area, an independent measurement), change from
2008–11 to 2012–15, in the GFED regions behind the three weak world regions:

| GFED region | MODIS | Raw merged | Harmonized | Gap | Change point in harmonized ratio | Yearly match with burned area, raw → harmonized |
|---|---|---|---|---|---|---|
| Boreal North America | −12.5% | +311% | −5.2% | +7.3 | none (p = 0.60) | 0.62 → 0.88 |
| Temperate North America | +13.3% | +274% | +13.0% | −0.3 | none (p = 0.46) | 0.53 → 0.85 |
| Europe | +1.7% | +241% | +5.2% | +3.5 | none (p = 1.0) | 0.02 → 0.93 |
| Boreal Asia | +9.6% | +343% | +17.9% | +8.3 | none (p = 0.77) | 0.44 → 0.82 |

The leftover sits in the boreal forests: +7 to +8 points, with no break at 2012 and a strong yearly
match with burned area. It's a steady offset, not a jump.

## 15 Nov: the 2012-jump work (reply to PROMPT_FIX_2012_JUMP.md)

Full write-up, diagnosis tables and every experiment: `docs/experiments_2012.md`.

**Result on our copy of your 27 checks** (`pipeline/check_2012.py`, live API): **20 → 22 pass**, no
regressions.
- **Now passing:** Patagonia (+8.0% → +1.1%) and California (+26.6% → +0.6%).
- **Better, still failing:** Northern Europe (+267 → +24 points). Western Europe is back within
  5 points, though the region check as a whole still fails.
- **Accuracy:** held-out error went from 4.18% to 4.03% (region × month) and from 17.6% to 17.1%
  (cell × month). Band coverage is 89%. Industrial sites: held-out error 23.4% → 21.0%, bias
  −17% → +3%.
- **Search:** a full grid of 166,404 setting combinations found no way to pass the other 5 without
  breaking held-out error, the held-back areas or Punjab's season months. Details in
  `docs/experiments_2012.md`.
- Please re-run `mock/evaluate_api.py`. Ours mirrors it but isn't it.

**What changed in the method:**
1. **One static mask for all sensors.** A 0.05° place where either MODIS or VIIRS types at least half
   its detections as volcano / static source / offshore is now static for **every** sensor and year.
   There is a 5 km margin around places VIIRS types static.
   - Why: VIIRS called the 2014 Holuhraun eruption a vegetation fire, and MODIS called it industrial.
   - Effect: worldwide, 7.6% of hotspots are now industrial/persistent, up from 6.7%. So the
     `industrial` share and the "hide industrial" filter cover a bit more (volcanic eruptions,
     flare fields).
   - It also helps 2026: near-real-time rows have no FIRMS type, and these places are now caught
     by location.
3. **Industrial sites get their own fit.** MODIS misses most small flares and plants, so each region
   keeps VIIRS's industrial total, fitted on 2012–15, with day and night sharing a ratio.
   - The `industrial` share of a pre-2012 month is now about right: before, it ran ~17% low.
   - `/api/proof` has a new `industrial_heldout` block with these numbers.
2. **The S-NPP → NOAA-20 factor is smoothed.** Same instrument on both satellites, so its
   place-to-place scatter was noise, and its own held-out error fell from 2.05% to 1.8%.

**What did not change:**
- No fields were added, removed or renamed, and no meanings changed.
- `/api/proof` keeps its layout, including the full-method row's name, "+ per 1° cell (shrunk) =
  FireLedger".

**Typical change in numbers:** for the Punjab request example, most months move by about 1–2%.
Volcanic regions move a lot, because their eruptions now count as industrial: Iceland 2014, Réunion.

**New optional fields** (safe to ignore):
- `summary.reliability`: `{region, leftover_2012, verdict, modis_fires, note}`, or `null` when the
  request doesn't cover 2008–2015, or when `harmonized` is false / `unit` is `modis`.
  - `leftover_2012` is the same metric as your preset check, worked out for the user's own area.
  - `verdict` is `good` (≤ 8%, inside the noise of MODIS's own change), `fair` (≤ 15%), `caution`, or
    `too few fires`.
  - `region` is the world region of the area.
  - Suggested use: a small "2012 switch" badge on the calendar, with `note` as its tooltip.
- `/api/proof` → `creams`: our counts vs **India's official crop-residue fire bulletins** (ICAR-IARI
  CREAMS) for Punjab and Haryana, 2022–2026.
  - Punjab paddy season: CREAMS 36,488 (2023) → 5,114 (2025); FireLedger 33,382 → 4,446.
  - The burning days line up: daily rank correlation 0.89.
  - Good for the Method & Trust page.

**About the 5 checks that still fail** (numbers in the write-up):
- At state scale, MODIS's own change is a rough yardstick. Across 323 5° blocks between 2012–15
  and 2016–19, when VIIRS's real change is known, MODIS's change landed within 5 points of it in
  only 59%.
- Terra and Aqua disagree by 20–42 points in several presets.
- Mato Grosso and Kalimantan leftovers come from real shifts in the fire mix: more day fires in
  Mato Grosso, more night peat fires in Kalimantan.
- We tried 11 fix families: month pooling, Aqua-only conversion, mass balancing, shorter windows
  and more. Each one that would pass one more check broke another or made the record less
  accurate in held-out years. Month pooling, for example, would bias Punjab's April by +71%.
- The 6 held-back areas: 5/6 within 5% (4/6 before). Saskatchewan improved +10.8% → −0.3%.

The real API now answers in `mock.json`'s shape. `docs/api-samples/api_as_mock.json` holds **real
answers** for `mock.json`'s `request_example` (Punjab box, 2001-01 → 2026-11), in the same
top-level layout. Drop it in place of `mock.json` to build against real numbers.

`python -I mock/validate_mock.py docs/api-samples/api_as_mock.json` → **0 errors**. The 312
warnings are all "months[i] has extra keys", one per month: the extra fields listed below.

## Public API (works without the team laptop)

**`https://dhruv-deku--fireledger-web.us-east.modal.run`** is the same API code on Modal, with docs
at `/docs`.

- **Presets:** match the laptop exactly (they come from the laptop's saved answers).
- **Drawn areas:** answered from the 0.25° grid, so small areas can differ by a few % from the
  laptop.
- **No CSV:** it answers 503; hide the download when `/api/health` says `"mode": "grid-only"`.
- **Speed:**
  - It sleeps after 20 idle minutes; the first request then takes ~10 s, so give the health check
    15 s.
  - After that, presets come back in ~1 s, a drawn area in ~5 s, and a 0.25° map frame in ~2–3 s
    from India (faster from the US/EU).
  - For the timeline animation, use the snapshot's 1° layer files (served by Vercel), and the API
    for the frame the user stops on.
- **On Vercel:** set `NEXT_PUBLIC_API_URL` to this URL. It's https, so there's no mixed-content
  problem. Keep snapshot mode as the fallback.

## Calling it

- **Speed:** answers are kept in memory until the API restarts, so anything asked before comes back
  in ~0.02 s.
  - The 9 presets are worked out in the background during the first ~5 minutes after a start.
  - A new drawn area takes ~3–8 s once.
  - A map frame (`/api/grid`) takes ~0.3 s.

- Base URL: `http://<laptop-ip>:8000` on the same Wi-Fi, or the https tunnel URL we send you
  (needed from a Vercel page: https can't call http).
- `POST /api/calendar`, `POST /api/summary`, `POST /api/hotspots.csv` take `request_example`'s body.
- `GET /api/presets` → `[{id, name, geometry}]`, one GeoJSON Polygon each. `punjab` and `patagonia`
  keep the mock's ids (now with real state borders; Patagonia = Neuquén + Río Negro). Also:
  `haryana`, `cordoba`, `mato-grosso`, `california`, `northern-territory`, `yakutia`,
  `central-kalimantan`.
- Bad body or geometry → **400** with `{"detail": ...}`. Unknown region → 404. CSV too big → 413.
- CORS allows any origin, including the preflight `OPTIONS`.

## Same as the contract

- `months`: ordered, continuous, starting at the request's `start`, ending at the **latest day
  with data** (Nov 13, 2026 now; months after that are not sent). `lo <= count <= hi`, all ≥ 0,
  `raw_viirs` is 0 before 2012.
- `flags` from `M C D B` only:
  - `M`: counted from MODIS (all of 2000–2011, plus VIIRS outages)
  - `C`: cross-calibrated VIIRS
  - `D`: MODIS orbit drift (MODIS-counted months from 2021 on, or every month in `unit: "modis"`)
  - `B`: blind spot. VIIRS would see at least 8× more fires than MODIS did, so the
    reconstruction leans hard on the conversion. **Show this badge.** Example: Punjab in 2001–02,
    when only Terra (10:30 overpass) was flying and farmers burn in the afternoon. Oct 2001 is
    47,267 fires from 726 MODIS detections, which is also why 2001 tops Punjab's `busiest_years`.
- `summary.this_month.count` = the calendar's count for that month.
- `busiest_months` (top 3 by median, complete months only, not the current one), `busiest_years`
  (top 3 full years, `total` = calendar sum), `season {start_month, peak_month, end_month}` (25% of
  peak rule), `critical_next` (next 6 months, `{month, median, note}`), and
  `trend {sen_slope_per_year, p_value}` all follow HANDOFF's definitions.
- `anomaly` is the robust z, (count − median) / (1.4826 × MAD).
- `days_covered` is sent: 13 now (FIRMS data runs to yesterday, UTC).
- The CSV header is exactly the contract's 11 columns. `weight` is 0 for rows that don't count
  (S-NPP on days NOAA-20 also observed). `type` is empty for near-real-time rows (FIRMS doesn't
  give it yet). `?full=true` adds brightness, pixel size, persistent flag, version, country and
  the weight's 90% band.

## One deliberate difference: the month in progress

When the current month is partial (`days_covered` < days in the month), `median`, `p25`, `p75`,
`pct_diff` and `anomaly` compare **Nov 1–13 of this year with Nov 1–13 of other years**, not with
whole Novembers. Comparing half a month with whole months makes every month look "below normal"
until its last day. Fields to label it:

- `compared_with`: e.g. "Nov 1–13 in other years"
- `same_days_past`: `[{year, count}]`, the numbers the median came from (plot these as the
  comparison, not the calendar's full Novembers)
- `full_month_median`: the median of the whole month, if you want to show it too

`validate_mock.py`'s rule 5 recomputes the median from full calendar months, so it can flag this
when the two differ by more than 5%. For the Punjab sample they're 18,234 vs 18,583, inside the
tolerance. Suggested fix: when `days_covered` is less than the month length, check `median`
against `same_days_past` instead.

## Extra fields (safe to ignore)

- **Each month:**
  - `raw_snpp`, `raw_noaa20`
  - `industrial`: the part of `count` from gas flares, plants and volcanoes
  - `source`: `VIIRS`, `S-NPP→VIIRS`, `MODIS→VIIRS`, or `MODIS (raw)` / `VIIRS (raw)` when `harmonized: false`
  - `drift`, `partial`, `through_day`
  - `preliminary`: near-real-time data, from Aug 2026 on; NASA finalises it about 3 months later
- **`this_month`:** `label`, `lo`, `hi`, `flags`, `anomaly_label` (text), `percentile`, `rank`,
  `of_years`, `record`, `preliminary`
- **Summary:**
  - `industrial {share, warning}`: show the warning when it isn't null
  - `season_timing`: start, peak and end of the season, a second season, and the shift per decade
  - `climatology`, `annual`

## The before/after switch

`harmonized: false` returns the record as it stands: raw MODIS before VIIRS existed, raw VIIRS
after. For Punjab, October goes from 5,427 (2011) to 13,580 (2012), which is the fake jump. Same
shape, `lo = hi = count`. The summary works on the raw numbers too.

## Also available

- `GET /api/proof`: the validation numbers for the Method & Trust page. `burned_area` is the
  independent check against MODIS burned area (World: raw +344% vs harmonized +4% in 2012).
- `GET /api/grid?year=2025&month=11&res=0.5`: map layer, `[lat, lon, count]` per cell.
- `GET /api/health`: data coverage, the month-to-date window, and where preliminary data starts.
