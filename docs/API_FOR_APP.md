# API ↔ app: reply to HANDOFF.md

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
