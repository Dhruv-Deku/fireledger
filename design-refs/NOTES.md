# FireLedger — design references (captured 14 Nov 2026)

Screenshots were taken from the live sites listed in "FireLedger — App Design References.md".
Copy the patterns only, never the look. Everything gets restyled to our dark theme: `#050505`, glass panels, `#ff4d00`, inferno colours.

| # | File | Site | What to take |
|---|------|------|--------------|
| 01 | `01-gwis-seasonal-trend-band.jpg` | GWIS Seasonal Trend | **This year vs normal:** a grey min–max band (2012–2025), a blue average line and a red "this year" line. Weekly and cumulative charts sit side by side. The legend sits under each chart. → Use for the A4 line chart (our lo–hi band) and the A5 "This month vs normal" card. |
| 02 | `02-gwis-viirs-and-modis-separate.jpg` | GWIS Seasonal Trend (further down) | **Pitch hook:** VIIRS thermal anomalies and MODIS thermal anomalies are drawn as *separate* charts, each against its own normal. We show one harmonized line. Put this next to ours on the pitch and Method slides. |
| 03 | `03-gfw-weekly-alerts-vs-normal.jpg` | Global Forest Watch (now "Global Nature Watch"), India › Fires | **The best pattern for A5.** A summary sentence goes above the chart, with key words in colour: "There were **17,037** VIIRS fire alerts… This is **normal** compared to previous years going back to 2012." The normal range is two-tone (inner band = normal, outer band = above/below normal). There's a brush/zoom strip under the chart, and the dashboard sits on the left with the map on the right. |
| 04 | `04-firms-punjab-raw-dots-before.jpg` | NASA FIRMS, Punjab, last 24 h | **The "before" picture:** raw red dots, with separate "VIIRS (NOAA-20 & NOAA-21) [375 m]" and "MODIS (Aqua & Terra) [1 km]" rows in the layer panel. Toolbar along the bottom (timeline, layers, capture, share). |
| 05 | `05-worldview-timeline-bar.jpg` | NASA Worldview | **Date control:** a bottom timeline bar with a big date stepper on the left (`2026 NOV 14`, ▲/▼ per part), a zoomable track with month labels and a draggable handle. A good model for our date-range picker if we want a timeline later. |
| 06 | `06-owid-chart-title-source.jpg` | Our World in Data, Wildfires | **Chart framing:** a serif title, a one-line subtitle with the unit, and a **Table / Line toggle**. Lines are labelled at the right end (no separate legend), the current year is highlighted in red with the others faded, and "Data source: …" plus a note sit under every chart, next to download, share and fullscreen icons. |
| 07 | `07-gwis-country-profile-layout.jpg` | GWIS Country Profile | **Layout:** an area picker list on the left, a KPI row (area, forest, croplands…) and a tabbed chart (burned area / number of fires) on the right. |

Not captured: **INPE Queimadas.** The old stats URL now redirects to `data.inpe.br/queimadas/estatisticas/?tipo=estados`, which stayed on its loading spinner for more than 20 s. **earth.nullschool.net:** the screenshot failed. Its style is already described in the Design Reference Pack, and the INPE month × year table idea is described in the references doc.

## Decisions this suggests for A4–A6

1. **Summary sentence first (A5).** Each card opens with one plain sentence in INPE/GFW style: "**1,520** hotspots so far in Nov 1–14. That's **42% above** the November median (1,070)." Key numbers go in JetBrains Mono and the verdict word ("above normal" / "normal" / "below normal") is coloured with the anomaly ramp.
2. **Band chart (A4 line chart).** Draw the lo–hi band as a filled area in `rgba(255,255,255,0.08)`, the harmonized line in `#ff4d00`, and the historical median as a thin muted line, following the GWIS/GFW pattern.
3. **Chart titles state the finding (OWID).** Example: "Hotspots per month, Punjab (VIIRS-equivalent)". Put a source line under every chart: "Data: NASA FIRMS (MODIS C6.1, VIIRS 375 m), harmonized by FireLedger".
4. **Calendar / table toggle (OWID).** A small "Calendar | Table" switch above the heat-map, if time allows.
5. **Pitch.** Show 04 (FIRMS raw dots), then 02 (GWIS showing the sensors separately), then our single harmonized calendar.
