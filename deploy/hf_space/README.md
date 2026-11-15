---
title: FireLedger API
emoji: 🔥
colorFrom: gray
colorTo: red
sdk: docker
app_port: 7860
pinned: false
short_description: One fire record from MODIS and VIIRS, 2000 to today
---

# FireLedger API (public, grid-only copy)

FireLedger turns NASA FIRMS hotspots from MODIS (2000–) and VIIRS (2012–) into one continuous,
harmonized record. This Space serves the API for any area, from the 0.25° harmonized grid.
NASA Space Apps Challenge 2026, "Harmonization of MODIS and VIIRS Hot Spots".

- Interactive docs: `/docs`
- `POST /api/calendar`, `POST /api/summary`: monthly harmonized counts and the summary for a
  GeoJSON area.
- `GET /api/presets`, `/api/regions`, `/api/grid`, `/api/proof`, `/api/health`.

**Differences from the full API on the team laptop:**
- Every area is answered from 0.25° cells (cell centre inside the area), not from single hotspots,
  so small areas can differ by a few percent.
- `POST /api/hotspots.csv` needs the full hotspot data and answers 503 here.

Data: NASA FIRMS (MODIS C6.1, VIIRS 375 m), harmonized by FireLedger. Code and method:
https://github.com/Dhruv-Deku/fireledger
