# Request: finer cells for `/api/grid`

From the app side. Today `/api/grid` answers `res` = 0.25, 0.5, 1 or 2 only
(`0.1` → 400 "res must be 0.25, 0.5, 1 or 2 (degrees)"). At 0.25° a cell is ~28 km, so when a user
zooms into Punjab the fire layer is a handful of big squares. We'd like finer cells when zoomed in.

## What we're asking for

1. **More `res` values:** `0.1` (≈11 km) and `0.05` (≈5.5 km). Same answer shape:
   `{year, month, unit, res, max, cells: [[lat, lon, count], ...]}`, `lat`/`lon` at the cell centre,
   only cells with fire, harmonized (same weights as the 0.25° grid, summed into the smaller cells).
2. **An optional `bbox=west,south,east,north`** (degrees, WGS84) that limits the answer to cells whose
   centre is inside the box. Without it a world grid at 0.05° is hundreds of thousands of cells per month,
   too heavy for a laptop browser. `max` should then be the max inside the box.
3. Keep the other parameters as they are (`year`, `month`, `unit`, `daynight`, `hide_industrial`).

If `bbox` is too much work, `res=0.1` alone (world grid) is still useful; we'll measure the size.

## What the app already does (no app change needed when you ship it)

| Map zoom | Fire layer | Request |
|---|---|---|
| < 5 | world grid, 0.25° | `GET /api/grid?res=0.25` (as today) |
| 5 to 7 | 0.1° cells | `GET /api/grid?res=0.1&bbox=<visible area, rounded out to whole degrees>` |
| 7 to 9 | 0.05° cells | `GET /api/grid?res=0.05&bbox=...` |
| ≥ 9 | single detections | `POST /api/hotspots.csv?full=true`, see below |

- If the API answers 400 "res must be ..." for a size, the app skips that size until reload and uses
  the next coarser one. So you can ship 0.1 first and 0.05 later.
- The legend and the cell popup read `res` from your answer, so they say the right cell size.

Example the app will send:

```
GET /api/grid?year=2026&month=10&unit=viirs&daynight=both&hide_industrial=false&res=0.05&bbox=73,29,78,33
```

## Close zoom: single detections (works today, no change needed)

At zoom ≥ 9 (a view about 1° across) the map stops drawing cells and draws each detection at its
pixel size (`scan` × `track`), coloured by `weight`. It uses the existing CSV endpoint:

- `POST /api/hotspots.csv?full=true`, geometry = the visible area rounded out to 0.5°, `start`/`end` =
  the shown month, `harmonized: true`, the user's unit / day-night / industrial filters.
- Rows with `weight` 0 are not drawn (S-NPP on NOAA-20 days, MODIS once VIIRS counts), so a fire seen
  by two satellites shows once.
- The app never asks for a box over 6 square degrees. For scale: a 6°×6° box of Central Africa in
  Dec 2025 is 209,575 rows / 23 MB / 50 s, while Punjab 4°×4° in Nov 2026 is 8,704 rows / 3 s.
- Heads-up on load: one CSV request per pan at close zoom (cached in the browser, 250 ms debounce).
- A 413 shows "Too many detections here to draw one by one. Zoom in further." and the map keeps the cells.
