# FireLedger app

The web app for FireLedger: draw an area, or pick one of 9 preset regions, and get its harmonized fire record from
November 2000 to today. MODIS and VIIRS hotspots are counted on one scale (VIIRS NOAA-20 equivalent by default).

**Live:** https://fireledger.vercel.app

## Pages

| Page | What it shows |
|---|---|
| `/` | Home: the globe with last month's fires and the five fire satellites |
| `/app` | Map and region panel: this month vs normal, the last 30 days, people near fires, the burning calendar (counts or "vs normal"), busiest months and years, fire season, trend, the monthly line, downloads (hotspot CSV, monthly CSV, calendar PNG) |
| `/accuracy` | How far to trust the numbers: held-out error, band coverage, the 2012 switch, burned-area and official-count checks, known limits |

## Run it

```powershell
npm ci
# point it at an API: the laptop's full API, or the public one
echo NEXT_PUBLIC_API_URL=https://dhruv-deku--fireledger-web.us-east.modal.run > .env.local
npm run build
npm run start            # http://localhost:3000
```

`npm run dev` works too, but only opened as `localhost` (Next.js blocks other origins in development).
Without `NEXT_PUBLIC_API_URL` the app runs on `lib/mock.json` (sample data).

Deploy: `npx vercel deploy --prod --yes` (the Vercel project has `NEXT_PUBLIC_API_URL` set).

## Code

| Path | What |
|---|---|
| `lib/api.ts` | every API call and its types (the contract is in `../docs/API_FOR_APP.md`) |
| `components/Workspace.tsx` | the map page: area, filters, fire layer, region panel |
| `components/RegionPanel.tsx` | the panel's cards, top to bottom |
| `components/charts/` | calendar heat-map, monthly line, daily card |
| `components/AccuracyReport.tsx` | the accuracy page |

Stack: Next.js 16, React 19, MapLibre, ECharts, Tailwind.
