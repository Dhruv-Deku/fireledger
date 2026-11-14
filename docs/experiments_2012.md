# The leftover 2012 jump: diagnosis, experiments, what shipped

Work done 15 Nov 2026 (01:00–06:00) for `PROMPT_FIX_2012_JUMP.md`. Every number here comes from
code in this repo: `pipeline/check_2012.py` (our copy of the app's checks, run on the live API),
`pipeline/jump2012.py` (what-if runs of the factor table, the same weights as `api/weights.py`) and
`pipeline/validate.py` (the proof the API serves).

## Result

| | Before (Nov 14) | After (shipped) |
|---|---|---|
| Checks passing (our copy of `mock/evaluate_api.py`, live API, with CSV) | 20/27 | **21/27** |
| Regions within 5 points (vegetation) | 16/20 | **17/20** |
| Mean \|gap\| over the 20 regions | 17.7 points | **3.8 points** |
| Mean \|leftover\| over the 9 presets | 7.6% | **6.5%** |
| Held-out error, region × month | 4.18% | **4.03%** |
| Held-out error, 1° cell × month | 17.59% | **17.14%** |
| Bias, cell × month | +0.32% | +0.38% |
| 90% band coverage, region × month | 89.0% | **89.1%** |
| S-NPP → NOAA-20 held-out error, region × month | 2.05% | **1.81%** |
| 6 held-back areas within 5% | 4/6 | **5/6** |
| Mean \|leftover\| over the 6 held-back areas | 3.7% | **2.2%** |

Nothing that passed before fails now. The new pass is Patagonia (+8.0% → +2.2%). Western Europe
(−37.1) is now inside 5 points and Northern Europe went from +267 to +24. California went from
+26.6% to +17.2%, but still fails.

**Still failing (6 checks):**

| Check | Now |
|---|---|
| World-region check | Northern Europe +23.9, Northern America +8.9, Eastern Europe +6.3 |
| Córdoba | +8.3% |
| Mato Grosso | −5.7% |
| California | +17.2% |
| Yakutia | +7.9% |
| Central Kalimantan | +12.2% |

The reasons are measured below. For most of them the harmonized record follows a real change in
where, when and what kind of fires burned. MODIS's own change is too rough a yardstick at state
scale to show it (see "About the checks").

## Shipped settings (`pipeline/harmonize_config.json`)

```json
{"static_mask": "persistent+typed_vbuffer", "n0_factor": {"k_s": [30000, 30000]}}
```

- **F6, one static mask from both sensors.** FIRMS's own `type` disagrees between sensors.
  - At Holuhraun (Iceland, 2014), S-NPP typed 49,791 of 49,958 detections as vegetation, while
    MODIS typed 3,995 of 4,028 (Aqua) as "static land source".
  - At Piton de la Fournaise (Réunion, counted as France, so Western Europe), MODIS typed the 2010
    eruption as vegetation and later eruptions as volcano.

  The fix: a 0.05° place where either sensor family types at least half its detections as 1–3
  (≥5 detections) is static for every sensor and every year. A one-cell margin is added around
  places VIIRS types static, because MODIS's 1–5 km pixels scatter detections of the same flare.
  `pipeline/flags.py` writes `data/derived/typed_static.parquet` and `api/weights.py` adds it to
  the persistent mask.
  - Worldwide, 7.6% of hotspots are now static, up from 6.7%.
  - On its own it cut held-out error from 4.18% to 4.05%, because vegetation factors no longer
    learn from lava and flares.
- **F8 for the S-NPP → NOAA-20 factor.** Both satellites carry the same VIIRS instrument, but the
  cell-level `k_s` ranged from 0.78 to 1.30 (5–95%).
  - That spread was mostly noise, and it leaked into the 2012 step through the mix of places and
    months (removing it moves Patagonia from +8.0% to +2.8%).
  - Pooling it almost to the month × day/night value cuts its own held-out error from 2.05% to 1.81%.
  - The strength (30,000) was the best of 300 / 3,000 / 30,000 / global by that error.

### Choices the checks can't see

**Margin.** A margin around **every** typed place scored marginally better (held-out 4.00% vs
4.03%; California +16 vs +17), with the same checks passing. But it moved 7.1% of Mato Grosso's
fires into "industrial" instead of 2.8%.
- Those places are typed static by MODIS only. VIIRS types none of their 1,000–1,800 detections as
  static; they are probably grain dryers next to farmland.
- A 5 km margin there would hide real fires behind "hide industrial", so we ship the VIIRS-only margin.

**Month pooling (F10) was tried and dropped.**
- Borrowing 10% from neighbouring calendar months lowered the world held-out error (4.03% → 3.95%),
  held all 6 held-back areas within 5%, and at 0.25 would even pass Mato Grosso (−4.99%).
- But in held-out years it biased the season-start months, where a quiet month sits next to a busy
  one, because the busy month's counts swamp the quiet month's ratio:

  | Held-out month | Predicted ÷ actual, no pooling | With pooling |
  |---|---|---|
  | Punjab April | 1.25 | 1.71 |
  | Punjab September | 0.99 | 0.61 |
  | Haryana September | 1.08 | 0.73 |

- Season start and critical months are what the app shows, so we turned it off.
- Blending neighbouring *ratios* instead of counts was worse everywhere (held-out 4.27–6.24%,
  Haryana fails).

## Step 1: diagnosis

Fires per preset, 2008–11 vs 2012–15, with the Nov 14 factors.

The leftover splits exactly into two factors: **calibration** (VIIRS ÷ factor prediction from MODIS
in 2012–15) times **composition** (the factor the MODIS fires got in 2012–15 ÷ the factor they got
in 2008–11). Composition is not 1 when fires shift between places, months or day/night with
different conversion factors.

| Preset | Leftover | = calibration | × composition | D1 VIIRS in MODIS-zero cell-months | Industrial share |
|---|---|---|---|---|---|
| Punjab | +0.6% | 0.977 | 1.029 | 0.3% | 0.1% |
| Haryana | −1.5% | 0.986 | 0.997 | 2.6% | 3.3% |
| Patagonia | +8.0% | 1.134 | 0.923 | 27.4% | 14.3% |
| Córdoba | +6.9% | 1.073 | 0.974 | 8.9% | 0.3% |
| Mato Grosso | −5.8% | 1.002 | 0.939 | 1.0% | 2.1% |
| California | +26.6% | 1.145 | 1.102 | 12.6% | 11.3% |
| N. Territory | +1.6% | 0.990 | 1.017 | 0.5% | 0.1% |
| Yakutia | +7.6% | 1.009 | 1.066 | 0.6% | 0.8% |
| C. Kalimantan | +10.1% | 1.019 | 1.080 | 0.5% | 0.6% |

**D1, zero cells.** Large only in Patagonia, California and Córdoba.
- The factor is a ratio of sums over all overlap years, so VIIRS fires in a year when MODIS saw none
  are already inside each cell's factor. Mass leaks only across cells, through shrinkage.
- A mass-conserving fix (F1: rescale every 5° block × month × day/night to its VIIRS total) made
  held-out error worse (4.38–4.62%) and broke Haryana (−6.8%).
- So the zeros are mostly year-to-year noise, not a bias a factor can carry.

**D2, ratio drift.** Calibration (VIIRS ÷ prediction) by year, 2012–19:

| Preset | 2012 → 2019 |
|---|---|
| California | 1.12, 1.13, 1.18, 1.15, 1.20, 1.05, 1.04, 1.17 |
| Patagonia | 0.99, 1.24, 1.27, 1.09, 1.10, 1.04, 1.31, 1.33 |
| C. Kalimantan | 1.05, 0.94, 0.99, 1.03, 1.12, 1.23, 0.88, 0.95 |
| Mato Grosso | 1.02, 0.99, 1.01, 0.99, 0.98, 1.00, 1.01, 0.98 |

California is flat (no drift; a constant shortfall from industrial sites, D4), Kalimantan is noisy
and Mato Grosso is flat. Fitting on 2012–15 only (F4) worsened held-out error (4.37–4.53%) and broke
Patagonia. No consistent drift showed up.

**D3, matched days vs all days.** The S-NPP ÷ MODIS ratio over all days in 2012–15, versus only days
all three satellites observed:
- Patagonia −4.7%, Córdoba −2.4%, all others within ±1%.
- Calibrating on all days (F5) pulled in outage days and made the S-NPP → NOAA-20 held-out error
  much worse (5.68% vs 1.81%).

**D4, industrial and persistent sources.**
- **Europe:** the volcano mistyping above. With F6, Western Europe goes from −37 to within 5 and
  Northern Europe from +267 to +24. What's left in Northern Europe is a few real fires in a region
  with ~2,000 MODIS fires a year: Västmanland (Sweden) 2014, the 2010–11 gorse fires in Ireland, and
  the Eyjafjallajökull and Holuhraun edges.
- **California:** industrial rows are 9% of detections, but their own leftover is +421%. VIIRS sees
  ~6,000 detections a year at oil fields and plants (FIRMS type 2 from VIIRS), MODIS 30–50. The
  factor can only rebuild what MODIS saw, so those sites can't be back-cast before 2012.
  Vegetation fires alone: +12.7%.

**D5, shrinkage.**
- Prior strength n0 = 10 / 30 / 100 / 200 for the cell → block step: held-out error is best at 100
  (3.96% with F6 + F8 k_s), but that breaks Patagonia (+5.7%), and 10 breaks it the other way (−6.3%).
- Kept at 30 for the MODIS factors.
- A 20° middle level (F8 hierarchy) was worse everywhere (4.25%).

**D6, day vs night and Terra vs Aqua.**

| Preset | Leftover, day only | Leftover, night only | Terra's own change | Aqua's own change |
|---|---|---|---|---|
| California | +8.3% | +34.0% | | |
| Yakutia | +16.8% | −7.5% | +104% | +94% |
| Córdoba | +9.5% | −9.1% | −29% | −43% |
| Punjab | | | −12% | +6% |
| Haryana | | | −18% | +20% |
| C. Kalimantan | | | +140% | +98% |

- In Kalimantan, the night share of MODIS fires rose from 29% to 38% (the 2015 peat fires).
  VIIRS's night factor is higher, so the VIIRS-equivalent count should rise more than MODIS's raw count.
- Converting from Aqua alone (F11: same 13:30 / 01:30 overpass as S-NPP) gave held-out 4.88% and
  broke Punjab, Haryana and Patagonia.

**D7, the S-NPP → NOAA-20 step.** The chain is identical on both sides of 2012 (MODIS → S-NPP →
NOAA-20 before it, S-NPP → NOAA-20 after it, with the same `k_s` per cell × month × day/night).
But `k_s` varied by place, so mix shifts leaked through. Setting `k_s` = 1 moves Patagonia from +8.0
to +2.8 and California from +26.6 to +21.9. That led to the F8 pooling of `k_s` above.

## Steps 2–3: experiments

Each fix is a setting in `pipeline/calibrate.py` (`DEFAULT_CFG`) or `pipeline/flags.py`.

| Flag | What it is here | Verdict |
|---|---|---|
| F1 `mass` | rescale each block × month × day/night to its VIIRS total (zero-aware, learned) | ✗ held-out 4.38–4.62%, breaks Haryana |
| F2 FRP-aware | MODIS weight × exp(slope · FRP and pixel-size anomaly) | ✗ moves every preset < 1 point; would need a per-month join table in the API |
| F3 quantile mapping | per block × day/night | ✗ not expressible as a per-hotspot weight (CSV sums would break); proof shows region jump gap worse (3.9 vs 2.8) |
| F4 `fit_years` | MODIS factors on 2012–15 only | ✗ held-out 4.37–4.53%, breaks Patagonia |
| F5 `matched: false` | fit on all days | ✗ S-NPP → NOAA-20 held-out 5.68%, no new pass |
| F6 `static_mask` | static places from both sensors (+ margins) | ✓ shipped (VIIRS-only margin) |
| F7 `industrial: false` | no separate industrial factors | ✗ Patagonia +14.6, California +29.2 |
| F8 `n0`, `mid`, `n0_factor` | shrinkage strength, 20° level, per-factor strength | ✓ shipped for `k_s` only; others ✗ |
| F9 strata | ratios by fire type | ✗ no land cover offline (needs Earthdata); FRP part covered by F2 |
| F10 `month_pool` | neighbouring calendar months (by counts, or by ratios) | ✗ dropped: better world held-out, but biases Punjab/Haryana season-start months (April ×1.71, Sept ×0.61); ratio version worse |
| F11 Terra/Aqua split | Aqua-anchored conversion | ✗ held-out 4.88%, breaks 3 presets |

How we searched:
1. Each fix alone.
2. Greedy forward from the best: keep a fix only if it passes more checks with zero regressions, or
   the same number with lower mean |leftover|.
3. Factorial over the top 3: F8 `k_s`, F6, F10.
4. Order check on where pooling applies.

Strengths (n0, pooling) were picked by held-out error, never by the jump test.

The 2008–15 preset numbers in this table come from `jump2012.py`'s area extracts. They match the
live API to within 0.1 point (checked on the Nov 14 and the shipped factors). **Bold** = within ±5%.

| Run | Flags | Pass /21 | Regions ≤5 | Regions off | Calif. | C. Kal. | Patag. | Yakutia | Córdoba | Mato G. | Punjab | Haryana | N. Terr. | Held-out region×month | cell×month | Band cov. | S-NPP→N20 held-out |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline | - | 14 | 16/20 | E. Europe +6, N. America +9, W. Europe -37, N. Europe +267 | +26.6 | +10.1 | +8.0 | +7.6 | +6.9 | -5.8 | **+0.5** | **-1.5** | **+1.6** | 4.18% | 17.59% | 89.0% | 2.05% |
| F6 typed static mask | F6 | 14 | 17/20 | E. Europe +6, N. America +9, N. Europe +24 | +26.2 | +10.3 | +7.3 | +7.7 | +6.9 | -5.6 | **+0.6** | **-1.6** | **+1.7** | 4.05% | 17.29% | 89.5% | – |
| F1 mass (n0 300) | F1 | 14 | 15/20 | S. Africa -6, N. Africa -7, C. Asia -5, W. Europe -70, N. Europe +245 | +20.5 | +9.5 | -8.8 | +6.9 | **+4.1** | -6.0 | **+0.3** | -6.8 | **+1.0** | 4.47% | 18.20% | 88.7% | – |
| F1 mass (n0 100) | F1 | 14 | 14/20 | S. Africa -6, N. Africa -9, C. Asia -5, W. Asia -5, W. Europe -77, N. Europe +227 | +19.5 | +9.6 | -13.6 | +6.7 | **+3.7** | -5.9 | **+0.3** | -7.3 | **+0.9** | 4.62% | 18.43% | 88.6% | – |
| F1 mass (n0 1000) | F1 | 15 | 16/20 | S. Africa -6, N. Africa -5, W. Europe -57, N. Europe +255 | +21.6 | +9.4 | **-4.4** | +6.9 | **+4.7** | -6.1 | **+0.3** | -6.2 | **+1.0** | 4.38% | 18.02% | 89.0% | – |
| F4 fit 2012-15 | F4 | 15 | 16/20 | N. America +14, C. Asia -7, W. Europe -26, N. Europe +253 | +26.3 | +8.6 | +13.1 | **+2.8** | +6.1 | -6.4 | **+1.1** | **-1.4** | **+0.9** | 4.53% | 18.40% | 88.8% | – |
| F5 all days | F5 | 16 | 16/20 | N. America +15, S. Africa -5, W. Europe -37, N. Europe +273 | +18.6 | +10.2 | +7.7 | **+3.5** | **+4.7** | -5.9 | **+0.8** | **-1.0** | **+0.8** | 4.72% | 17.80% | 89.2% | – |
| F7 industrial off | F7off | 14 | 16/20 | E. Europe +6, N. America +9, W. Europe -37, N. Europe +267 | +29.2 | +8.8 | +14.6 | +7.7 | +6.9 | -6.0 | **+0.6** | **-4.9** | **+1.7** | 4.18% | 17.59% | 89.0% | – |
| F8 mid 20deg | F8 | 13 | 15/20 | E. Europe +6, N. America +8, S. Africa -5, W. Europe -39, N. Europe +202 | +26.2 | +10.6 | +8.7 | +9.0 | +6.5 | -5.6 | **+0.5** | -5.2 | **+1.6** | 4.25% | 17.78% | 88.6% | 2.05% |
| F8 n0 10 | F8 | 15 | 16/20 | N. America +8, S. Africa -5, W. Europe -52, N. Europe +259 | +25.4 | +9.9 | **-1.7** | +6.8 | +6.3 | -5.9 | **+0.6** | **-1.3** | **+1.1** | 4.33% | 18.07% | 88.7% | 2.18% |
| F8 n0 100 | F8 | 14 | 16/20 | E. Europe +6, N. America +10, W. Europe -28, N. Europe +268 | +26.4 | +10.1 | +10.9 | +7.5 | +6.9 | -5.6 | **+0.5** | **-1.9** | **+1.9** | 4.12% | 17.43% | 88.8% | 1.97% |
| F10 month pool 0.5 | F10 | 14 | 16/20 | E. Europe +6, N. America +11, W. Europe -34, N. Europe +271 | +25.6 | +10.8 | **-1.1** | +7.7 | +11.3 | -5.3 | **-1.8** | -5.2 | **+1.8** | 4.61% | 17.40% | 88.9% | 1.84% |
| F8 k_s smooth 300 | F8ks | 15 | 16/20 | E. Europe +5, N. America +10, W. Europe -38, N. Europe +233 | +25.1 | +10.5 | **+4.1** | +8.2 | +7.7 | -5.8 | **+0.6** | **-1.8** | **+1.5** | 4.18% | 17.59% | 89.0% | 1.90% |
| F8 k_s smooth 3000 | F8ks | 15 | 16/20 | E. Europe +6, N. America +10, W. Europe -38, N. Europe +224 | +23.8 | +11.0 | **+3.1** | +8.1 | +7.7 | -5.8 | **+0.7** | **-1.9** | **+1.5** | 4.18% | 17.59% | 89.0% | 1.84% |
| k_s smooth 30000 | F8ks | 15 | 16/20 | E. Europe +5, N. America +10, W. Europe -38, N. Europe +224 | +22.9 | +12.0 | **+3.1** | +7.8 | +8.5 | -5.8 | **+0.9** | **-2.0** | **+1.5** | 4.18% | 17.59% | 89.0% | 1.81% |
| k_s global only | F8ks | 15 | 15/20 | E. Europe +5, N. America +10, W. Europe -38, N. Europe +224 | +22.1 | +12.7 | **+3.0** | +7.4 | +9.0 | -5.8 | **+1.4** | **-2.2** | **+1.4** | 4.18% | 17.59% | 89.0% | 1.83% |
| F6 typed | F6 | 14 | 17/20 | E. Europe +6, N. America +9, N. Europe +24 | +26.2 | +10.3 | +7.3 | +7.7 | +6.9 | -5.6 | **+0.6** | **-1.6** | **+1.7** | 4.05% | 17.29% | 89.5% | 2.04% |
| F6 typed + buffer | F6b | 14 | 17/20 | E. Europe +7, N. America +8, N. Europe +15 | +19.0 | +10.4 | +6.7 | +7.8 | +6.8 | -5.5 | **+0.6** | **-1.6** | **+1.7** | 4.00% | 17.06% | 88.7% | 2.03% |
| F6 typed share 0.3 | F6c | 14 | 17/20 | E. Europe +7, N. America +9, N. Europe +23 | +25.5 | +10.1 | +6.5 | +7.8 | +6.5 | -5.5 | **+0.6** | **-1.7** | **+1.7** | 4.01% | 17.16% | 89.0% | 2.04% |
| F6 + n0 100 + k_s 3000 | F6,F8,F8ks | 14 | 17/20 | E. Europe +6, N. America +10, N. Europe +23 | +24.1 | +10.9 | +7.1 | +7.6 | +6.9 | -5.5 | **+0.6** | **-2.3** | **+1.8** | 4.01% | 17.15% | 89.0% | 1.83% |
| F6 + n0 200 + k_s 3000 | F6,F8,F8ks | 14 | 16/20 | E. Europe +6, N. America +9, S. Africa -5, N. Europe +23 | +23.9 | +10.7 | +8.1 | +7.3 | +6.7 | -5.4 | **+0.5** | **-2.5** | **+1.9** | 4.01% | 17.23% | 89.1% | 1.83% |
| G1 k_s + F6buf | F8ks,F6b | 15 | 17/20 | E. Europe +7, N. America +9, N. Europe +15 | +16.1 | +12.1 | **+2.0** | +7.9 | +8.4 | -5.5 | **+0.9** | **-2.2** | **+1.5** | 4.00% | 17.06% | 88.7% | 1.81% |
| G2 G1 + n0 100 | F8ks,F6b,F8 | 14 | 17/20 | E. Europe +7, N. America +9, N. Europe +15 | +17.6 | +12.1 | +5.7 | +7.3 | +7.6 | -5.4 | **+0.8** | **-2.5** | **+1.8** | 3.96% | 16.92% | 89.2% | 1.81% |
| G3 G1 + F1 mass 1000 | F8ks,F6b,F1 | 13 | 18/20 | S. Africa -6, N. Europe +12 | +9.3 | +10.2 | -10.6 | +7.7 | +5.5 | -6.1 | **+0.6** | -7.1 | **+0.8** | 4.18% | 17.41% | 88.7% | 5.81% |
| G4 G1 + F10 pool 0.25 | F8ks,F6b,F10 | 16 | 16/20 | E. Europe +7, N. America +10, S. Europe +5, N. Europe +14 | +15.9 | +11.9 | **-0.5** | +8.2 | +10.7 | **-5.0** | **-1.2** | **-4.7** | **+2.0** | 4.16% | 16.67% | 89.2% | 1.75% |
| G5 G1 + F4 2012-15 | F8ks,F6b,F4 | 15 | 17/20 | N. America +13, C. Asia -6, N. Europe +11 | +13.1 | +11.2 | +7.2 | **+2.9** | +7.5 | -6.3 | **+1.6** | **-2.1** | **+0.9** | 4.37% | 17.91% | 89.1% | 1.81% |
| G6 G1 + F5 all days | F8ks,F6b,F5 | 15 | 17/20 | E. Europe +6, N. America +11, N. Europe +15 | +12.8 | +11.8 | **+1.2** | +6.9 | +6.6 | -5.5 | **+1.1** | **-1.5** | **+0.8** | 4.54% | 17.27% | 89.3% | 5.68% |
| G7 G1 + F8 n0 10 | F8ks,F6b,F8 | 14 | 16/20 | E. Europe +5, N. America +8, S. Africa -5, N. Europe +14 | +10.8 | +11.7 | -6.3 | +8.1 | +8.9 | -5.6 | **+0.9** | **-2.1** | **+0.8** | 4.12% | 17.48% | 88.5% | 1.81% |
| G1 + pool 0.10 | F8ks,F6b,F10 | 15 | 17/20 | E. Europe +7, N. America +10, N. Europe +15 | +16.4 | +12.2 | **+1.4** | +8.5 | +9.9 | -5.2 | **-0.4** | **-3.6** | **+1.8** | 3.95% | 16.69% | 89.2% | 1.78% |
| G1 + pool 0.15 | F8ks,F6b,F10 | 15 | 16/20 | E. Europe +7, N. America +10, S. Europe +5, N. Europe +15 | +16.3 | +12.1 | **+0.8** | +8.4 | +10.2 | -5.1 | **-0.7** | **-4.0** | **+1.9** | 4.01% | 16.65% | 89.3% | 1.77% |
| G1 + pool 0.35 | F8ks,F6b,F10 | 15 | 16/20 | E. Europe +7, N. America +9, S. Europe +5, N. Europe +14 | +15.4 | +11.6 | **-1.8** | +8.0 | +10.9 | **-4.9** | **-1.5** | -5.2 | **+2.0** | 4.29% | 16.73% | 89.1% | 1.74% |
| FF F10 0.25 alone | F10 | 15 | 16/20 | E. Europe +6, N. America +11, W. Europe -35, N. Europe +274 | +26.3 | +11.0 | **+2.9** | +8.1 | +10.8 | -5.4 | **-1.3** | **-4.2** | **+1.9** | 4.33% | 17.19% | 89.3% | 1.87% |
| FF k_s + F10 0.25 | F8ks,F10 | 15 | 15/20 | E. Europe +6, N. America +11, S. Europe +5, W. Europe -35, N. Europe +210 | +23.6 | +12.0 | **+1.2** | +8.1 | +10.7 | -5.3 | **-1.2** | **-4.5** | **+2.0** | 4.33% | 17.19% | 89.3% | 1.76% |
| FF F6b + F10 0.25 | F6b,F10 | 15 | 17/20 | E. Europe +8, N. America +10, N. Europe +14 | +17.8 | +11.0 | **+1.0** | +8.2 | +10.7 | -5.0 | **-1.3** | **-4.3** | **+1.9** | 4.16% | 16.67% | 89.2% | 1.85% |
| order: pool cell only | F8ks,F6b,F10c | 15 | 17/20 | E. Europe +7, N. America +9, N. Europe +15 | +15.6 | +11.9 | **+1.5** | +7.6 | +9.3 | -5.2 | **-0.4** | **-3.3** | **+1.8** | 3.93% | 16.73% | 89.1% | 1.80% |
| order: pool block only | F8ks,F6b,F10b | 15 | 17/20 | E. Europe +7, N. America +9, N. Europe +15 | +17.5 | +12.4 | **+2.1** | +8.8 | +9.2 | -5.4 | **+0.9** | **-2.5** | **+1.5** | 3.97% | 16.98% | 88.9% | 1.79% |
| FINAL buffer all | F6x,F8ks,F10c | 15 | 17/20 | E. Europe +7, N. America +9, N. Europe +15 | +15.6 | +11.9 | **+1.5** | +7.6 | +9.3 | -5.2 | **-0.4** | **-3.3** | **+1.8** | 3.93% | 16.73% | 89.1% | 1.80% |
| FINAL margin VIIRS-typed only | F6x,F8ks,F10c | 15 | 17/20 | E. Europe +7, N. America +9, N. Europe +24 | +16.7 | +12.0 | **+1.7** | +7.6 | +9.3 | -5.4 | **-0.4** | **-3.3** | **+1.8** | 3.95% | 16.80% | 89.1% | 1.80% |
| FINAL no margin | F6x,F8ks,F10c | 15 | 17/20 | E. Europe +6, N. America +10, N. Europe +24 | +22.2 | +12.0 | **+1.8** | +7.6 | +9.3 | -5.4 | **-0.4** | **-3.2** | **+1.8** | 3.98% | 16.95% | 89.1% | 1.80% |
| FINAL2 F6 vbuffer + k_s (no pooling) ← shipped | F6v,F8ks | 15 | 17/20 | E. Europe +6, N. America +9, N. Europe +24 | +17.2 | +12.2 | **+2.2** | +7.9 | +8.3 | -5.7 | **+0.9** | **-2.2** | **+1.5** | 4.03% | 17.14% | 89.1% | 1.81% |
| FINAL3 ratio-pool 0.1 cell | F6v,F8ks,F10r | 14 | 15/20 | South-E. Asia -5, N. America +8, S. Africa -5, C. Asia -5, N. Europe +23 | +15.5 | +10.2 | **+0.0** | +7.6 | +7.7 | -5.9 | **-2.3** | -8.0 | **+0.9** | 4.27% | 17.23% | 89.0% | 1.80% |
| FINAL3 ratio-pool 0.25 cell | F6v,F8ks,F10r | 12 | 12/20 | E. Africa -5, W. Africa -6, South-E. Asia -7, N. America +6, S. Asia -6, S. Africa -6, C. Asia -7, N. Europe +21 | +13.4 | +8.1 | **-3.0** | +7.3 | +7.0 | -6.3 | -5.4 | -13.5 | **+0.4** | 5.00% | 17.82% | 88.7% | 1.78% |
| FINAL3 ratio-pool 0.5 cell | F6v,F8ks,F10r | 11 | 10/20 | Middle Africa -6, E. Africa -7, W. Africa -8, South-E. Asia -9, S. Asia -9, S. Africa -7, N. Africa -5, C. Asia -9, W. Asia -7, N. Europe +20 | +10.9 | +5.8 | -7.4 | +6.7 | +6.0 | -6.8 | -8.5 | -18.5 | **-0.1** | 6.24% | 18.99% | 89.0% | 1.76% |

Flag key: F6 = typed mask (b = margin everywhere, v/x = margin around VIIRS-typed places, c = share 0.3), F8ks = S-NPP→NOAA-20 pooling, F8 = cell shrinkage n0 / 20° level, F10 = month pooling (r = by ratio, c = cell level only, b = block only).

## The 6 held-back areas

Chosen before tuning, never scored until the config was frozen.

| Area (continent) | Before | After |
|---|---|---|
| Zambia (Africa) | −0.4% | −0.5% |
| Ukraine (Europe) | −1.5% | −2.6% |
| Madhya Pradesh (South Asia) | −6.4% | −6.5% |
| Saskatchewan (North America) | +10.8% | +0.0% |
| Paraguay (South America) | −2.1% | −2.0% |
| Queensland (Oceania) | −1.1% | −1.7% |
| Within ±5% | 4/6 | **5/6** |
| Mean \|leftover\| | 3.7 | **2.2** |
| Worst | 10.8 | **6.5** |

"After" is the live API with the shipped settings. Saskatchewan's +10.8% was flare fields (the
static mask fixes it). Ukraine, Madhya Pradesh and Queensland moved by 0.1–1.1 points the other way,
which is inside the noise floor measured below. With month pooling, all six were within 5% (mean
1.7), but pooling was dropped for the season-start bias above.

