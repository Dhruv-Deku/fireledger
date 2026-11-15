"use client";

import { useEffect, useRef } from "react";
import {
  Map as MLMap,
  NavigationControl,
  Popup,
  setWorkerUrl,
  type GeoJSONSource,
  type SkySpecification,
  type StyleSpecification,
} from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
  TerraDraw,
  TerraDrawPolygonMode,
  TerraDrawRectangleMode,
} from "terra-draw";
import { TerraDrawMapLibreGLAdapter } from "terra-draw-maplibre-gl-adapter";
import { polygonBounds, type AreaOfInterest, type Polygon } from "@/lib/geo";
import { useTheme, type Theme } from "@/lib/theme";
import type { Base, MapView, Projection } from "@/lib/mapPrefs";
import type { GridResponse, Hotspot, Unit } from "@/lib/api";
import { INFERNO, MONTHS, fmtInt } from "@/lib/chartTheme";

// ---- Fire layer (GET /api/grid) ----
// Counts per cell are very skewed (median ~8, max in the thousands), so the
// colour uses log(count) / log(max): small fires stay visible next to big ones.
// Always the dark-to-bright inferno ramp: the imagery under it is the same in
// both themes.
const FIRE_COLOR = [
  "interpolate",
  ["linear"],
  ["get", "t"],
  ...INFERNO.flatMap((c, i) => [i / (INFERNO.length - 1), c]),
];

const EMPTY_FC = { type: "FeatureCollection" as const, features: [] };

function gridToGeoJSON(g: GridResponse) {
  const h = g.res / 2;
  const logMax = Math.log(Math.max(2, g.max));
  return {
    type: "FeatureCollection" as const,
    features: g.cells.map(([lat, lon, count]) => ({
      type: "Feature" as const,
      properties: { c: count, t: Math.log(Math.max(1, count)) / logMax, lat, lon },
      geometry: {
        type: "Polygon" as const,
        coordinates: [[[lon - h, lat - h], [lon + h, lat - h], [lon + h, lat + h], [lon - h, lat + h], [lon - h, lat - h]]],
      },
    })),
  };
}

// ---- Close-up fire layer: single detections (POST /api/hotspots.csv) ----

/** Detections that count in the visible box, with the weight range for the colours. */
export type DotsLayer = { points: Hotspot[]; lo: number; hi: number; unit: Unit };

/** Colour position when every detection has the same weight: INFERNO's middle
 * stop (FireLayerControl's legend shows the same swatch). */
const DOTS_UNIFORM_T = 0.5;

// Each detection is drawn as its pixel on the ground: `scan` km across the
// orbit (roughly east–west) by `track` km along it (roughly north–south).
// Colour: its weight on a log scale from the lightest to the heaviest in view.
function dotsToGeoJSON(d: DotsLayer) {
  const spread = d.hi / d.lo > 1.05 ? Math.log(d.hi / d.lo) : 0;
  return {
    type: "FeatureCollection" as const,
    features: d.points.map((p, i) => {
      const dy = p.track / 2 / 110.574;
      const dx = p.scan / 2 / (111.32 * Math.cos((p.lat * Math.PI) / 180));
      return {
        type: "Feature" as const,
        properties: { i, t: spread ? Math.log(p.weight / d.lo) / spread : DOTS_UNIFORM_T },
        geometry: {
          type: "Polygon" as const,
          coordinates: [[[p.lon - dx, p.lat - dy], [p.lon + dx, p.lat - dy], [p.lon + dx, p.lat + dy], [p.lon - dx, p.lat + dy], [p.lon - dx, p.lat - dy]]],
        },
      };
    }),
  };
}

