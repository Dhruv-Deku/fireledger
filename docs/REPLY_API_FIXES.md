# Reply to the API fix list (15 Nov, 15:00)

All live on the public API (`https://dhruv-deku--fireledger-web.us-east.modal.run`) and on the laptop, at
tag `api-freeze-2026-11-15e`. **No field changed meaning**, and every check result on the laptop is the
same as before (22/27 on our copy; the laptop's numbers are untouched by these fixes).

| # | Item | Status | Check |
|---|---|---|---|
| 1 | `POST /api/summary` 500 for areas with no fires | **Fixed.** The trend test returned NaN on an all-zero series. | Your Sahara repro → **200**. The response has `this_month` counts 0, `anomaly_label` "no fires recorded", `trend` slope 0 with `p_value` 1, empty `busiest_*` and `critical_next`, and `reliability: null`. Any non-finite number anywhere becomes `null`. |
| 2 | Errors without CORS | **Fixed.** An error middleware sits inside the CORS middleware. | A forced error returns JSON 500 with `access-control-allow-origin: *` |
| 3 | Small areas on the grid-only server | **Fixed with option (b).** Areas under 25 deg² count each 0.25° cell × its share inside the area. | Punjab 2016, see below |
| 4 | `GET /api/grid` ignores `bbox` | **Fixed.** `bbox=west,south,east,north` keeps cells whose centre is inside, inclusive. A malformed box gets 400. | `bbox=75,30,76,31` → 14 cells, all inside |
| 5a | `modis_only` null | **By design on the public server**: it needs single detections. The laptop returns it for areas answered from points (Punjab Nov 2016: 4,053). | Hide the slot when it's null, as you do |
| 5b | 3 of 20 world regions over ±5% | Known limit; burned-area check in `API_FOR_APP.md` part 2 | n/a |
| 5c | FRP-aware variant | Not shipped. +0.08 points, and it needs a per-month feature table joined into every weight. Not worth the risk before 16:00. | n/a |

Item 3, Punjab 2016:

| Box | Exact (laptop, single detections) | Public before | Public now |
|---|---|---|---|
| 0.05° | 2 | 0 | 35 (that cell's share) |
| 0.25° | 916 | 875 | 1,152 |
| 0.7° | 11,841 | 14,583 | 11,653 |
| 1.5° | 60,260 | 60,259 | 60,259 |

The **9 presets** on the public server still give exactly the laptop's answers (from the saved
snapshot). Areas over 25 deg² still use the cell centre, as before.

**Your side (report section 4.2):**
- item 7: `NEXT_PUBLIC_API_URL` in `app/.env.local` and in Vercel;
- item 8: delete `app/app/.env.local`;
- item 9: `git init` and commit the app. Ask Dhruv for a repo or collaborator access to push it.