## About the checks (numbers, not edits)

We didn't change `mock/evaluate_api.py`. Three measurements suggest the ±5% preset target sits at
or below the noise in its own yardstick.

1. **MODIS's own change is a noisy yardstick at state scale, even with VIIRS flying.**
   - We took the 323 5° blocks with ≥5,000 MODIS fires in each window and compared 2012–15 with
     2016–19, where VIIRS's real change is known.
   - MODIS's change was within 5 points of VIIRS's in only **59%** of blocks (median miss 4.1 points,
     75th percentile 8.0).
   - Our held-out prediction did about the same: 56% within 5, median 4.3.
   - So even a perfect harmonization would fail a ±5% MODIS-based check in about 4 of 10 areas.
2. **Terra and Aqua disagree with each other** by 20–42 points across 2012 in Haryana, Córdoba and
   Kalimantan (table D6). "MODIS's own change" depends on which MODIS you ask.
3. **Some leftovers are a real change in the fire mix.**
   - Mato Grosso's calibration is perfect (1.002); its whole −5.8% is composition.
   - In the overlap years the model's change tracked VIIRS's real change better than MODIS's did
     (VIIRS +2.9%, MODIS +5.6%, model +3.5%).
   - In Kalimantan, the shift to night fires (29% → 38%) should raise a VIIRS-equivalent count
     more than MODIS's.

Suggestion: score the preset 2012 check against the noise floor. For example, pass when
|leftover| ≤ max(5%, the area's own in-overlap MODIS-vs-VIIRS change error), or report it as a
trend rather than a gate. The region check (vegetation, large areas) is a fair gate, and it now
fails only on Northern Europe (tiny), Northern America and Eastern Europe (both composition:
Alaska 2009 vs NWT 2014; Moscow peat 2010 vs Yakutia 2012–14).

## Reproduce

```powershell
python pipeline/jump2012.py extract; python pipeline/jump2012.py areas    # ~2 min
# edit pipeline/harmonize_config.json, then:
python pipeline/flags.py --typed-only; python pipeline/calibrate.py; python pipeline/build_grid.py
python pipeline/build_grid.py --mtd; python pipeline/validate.py; python pipeline/burned_area.py
python pipeline/check_2012.py --csv --out data/eval/eval_final.json       # API running
```