const SATELLITE: Record<string, string> = { NOAA20: "NOAA-20", NOAA21: "NOAA-21", SNPP: "Suomi NPP", N: "Suomi NPP" };
const CONFIDENCE: Record<string, string> = { l: "low", n: "nominal", h: "high" };
const esc = (s: string) => s.replace(/[&<>"]/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[ch]!);

export type DrawMode = "polygon" | "rectangle" | null;
export type Cursor = { lat: number; lng: number; zoom: number };
/** Where the map is looking, reported when it stops moving. */
export type Viewport = { zoom: number; bounds: [number, number, number, number] };

// NASA Blue Marble with shaded relief and ocean bathymetry, served by NASA GIBS
// (public domain). The same physical Earth in both themes; only the space
// backdrop and the atmosphere change with the theme.
const BLUE_MARBLE =
  "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg";

// NASA Black Marble: Earth at night from Suomi NPP VIIRS (2016 composite).
const BLACK_MARBLE =
  "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/default/2016-01-01/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png";

// Elevation for 3D terrain: AWS Open Data terrain tiles (Terrarium encoding, no key).
const TERRAIN_DEM = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

// Satellite: Sentinel-2 cloudless 2016 mosaic by EOX (CC BY 4.0), 10 m pixels,
// sharp enough to see fields and burn scars under the single-detection view.
const SENTINEL2 = "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg";

// Terrain: atlas-style colours by height above sea level (and ocean depth),
// from the same elevation tiles. Hypsometric tints, the convention for
// physical atlas maps; they don't encode fire, so they stay out of its colours.
const RELIEF_STOPS: [number, string][] = [
  [-8000, "#0b2545"],
  [-4000, "#13406f"],
  [-1000, "#1f5f9c"],
  [-150, "#3d84c4"],
  [-1, "#79b3e0"],
  [0, "#5e8f52"],
  [250, "#86ad66"],
  [600, "#bdbf80"],
  [1200, "#d6bd86"],
  [2000, "#b48c5c"],
  [3200, "#8e6b4b"],
  [4500, "#b3a79d"],
  [6000, "#f4f4f2"],
];
const RELIEF_COLOR = ["interpolate", ["linear"], ["elevation"], ...RELIEF_STOPS.flat()];

// Borders and place names drawn over the imagery from OpenFreeMap vector tiles
// (free, no API key; OpenStreetMap data), so state lines like Punjab's show.
const OPENFREEMAP = "https://tiles.openfreemap.org/planet";
const GLYPHS = "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf";
const NAME = ["coalesce", ["get", "name:en"], ["get", "name"]];

// ---- India's official view of its borders ----
// OpenStreetMap draws de facto lines (Line of Control, Line of Actual Control)
// as borders. Maps for India must show the whole of Jammu & Kashmir and Ladakh
// (including PoK and Aksai Chin) as India. So:
//  1. inside the J&K / Ladakh box, OSM country lines are hidden and replaced by
//     Natural Earth's India point-of-view boundaries (public domain), cut to
//     the box by scripts/extract-india-view.py and served from /geo;
//  2. elsewhere in South Asia, disputed lines are shown only if India claims them;
//  3. Pakistani/Chinese province lines inside India's claim, and their region
//     labels (Azad Kashmir, Gilgit-Baltistan), are hidden.
const INDIA_VIEW_LINES = "/geo/india-view-kashmir.geojson";
const box = (w: number, s: number, e: number, n: number) => ({
  type: "Polygon" as const,
  coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
});
const KASHMIR_BOX = box(72.4, 32.2, 80.6, 37.2); // must match the cut box of the GeoJSON
const SOUTH_ASIA_BOX = box(60, 0, 100, 40);
const POK_REGION_NAMES = [
  "Azad Kashmir",
  "Azad Jammu and Kashmir",
  "Gilgit-Baltistan",
  "آزاد کشمیر",
  "گلگت بلتستان",
];
const COUNTRY_BORDER_FILTER = [
  "all",
  ["==", ["get", "admin_level"], 2],
  ["!=", ["get", "maritime"], 1],
  ["!", ["within", KASHMIR_BOX]],
  ["any", ["!=", ["get", "disputed"], 1], ["==", ["get", "claimed_by"], "IN"], ["!", ["within", SOUTH_ASIA_BOX]]],
];
const STATE_BORDER_FILTER = [
  "all",
  ["==", ["get", "admin_level"], 4],
  ["!=", ["get", "maritime"], 1],
  [
    "any",
    ["!=", ["get", "disputed"], 1],
    // India's own internal lines in J&K are tagged disputed in OSM too; keep them.
    // (No claimed_by exception here: OSM tags a Tibet–Xinjiang province line
    // inside Aksai Chin as claimed_by IN, which would draw a Chinese line in India.)
    ["in", "India", ["coalesce", ["get", "disputed_name"], ""]],
  ],
];
const LABEL_PAINT = {
  "text-color": "#f5f5f4",
  "text-halo-color": "rgba(0,0,0,0.75)",
  "text-halo-width": 1.3,
};

// drawing uses the sodium amber (an action); the chosen area is drawn in paper white,
// amber would disappear against orange fire cells
const ACCENT = "#ffb547";
const AREA_LINE = "#f4efe6";
/** Shown under the imagery while tiles load, and as the whole sphere when offline. */
const OCEAN = "#0b2545";

// Served from public/ by scripts/copy-maplibre-worker.mjs (runs before dev/build).
setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

function skyFor(theme: Theme): SkySpecification {
  return {
    // Full atmosphere halo when the whole globe is in view, fading out as you zoom in.
    "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 4, 0.9, 7, 0],
    "sky-color": theme === "light" ? "#dfe8f2" : "#050608",
    "horizon-color": theme === "light" ? "#f4f1ec" : "#0a0f1a",
  };
}

/** 3D globe, flat map, or Auto: globe when zoomed out, flat as you zoom in. */
function projectionFor(p: Projection) {
  if (p === "flat") return { type: "mercator" } as const;
  if (p === "globe") return { type: "globe" } as const;
  return {
    type: ["interpolate", ["linear"], ["zoom"], 4, "vertical-perspective", 6, "mercator"],
  } as unknown as { type: "globe" };
}

// Street map layers, drawn from the same OpenFreeMap tiles. Colours follow the theme.
const STREET_LAYERS = [
  "street-land",
  "street-landcover",
  "street-water",
  "street-waterway",
  "street-roads-minor",
  "street-roads-major",
  "street-buildings",
];

const TERRAIN_LAYERS = ["terrain-relief", "terrain-hillshade", "terrain-water", "terrain-rivers"];

// ---- Political map ----
// Countries filled by Natural Earth's MAPCOLOR7 (seven colours, chosen so
// neighbours differ), India's point of view, made by scripts/build-political.py.
// 1.9 MB, so it's added the first time the political map is chosen, not on load.
const POLITICAL_SHAPES = "/geo/countries-india-view.geojson";
const POLITICAL_LAYERS = ["political-ocean", "political-fill"];

/** Calm tones, so the fire colours on top stay the loudest thing on the map. */
function politicalColors(theme: Theme) {
  return theme === "light"
    ? { ocean: "#cddfeb", fills: ["#e9dcc4", "#d5e3c8", "#f1d8cc", "#dad3ea", "#f2eac1", "#cde3e8", "#ecd0dc"] }
    : { ocean: "#0a121c", fills: ["#2d3b4f", "#3a3349", "#2e4436", "#4a3a2b", "#3d3c29", "#284247", "#47303a"] };
}
const politicalFill = (fills: string[]) => ["match", ["get", "color"], ...fills.flatMap((c, i) => [i + 1, c]), fills[0]];

function ensurePolitical(map: MLMap, theme: Theme) {
  if (map.getSource("political")) return;
  const c = politicalColors(theme);
  map.addSource("political", {
    type: "geojson",
    data: POLITICAL_SHAPES,
    attribution: "Countries: Natural Earth (India's view)",
  });
  // Under the street layers, above the imagery (hidden while this map is on).
  map.addLayer({ id: "political-ocean", type: "background", paint: { "background-color": c.ocean } }, "street-land");
  map.addLayer(
    { id: "political-fill", type: "fill", source: "political", paint: { "fill-color": politicalFill(c.fills) as never } },
    "street-land",
  );
}

/** The layers that make up each base map; the others are hidden. */
const BASE_LAYERS: Record<Base, string[]> = {
  physical: ["earth"],
  satellite: ["satellite"],
  terrain: TERRAIN_LAYERS,
  political: POLITICAL_LAYERS,
  street: STREET_LAYERS,
  night: ["night"],
};

function streetColors(theme: Theme) {
  return theme === "light"
    ? { land: "#f1eee8", green: "#dfe8d3", water: "#b9d3e2", road: "#ffffff", major: "#f4c27a", building: "#e3ddd3", casing: "#d9d2c5" }
    : { land: "#14161b", green: "#18221c", water: "#0c1d2e", road: "#2a2e36", major: "#5a4632", building: "#1d2026", casing: "#0b0c0f" };
}

function buildStyle(theme: Theme, view: MapView): StyleSpecification {
  const s = streetColors(theme);
  const vis = (b: Base) => (view.base === b ? ("visible" as const) : ("none" as const));
  const street = vis("street");
  const terrain = vis("terrain");
  return {
    version: 8,
    projection: projectionFor(view.projection),
    sky: skyFor(theme),
    glyphs: GLYPHS,
    sources: {
      earth: {
        type: "raster",
        tiles: [BLUE_MARBLE],
        tileSize: 256,
        maxzoom: 8,
        attribution: "Imagery: NASA Blue Marble, via NASA GIBS",
      },
      night: {
        type: "raster",
        tiles: [BLACK_MARBLE],
        tileSize: 256,
        maxzoom: 8,
        attribution: "Night lights: NASA Black Marble (VIIRS), via NASA GIBS",
      },
      dem: {
        type: "raster-dem",
        tiles: [TERRAIN_DEM],
        tileSize: 256,
        maxzoom: 14,
        encoding: "terrarium",
        attribution: "Elevation: AWS Terrain Tiles (Mapzen)",
      },
      // Same elevation tiles, kept apart from the 3D-terrain source (MapLibre
      // draws relief and hillshade better from a source of their own).
      relief: {
        type: "raster-dem",
        tiles: [TERRAIN_DEM],
        tileSize: 256,
        maxzoom: 12,
        encoding: "terrarium",
      },
      satellite: {
        type: "raster",
        tiles: [SENTINEL2],
        tileSize: 256,
        maxzoom: 15,
        attribution:
          'Satellite: <a href="https://s2maps.eu">Sentinel-2 cloudless</a> by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2016 & 2017)',
      },
      osm: {
        type: "vector",
        url: OPENFREEMAP,
        attribution: "Borders and names: © OpenFreeMap, © OpenMapTiles, © OpenStreetMap contributors",
      },
      "india-view": {
        type: "geojson",
        data: INDIA_VIEW_LINES,
        attribution: "Borders in J&K and Ladakh: Natural Earth (India's view)",
      },
    },
    layers: [
      { id: "ocean", type: "background", paint: { "background-color": OCEAN } },
      {
        id: "earth",
        type: "raster",
        source: "earth",
        layout: { visibility: vis("physical") },
        paint: { "raster-fade-duration": 250 },
      },
      {
        id: "satellite",
        type: "raster",
        source: "satellite",
        layout: { visibility: vis("satellite") },
        paint: { "raster-fade-duration": 250 },
      },
      {
        id: "night",
        type: "raster",
        source: "night",
        layout: { visibility: vis("night") },
        paint: { "raster-fade-duration": 250 },
      },
      // ---- Terrain (atlas) map ----
      {
        id: "terrain-relief",
        type: "color-relief",
        source: "relief",
        layout: { visibility: terrain },
        paint: { "color-relief-color": RELIEF_COLOR as never },
      },
      {
        id: "terrain-hillshade",
        type: "hillshade",
        source: "relief",
        layout: { visibility: terrain },
        paint: {
          "hillshade-exaggeration": 0.4,
          "hillshade-shadow-color": "rgba(20, 16, 12, 0.5)",
          "hillshade-highlight-color": "rgba(255, 255, 255, 0.18)",
          "hillshade-accent-color": "rgba(20, 16, 12, 0.25)",
        },
      },
      {
        id: "terrain-water",
        type: "fill",
        source: "osm",
        "source-layer": "water",
        minzoom: 4,
        layout: { visibility: terrain },
        paint: { "fill-color": "#4f93cf" },
      },
      {
        id: "terrain-rivers",
        type: "line",
        source: "osm",
        "source-layer": "waterway",
        minzoom: 6,
        filter: ["in", ["get", "class"], ["literal", ["river", "canal"]]],
        layout: { visibility: terrain, "line-cap": "round" },
        paint: { "line-color": "#4f93cf", "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.6, 12, 2] },
      },
      // ---- Street map ----
      { id: "street-land", type: "background", layout: { visibility: street }, paint: { "background-color": s.land } },
      {
        id: "street-landcover",
        type: "fill",
        source: "osm",
        "source-layer": "landcover",
        filter: ["in", ["get", "class"], ["literal", ["wood", "grass", "farmland"]]],
        layout: { visibility: street },
        paint: { "fill-color": s.green, "fill-opacity": 0.7 },
      },
      { id: "street-water", type: "fill", source: "osm", "source-layer": "water", layout: { visibility: street }, paint: { "fill-color": s.water } },
      {
        id: "street-waterway",
        type: "line",
        source: "osm",
        "source-layer": "waterway",
        minzoom: 8,
        layout: { visibility: street },
        paint: { "line-color": s.water, "line-width": 1 },
      },
      {
        id: "street-roads-minor",
        type: "line",
        source: "osm",
        "source-layer": "transportation",
        minzoom: 9,
        filter: ["in", ["get", "class"], ["literal", ["secondary", "tertiary", "minor"]]],
        layout: { visibility: street, "line-cap": "round", "line-join": "round" },
        paint: { "line-color": s.road, "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.5, 14, 3] },
      },
      {
        id: "street-roads-major",
        type: "line",
        source: "osm",
        "source-layer": "transportation",
        minzoom: 5,
        filter: ["in", ["get", "class"], ["literal", ["motorway", "trunk", "primary"]]],
        layout: { visibility: street, "line-cap": "round", "line-join": "round" },
        paint: { "line-color": s.major, "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.6, 14, 5] },
      },
      {
        id: "street-buildings",
        type: "fill",
        source: "osm",
        "source-layer": "building",
        minzoom: 13,
        layout: { visibility: street },
        paint: { "fill-color": s.building, "fill-outline-color": s.casing },
      },
      {
        id: "state-borders",
        type: "line",
        source: "osm",
        "source-layer": "boundary",
        minzoom: 3.5,
        filter: STATE_BORDER_FILTER as never,
        paint: { "line-color": "rgba(255,255,255,0.4)", "line-width": 0.8, "line-dasharray": [3, 2] },
      },
      {
        id: "country-borders",
        type: "line",
        source: "osm",
        "source-layer": "boundary",
        filter: COUNTRY_BORDER_FILTER as never,
        paint: {
          "line-color": "rgba(255,255,255,0.6)",
          "line-width": ["interpolate", ["linear"], ["zoom"], 1, 0.5, 6, 1.3],
        },
      },
      {
        id: "india-view-borders",
        type: "line",
        source: "india-view",
        paint: {
          "line-color": "rgba(255,255,255,0.6)",
          "line-width": ["interpolate", ["linear"], ["zoom"], 1, 0.5, 6, 1.3],
        },
      },
      {
        id: "country-names",
        type: "symbol",
        source: "osm",
        "source-layer": "place",
        minzoom: 2.5,
        maxzoom: 7,
        filter: ["==", ["get", "class"], "country"],
        layout: {
          "text-field": NAME as never,
          "text-font": ["Noto Sans Bold"],
          "text-size": ["interpolate", ["linear"], ["zoom"], 2.5, 10, 6, 14],
          "text-transform": "uppercase",
          "text-letter-spacing": 0.08,
          "text-max-width": 8,
        },
        paint: LABEL_PAINT,
      },
      {
        id: "state-names",
        type: "symbol",
        source: "osm",
        "source-layer": "place",
        minzoom: 4.5,
        filter: [
          "all",
          ["==", ["get", "class"], "state"],
          ["!", ["in", NAME, ["literal", POK_REGION_NAMES]]],
          ["!", ["in", ["get", "name"], ["literal", POK_REGION_NAMES]]],
        ] as never,
        layout: {
          "text-field": NAME as never,
          "text-font": ["Noto Sans Regular"],
          "text-size": 12,
          "text-letter-spacing": 0.04,
        },
        paint: { ...LABEL_PAINT, "text-color": "rgba(245,245,244,0.8)" },
      },
      {
        id: "city-names",
        type: "symbol",
        source: "osm",
        "source-layer": "place",
        minzoom: 5,
        filter: ["in", ["get", "class"], ["literal", ["city", "town"]]],
        layout: {
          "text-field": NAME as never,
          "text-font": ["Noto Sans Regular"],
          "text-size": ["interpolate", ["linear"], ["zoom"], 5, 11, 9, 13],
          "symbol-sort-key": ["get", "rank"] as never,
        },
        paint: LABEL_PAINT,
      },
    ],
  };
}

const LABEL_LAYERS = ["country-names", "state-names", "city-names"];

/** Apply projection, base map, street colours and label ink without reloading the style. */
function applyView(map: MLMap, view: MapView, theme: Theme) {
  map.setProjection(projectionFor(view.projection));
  map.setSky(skyFor(theme));
  if (view.base === "political") ensurePolitical(map, theme);
  for (const [base, ids] of Object.entries(BASE_LAYERS)) {
    for (const id of ids) {
      // Political layers exist only once that map has been chosen.
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", base === view.base ? "visible" : "none");
    }
  }
  if (map.getSource("political")) {
    const pc = politicalColors(theme);
    map.setPaintProperty("political-ocean", "background-color", pc.ocean);
    map.setPaintProperty("political-fill", "fill-color", politicalFill(pc.fills) as never);
  }

  const s = streetColors(theme);
  map.setPaintProperty("street-land", "background-color", s.land);
  map.setPaintProperty("street-landcover", "fill-color", s.green);
  map.setPaintProperty("street-water", "fill-color", s.water);
  map.setPaintProperty("street-waterway", "line-color", s.water);
  map.setPaintProperty("street-roads-minor", "line-color", s.road);
  map.setPaintProperty("street-roads-major", "line-color", s.major);
  map.setPaintProperty("street-buildings", "fill-color", s.building);
  map.setPaintProperty("street-buildings", "fill-outline-color", s.casing);

  // Light text with a dark halo reads on imagery and dark maps; the light street
  // map and the atlas-coloured terrain map need dark ink, as printed atlases use.
  const darkInk =
    ((view.base === "street" || view.base === "political") && theme === "light") || view.base === "terrain";
  for (const id of LABEL_LAYERS) {
    map.setPaintProperty(id, "text-color", darkInk ? "#1c1917" : id === "state-names" ? "rgba(245,245,244,0.8)" : "#f5f5f4");
    map.setPaintProperty(id, "text-halo-color", darkInk ? "rgba(255,255,255,0.9)" : "rgba(0,0,0,0.75)");
  }
  map.setPaintProperty("country-borders", "line-color", darkInk ? "rgba(28,25,23,0.55)" : "rgba(255,255,255,0.6)");
  map.setPaintProperty("india-view-borders", "line-color", darkInk ? "rgba(28,25,23,0.55)" : "rgba(255,255,255,0.6)");
  map.setPaintProperty("state-borders", "line-color", darkInk ? "rgba(28,25,23,0.35)" : "rgba(255,255,255,0.4)");
}

const prefersReducedMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

type Props = {
  aoi: AreaOfInterest | null;
  /** Harmonized hotspot counts per cell for one month, or null to hide the fire layer. */
  grid?: GridResponse | null;
  /** Single detections for the close-up view, or null to hide them. */
  dots?: DotsLayer | null;
  /** Projection, base map and terrain chosen in the map switcher. */
  view: MapView;
  drawMode: DrawMode;
  onDrawn: (geometry: Polygon, mode: Exclude<DrawMode, null>) => void;
  onCursor: (c: Cursor | null) => void;
  /** Called with false if the NASA imagery can't be loaded (offline). */
  onBasemap?: (online: boolean) => void;
  /** Zoom and visible box after each move, e.g. to pick the fire-layer cell size. */
  onViewport?: (v: Viewport) => void;
};

export default function GlobeMap({
  aoi,
  grid = null,
  dots = null,
  view,
  drawMode,
  onDrawn,
  onCursor,
  onBasemap,
  onViewport,
}: Props) {
  const { theme } = useTheme();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const drawRef = useRef<TerraDraw | null>(null);
  const readyRef = useRef(false);
  // Keep the latest callbacks/area/theme/mode without re-creating the map.
  const cb = useRef({ onDrawn, onCursor, onBasemap, onViewport });
  const aoiRef = useRef(aoi);
  const themeRef = useRef(theme);
  const viewRef = useRef(view);
  const gridRef = useRef(grid);
  const dotsRef = useRef(dots);
  const drawModeRef = useRef(drawMode);
  const terrainOnRef = useRef(false);
  useEffect(() => {
    cb.current = { onDrawn, onCursor, onBasemap, onViewport };
    aoiRef.current = aoi;
    themeRef.current = theme;
    viewRef.current = view;
    gridRef.current = grid;
    dotsRef.current = dots;
    drawModeRef.current = drawMode;
  });

  // Create the map once.
  useEffect(() => {
    if (!containerRef.current) return;
    const map = new MLMap({
      container: containerRef.current,
      style: buildStyle(themeRef.current, viewRef.current),
      center: [76, 22],
      zoom: 1.7,
      maxZoom: 16,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    // Dev-only handle for inspecting map data from the browser console.
    if (process.env.NODE_ENV === "development") (window as unknown as { __flMap?: MLMap }).__flMap = map;
    map.addControl(new NavigationControl({ visualizePitch: true }), "top-right");

    map.on("style.load", () => map.setProjection(projectionFor(viewRef.current.projection)));

    map.on("load", () => {
      // Keep the credits collapsed behind their (i) button; MapLibre opens
      // them on wide maps (again after each resize), where the long text would
      // run under the fire legend.
      const collapseCredits = () =>
        containerRef.current?.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
      collapseCredits();
      map.on("resize", collapseCredits);
      // Fire layer: above the imagery, under borders, labels and the area outline.
      map.addSource("fire", { type: "geojson", data: EMPTY_FC });
      map.addLayer(
        {
          id: "fire-cells",
          type: "fill",
          source: "fire",
          paint: {
            "fill-color": FIRE_COLOR as never,
            "fill-opacity": ["interpolate", ["linear"], ["zoom"], 1, 0.85, 8, 0.7],
          },
        },
        "state-borders",
      );
      if (gridRef.current) (map.getSource("fire") as GeoJSONSource).setData(gridToGeoJSON(gridRef.current));
      // Single detections, same colour ramp. The outline keeps a 1-pixel minimum
      // so the smallest VIIRS pixels stay visible.
      map.addSource("fire-dots", { type: "geojson", data: EMPTY_FC });
      map.addLayer(
        {
          id: "fire-dots",
          type: "fill",
          source: "fire-dots",
          paint: {
            "fill-color": FIRE_COLOR as never,
            "fill-outline-color": FIRE_COLOR as never,
            "fill-opacity": 0.85,
          },
        },
        "state-borders",
      );
      if (dotsRef.current) (map.getSource("fire-dots") as GeoJSONSource).setData(dotsToGeoJSON(dotsRef.current));

      // Click a cell (when not drawing) to read its count.
      map.on("click", "fire-cells", (e) => {
        if (drawModeRef.current) return;
        const f = e.features?.[0];
        const g = gridRef.current;
        if (!f || !g) return;
        const { c, lat, lon } = f.properties as { c: number; lat: number; lon: number };
        const period = g.month ? `${MONTHS[g.month - 1]} ${g.year}` : String(g.year);
        const ns = `${Math.abs(lat).toFixed(2)}°${lat >= 0 ? "N" : "S"}`;
        const ew = `${Math.abs(lon).toFixed(2)}°${lon >= 0 ? "E" : "W"}`;
        new Popup({ closeButton: false, className: "fl-popup", maxWidth: "240px" })
          .setLngLat(e.lngLat)
          .setHTML(
            `<div class="num" style="font-size:18px">${fmtInt(c)}</div>` +
              `<div style="font-size:12px">hotspots in ${period}, ${g.unit === "viirs" ? "VIIRS" : "MODIS"}-equivalent</div>` +
              `<div class="num" style="font-size:11px;opacity:.7;margin-top:2px">cell ${ns}, ${ew} · ${g.res}°</div>`,
          )
          .addTo(map);
      });
      // Click a detection to see which satellite saw it, when, and what it counts as.
      map.on("click", "fire-dots", (e) => {
        if (drawModeRef.current) return;
        const i = e.features?.[0]?.properties?.i as number | undefined;
        const d = dotsRef.current;
        const p = d && i !== undefined ? d.points[i] : undefined;
        if (!d || !p) return;
        const [y, m, day] = p.date.split("-").map(Number);
        const hhmm = p.time.padStart(4, "0");
        const sat = esc(SATELLITE[p.satellite] ?? p.satellite);
        const conf = esc(CONFIDENCE[p.confidence] ?? p.confidence);
        const w = Number.isInteger(p.weight) ? String(p.weight) : p.weight.toFixed(2);
        new Popup({ closeButton: false, className: "fl-popup", maxWidth: "260px" })
          .setLngLat(e.lngLat)
          .setHTML(
            `<div class="num" style="font-size:18px">${w}</div>` +
              `<div style="font-size:12px">${w === "1" ? "hotspot" : "hotspots"} this detection counts as, ${d.unit === "viirs" ? "VIIRS" : "MODIS"}-equivalent</div>` +
              `<div style="font-size:12px;margin-top:6px">${sat} · ${day} ${MONTHS[m - 1]} ${y}, ${hhmm.slice(0, 2)}:${hhmm.slice(2)} UTC · ${p.daynight === "N" ? "night" : "day"}</div>` +
              `<div class="num" style="font-size:11px;opacity:.7;margin-top:2px">pixel ${p.scan.toFixed(2)} × ${p.track.toFixed(2)} km · fire power ${p.frp} MW · ${conf} confidence</div>`,
          )
          .addTo(map);
      });
      for (const layer of ["fire-cells", "fire-dots"]) {
        map.on("mouseenter", layer, () => {
          if (!drawModeRef.current) map.getCanvas().style.cursor = "pointer";
        });
        map.on("mouseleave", layer, () => {
          map.getCanvas().style.cursor = "";
        });
      }

      // Area of interest: a dark casing under the orange line keeps it readable
      // on bright desert and dark ocean alike.
      map.addSource("aoi", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      map.addLayer({ id: "aoi-fill", type: "fill", source: "aoi", paint: { "fill-color": AREA_LINE, "fill-opacity": 0.07 } });
      map.addLayer({ id: "aoi-casing", type: "line", source: "aoi", paint: { "line-color": "#0a0a0a", "line-opacity": 0.55, "line-width": 4.5 } });
      map.addLayer({ id: "aoi-line", type: "line", source: "aoi", paint: { "line-color": AREA_LINE, "line-width": 2 } });

      const draw = new TerraDraw({
        adapter: new TerraDrawMapLibreGLAdapter({ map }),
        modes: [
          new TerraDrawPolygonMode({
            styles: {
              fillColor: ACCENT,
              fillOpacity: 0.12,
              outlineColor: ACCENT,
              outlineWidth: 2,
              closingPointColor: ACCENT,
              closingPointOutlineColor: "#0a0a0a",
            },
          }),
          new TerraDrawRectangleMode({
            styles: { fillColor: ACCENT, fillOpacity: 0.12, outlineColor: ACCENT, outlineWidth: 2 },
          }),
        ],
      });
      draw.start();
      draw.on("finish", (id) => {
        const mode = draw.getMode() as Exclude<DrawMode, null>;
        const feature = draw.getSnapshot().find((f) => f.id === id);
        if (feature && feature.geometry.type === "Polygon") {
          cb.current.onDrawn(feature.geometry as Polygon, mode);
        }
        // The shape now lives in app state and is drawn by our own "aoi" layers.
        draw.clear();
        draw.setMode("static");
      });
      drawRef.current = draw;
      readyRef.current = true;
      if (drawModeRef.current) draw.setMode(drawModeRef.current);
      // A view or theme chosen while the map was loading, and terrain from a saved setup.
      applyView(map, viewRef.current, themeRef.current);
      applyTerrain(map, viewRef.current.terrain, terrainOnRef);
      if (aoiRef.current) showAoi(map, aoiRef.current, false);
    });

    // Tell the panel once if NASA's imagery can't be reached (offline sphere).
    let reported = false;
    map.on("error", (e) => {
      const src = (e as unknown as { sourceId?: string }).sourceId;
      if (src === "earth" && !reported) {
        reported = true;
        cb.current.onBasemap?.(false);
      }
    });

    const reportViewport = () => {
      const b = map.getBounds();
      cb.current.onViewport?.({ zoom: map.getZoom(), bounds: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()] });
    };
    map.on("load", reportViewport);
    map.on("moveend", reportViewport);

    map.on("mousemove", (e) => cb.current.onCursor({ lat: e.lngLat.lat, lng: e.lngLat.lng, zoom: map.getZoom() }));
    map.on("mouseout", () => cb.current.onCursor(null));

    return () => {
      readyRef.current = false;
      try {
        drawRef.current?.stop();
      } catch {
        /* map already torn down */
      }
      drawRef.current = null;
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // Follow the theme and the map switcher. The imagery itself doesn't change
  // with the theme; the sky, street colours and label ink do.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    applyView(map, view, theme);
  }, [theme, view]);

  // 3D terrain on/off (tilts the camera so the relief is visible).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    applyTerrain(map, view.terrain, terrainOnRef);
  }, [view.terrain]);

  // Fire layer data (null hides it).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    const src = map.getSource("fire") as GeoJSONSource | undefined;
    src?.setData(grid ? gridToGeoJSON(grid) : EMPTY_FC);
  }, [grid]);

  // Single detections (null hides them).
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    const src = map.getSource("fire-dots") as GeoJSONSource | undefined;
    src?.setData(dots ? dotsToGeoJSON(dots) : EMPTY_FC);
  }, [dots]);

  // Follow the draw mode chosen in the panel.
  useEffect(() => {
    const draw = drawRef.current;
    if (!draw || !readyRef.current) return;
    draw.setMode(drawMode ?? "static");
  }, [drawMode]);

  // Show the area outline and fly to it.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    showAoi(map, aoi, true);
  }, [aoi]);

  // MapLibre's CSS sets `.maplibregl-map { position: relative }`, so the map
  // element fills an absolutely positioned wrapper instead of being absolute itself.
  // The wrapper carries the space backdrop (stars in dark, pale sky in light);
  // the map canvas is transparent outside the sphere.
  return (
    <div className="space-backdrop absolute inset-0">
      <div
        ref={containerRef}
        className="h-full w-full"
        role="application"
        aria-label="Globe. Draw an area or pick one in the side panel."
      />
    </div>
  );
}

