# Note for the app person: the 2012 preset check

**Where we are:** 22 of your 27 checks pass on the live API (it was 20). Nothing regressed. The API
is frozen at git tag `api-freeze-2026-11-15`.

| Still failing | Leftover | Same check run inside the overlap years* |
|---|---|---|
| World-region check | N. Europe +23.9, N. America +8.9, E. Europe +6.3 | |
| Córdoba | +8.0% | MODIS misses VIIRS's real change by 14.5 points |
| Mato Grosso | −5.7% | 2.6 points |
| Yakutia | +8.0% | 6.7 points |
| Central Kalimantan | +12.4% | 8.4 points |

\*Between 2012–15 and 2016–19 both satellites flew, so VIIRS's real change is known. This column is
how far MODIS's own change lands from it in that area. It's the noise in the check's own yardstick.

## Why we stopped here

We searched 166,404 combinations of fitting settings. Every one that passes more of these also does
at least one of the following:
- makes the 6 areas held back from tuning worse (your rule 6);
- bends Punjab's and Haryana's season months (held-out April up to 2× too high);
- pushes held-out error past your 5% limit.

Across 323 areas of this size, MODIS's own change is within 5 points of VIIRS's real change only
59% of the time. So a ±5% gate fails roughly 4 in 10 areas even for a perfect harmonization.
Details: `docs/experiments_2012.md`.

## Proposal: pick one (your call, it's your test)

1. **±8% instead of ±5%.** 8 points is the 75th percentile of that yardstick noise.
   - Mato Grosso passes (−5.7%).
   - Córdoba (+8.04%) and Yakutia (+7.96%) sit on the line.
   - Kalimantan still fails.
2. **Each area against its own noise.** Pass when |leftover| ≤ max(5%, that area's in-overlap miss
   from the table).
   - Córdoba passes (14.5).
   - Yakutia (6.7), Kalimantan (8.4) and Mato Grosso (5) don't.
3. **Report it instead of gating it.** `summary.reliability` already does this for any area the user
   draws:
   - `leftover_2012` is the same metric as your check;
   - `verdict` is good (≤ 8%), fair (≤ 15%) or caution;
   - `note` is a one-line explanation.

   Today: Punjab, Haryana, Patagonia, California, Mato Grosso, N. Territory and Yakutia are *good*;
   Córdoba and Kalimantan are *fair*. A small "2012 switch" badge on the calendar shows judges the
   limits honestly.

We'd suggest **3**, plus keeping ±5% for the world regions (large areas, where the test is fair).

## Please send

`mock/evaluate_api.py`, so we can confirm the 22/27 with your script, not our copy of it.
