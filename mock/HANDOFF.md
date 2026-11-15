# Hand-off: what the app expects from the API

For the data person building `api/`. The contract in `docs/API_FOR_APP.md` is the source of truth;
this note adds only what the app needs on top of it, and what we found when checking `mock.json`.

## How the app calls the API

- Base URL: `NEXT_PUBLIC_API_URL` (no trailing slash). When it's unset the app uses `mock.json`.
- `POST /api/calendar`, `POST /api/summary`, `POST /api/hotspots.csv` — all take the same JSON body as
  `request_example` in `mock.json`. The app sends them 500 ms after the user stops changing the area/filters.
- `GET /api/presets` → array of `{ id, name, geometry }` (GeoJSON Polygon).
- Any non-2xx answer is shown to the user as "The API answered <status> for <path>", so please return proper
  status codes (400 for a bad body, 500 for a crash) rather than 200 with an error message.
- CORS must allow `http://localhost:3000` (and the Vercel domain once deployed). The app sends
  `Content-Type: application/json`, so the preflight `OPTIONS` must be allowed.
- Mixed content: a website on https (Vercel) cannot call `http://<laptop-ip>:8000`. For the demo, run the
  website locally on http, or put the API behind an https tunnel (cloudflared / ngrok).

## One optional field (no breaking change)

`summary.this_month.days_covered` (integer, optional): how many days of the current month the data covers.
If present, the app shows "Nov 1–14 so far · data through 14 Nov". If absent, it guesses from the request's
`end` date and labels it "(your end date)". Please send it if you can.

## Rules the numbers must follow (the app shows them side by side)

Run `python -I mock/validate_mock.py <file.json>` on a saved API answer to check these.
The answer file needs the same top-level keys as `mock.json` (`calendar`, `summary`, `hotspots_csv_sample`,
`presets`, `request_example`).

1. `months` are ordered, continuous (no missing months) and start at the request `start` month.
2. `lo <= count <= hi`; all counts >= 0; `flags` only uses the letters `M C D B`.
3. `raw_viirs` is 0 before 2012 (VIIRS didn't exist).
4. `summary.this_month.count` equals the calendar's `count` for the same year-month.
5. `median`, `p25`, `p75` are computed from the same calendar month in past years, and
   `pct_diff = (count - median) / median * 100`.
6. `busiest_months` = the 3 months with the highest median `count` (complete months only, not the current one);
   `busiest_years` = the 3 full years with the highest summed `count`, and `total` equals that sum.
7. The CSV header is exactly
   `acq_date,acq_time,latitude,longitude,satellite,instrument,frp,confidence,daynight,type,weight`.

## `mock.json` is now internally consistent

The summary used to disagree with the calendar (e.g. Nov 2026 was 1,520 in one place and 2,023.4 in the other).
`mock/fix_summary.py` now derives every summary number from the calendar, using the rules above plus:

- `season`: peak = month with the highest median; start/end = the run of months around it whose median is at
  least 25 % of the peak's.
- `critical_next`: months in the next 6 whose median is at least 25 % of the peak's.
- `anomaly`: robust z = (count − median) / (1.4826 × MAD) over the same month in earlier years.
- `trend`: Sen's slope of full-year totals, with a two-sided Mann-Kendall p-value.
- `this_month.days_covered`: 14 (Nov 1–14 so far).

`python -I mock/validate_mock.py mock/mock.json` → 0 errors. The real API should use the same definitions (or
tell the app person if it doesn't). The calendar starts in Jan 2001, matching `request_example.start`.

## Borders

The app draws Jammu & Kashmir and Ladakh per India's official boundary (Natural Earth, India's view). Nothing is
needed from the API for this; it's noted so the area presets and any server-side maps follow the same view.