function applyTerrain(map: MLMap, on: boolean, onRef: { current: boolean }) {
  if (on === onRef.current) return;
  onRef.current = on;
  const duration = prefersReducedMotion() ? 0 : 800;
  if (on) {
    map.setTerrain({ source: "dem", exaggeration: 1.5 });
    if (map.getPitch() < 30) map.easeTo({ pitch: 55, duration });
  } else {
    map.setTerrain(null);
    map.easeTo({ pitch: 0, duration });
  }
}

function showAoi(map: MLMap, aoi: AreaOfInterest | null, animate: boolean) {
  const src = map.getSource("aoi") as GeoJSONSource | undefined;
  if (!src) return;
  src.setData({
    type: "FeatureCollection",
    features: aoi ? [{ type: "Feature", properties: {}, geometry: aoi.geometry }] : [],
  });
  if (!aoi) return;
  // Panels are docked beside the map, but opening the results panel changes
  // the map's width in the same render; measure the new size first.
  map.resize();
  // Below lg the results panel slides over the map: keep the area clear of it.
  const region = document.querySelector<HTMLElement>("[data-region-panel]");
  // A phone-width sheet covers the whole map; then frame for when it's closed.
  const canvasW = map.getCanvas().clientWidth;
  const sideOverlay =
    region && getComputedStyle(region).position === "absolute" && region.offsetWidth < canvasW - 200
      ? region.offsetWidth
      : 0;
  // The "choose an area" card floats over the map's top-left: keep the area to
  // its right when there's room, else below it, else ignore it. Padding wider
  // than the map makes MapLibre skip the fit altogether.
  const canvasH = map.getCanvas().clientHeight;
  const pad = { top: 48, bottom: 48, left: 48, right: sideOverlay + 48 };
  const card = document.querySelector<HTMLElement>("[data-where-card]");
  if (card) {
    const beside = card.offsetWidth + 36;
    const below = card.offsetHeight + 36;
    if (canvasW - pad.right - beside >= 200) pad.left = beside;
    else if (canvasH - pad.bottom - below >= 160) pad.top = below;
  }
  if (canvasW - pad.left - pad.right < 120) pad.right = 48;
  map.fitBounds(polygonBounds(aoi.geometry), {
    padding: pad,
    maxZoom: 9,
    duration: animate && !prefersReducedMotion() ? 1200 : 0,
  });
}
