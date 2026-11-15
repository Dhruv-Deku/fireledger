"use client";

import { useEffect, useRef, useState } from "react";
import { Map as MLMap, setWorkerUrl, type GeoJSONSource } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { getGrid, getHealth, USING_MOCK, type GridResponse } from "@/lib/api";
import { INFERNO, MONTHS } from "@/lib/chartTheme";
import { useTheme } from "@/lib/theme";
import { SATS, satAt, viewBasis } from "./orbits";

setWorkerUrl("/maplibre/maplibre-gl-worker.mjs");

const NIGHT =
  "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_Black_Marble/default/2016-01-01/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png";
const DAY =
  "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg";

const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// every grid cell as a glowing point, colour on a log scale like the app's map
function toPoints(g: GridResponse) {
  const logMax = Math.log(Math.max(2, g.max));
  return {
    type: "FeatureCollection" as const,
    features: g.cells.map(([lat, lon, c]) => ({
      type: "Feature" as const,
      properties: { t: Math.log(Math.max(1, c)) / logMax },
      geometry: { type: "Point" as const, coordinates: [lon, lat] },
    })),
  };
}

/**
 * run fn now if the map's style is loaded, else once it is. in dev react mounts
 * the globe twice, so "ready" can be true while the second map is still loading.
 * returns a cleanup that drops the wait.
 */
function whenStyled(map: MLMap, fn: () => void) {
  // not isStyleLoaded(): that stays false while tiles load, long after style.load has fired.
  // the layers exist exactly when the style is ready to be changed.
  if (map.getLayer("fire")) {
    fn();
    return undefined;
  }
  map.once("style.load", fn);
  return () => {
    map.off("style.load", fn);
  };
}

/** orbits run this many times faster than real life, so a lap takes under a minute */
export const ORBIT_SPEEDUP = 120;

const dot3 = (a: readonly number[], b: readonly number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];

/**
 * the satellites and their orbit rings over the globe. the globe is drawn by maplibre,
 * so its centre and radius on screen are read back from the map each frame and the
 * orbits are projected the same way (orthographic: close enough to maplibre's camera).
 */
function drawSatellites(cv: HTMLCanvasElement, map: MLMap, tMs: number, theme: string) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth;
  const h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  const ctx = cv.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const c = map.getCenter();
  const cp = map.project(c);
  // globe radius on screen, from a point 80 degrees north of the centre
  const lat2 = c.lat + 80;
  const far = lat2 <= 90 ? map.project([c.lng, lat2]) : map.project([c.lng + 180, 180 - lat2]);
  const r = Math.hypot(far.x - cp.x, far.y - cp.y) / Math.sin((80 * Math.PI) / 180);
  if (!Number.isFinite(r) || r <= 0) return;

  const { east, north, out } = viewBasis(c.lng, c.lat);
  const light = theme === "light";
  const ink = light ? "14,22,38" : "236,231,220";
  const font = getComputedStyle(document.documentElement).getPropertyValue("--font-archivo").trim() || "sans-serif";

  for (const s of SATS) {
    const { pos, ring, scale } = satAt(s, tMs);
    const to = (v: readonly number[]) => {
      const x = dot3(v, east);
      const y = dot3(v, north);
      const z = dot3(v, out);
      return { sx: cp.x + x * r * scale, sy: cp.y - y * r * scale, hidden: z < 0 && Math.hypot(x, y) * scale < 1, z };
    };

    // the ring: bright in front of the earth, faint behind it, gone where the earth covers it
    ctx.lineWidth = 1;
    for (let k = 1; k < ring.length; k++) {
      const a = to(ring[k - 1]);
      const b = to(ring[k]);
      if (a.hidden || b.hidden) continue;
      ctx.strokeStyle = `rgba(${ink},${a.z >= 0 ? 0.32 : 0.1})`;
      ctx.beginPath();
      ctx.moveTo(a.sx, a.sy);
      ctx.lineTo(b.sx, b.sy);
      ctx.stroke();
    }

    const p = to(pos);
    if (p.hidden) continue;
    const front = p.z >= 0;
    ctx.globalAlpha = front ? 1 : 0.45;
    ctx.fillStyle = `rgba(${ink},0.18)`;
    ctx.beginPath();
    ctx.arc(p.sx, p.sy, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = `rgb(${ink})`;
    ctx.beginPath();
    ctx.arc(p.sx, p.sy, 3, 0, Math.PI * 2);
    ctx.fill();

    ctx.font = `600 12px ${font}`;
    ctx.textBaseline = "middle";
    ctx.lineWidth = 3;
    ctx.strokeStyle = light ? "rgba(255,255,255,0.85)" : "rgba(4,7,15,0.8)";
    ctx.strokeText(s.name, p.sx + 11, p.sy - 6);
    ctx.fillText(s.name, p.sx + 11, p.sy - 6);
    ctx.font = `400 11px ${font}`;
    ctx.fillStyle = `rgba(${ink},0.7)`;
    const sub = `${s.sensor}, ${s.heightKm} km`;
    ctx.strokeText(sub, p.sx + 11, p.sy + 8);
    ctx.fillText(sub, p.sx + 11, p.sy + 8);
    ctx.globalAlpha = 1;
  }
}

/** zoom at which the globe is about `px` pixels across (maplibre globe, 512px tiles) */
const zoomFor = (px: number) => Math.log2((px * Math.PI) / 512);

/**
 * the home page's globe: earth at night with last month's fire hotspots on it,
 * turning slowly. drag to spin it; it picks the turn back up after a moment.
 */
export default function HomeGlobe({ onMonth }: { onMonth?: (label: string | null) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const { theme } = useTheme();
  const themeRef = useRef(theme);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    themeRef.current = theme;
  });

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const wide = el.clientWidth >= 900;
    // small enough that the polar orbits (about 1.13 earth radii) fit on screen around it
    const diameter = wide ? el.clientHeight * 0.74 : Math.min(el.clientWidth * 0.78, el.clientHeight * 0.5);
    const map = new MLMap({
      container: el,
      center: [78, 16],
      zoom: zoomFor(diameter),
      attributionControl: { compact: true },
      interactive: true,
      scrollZoom: false,
      boxZoom: false,
      doubleClickZoom: false,
      dragRotate: false,
      keyboard: false,
      touchZoomRotate: false,
      style: {
        version: 8,
        projection: { type: "globe" },
        sky: {
          "atmosphere-blend": 1,
          "sky-color": "#04070f",
          "horizon-color": "#0b1530",
        },
        sources: {
          night: { type: "raster", tiles: [NIGHT], tileSize: 256, maxzoom: 8, attribution: "Night lights: NASA Black Marble, via NASA GIBS" },
          day: { type: "raster", tiles: [DAY], tileSize: 256, maxzoom: 8, attribution: "Imagery: NASA Blue Marble, via NASA GIBS" },
          fire: { type: "geojson", data: { type: "FeatureCollection", features: [] } },
        },
        layers: [
          { id: "ocean", type: "background", paint: { "background-color": "#050a16" } },
          { id: "night", type: "raster", source: "night", layout: { visibility: themeRef.current === "light" ? "none" : "visible" } },
          { id: "day", type: "raster", source: "day", layout: { visibility: themeRef.current === "light" ? "visible" : "none" } },
          {
            id: "fire",
            type: "circle",
            source: "fire",
            paint: {
              "circle-color": ["interpolate", ["linear"], ["get", "t"], ...INFERNO.flatMap((c, i) => [i / (INFERNO.length - 1), c])] as never,
              "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 1.1, 3, 2.4, 5, 4],
              "circle-blur": 0.55,
              "circle-opacity": ["interpolate", ["linear"], ["get", "t"], 0, 0.55, 1, 1],
              "circle-pitch-alignment": "map",
            },
          },
        ],
      },
    });
    mapRef.current = map;
    if (process.env.NODE_ENV === "development") (window as unknown as { __flHome?: MLMap }).__flHome = map;
    // on wide screens the globe sits right of the headline
    // and on phones it sits up top, above the text
    if (wide) map.setPadding({ left: el.clientWidth * 0.42, top: 0, right: 0, bottom: 0 });
    else map.setPadding({ left: 0, top: 72, right: 0, bottom: el.clientHeight * 0.5 });
    // an inline style can finish loading inside the constructor, before this listener exists,
    // so load is the backup
    const markReady = () => setReady(true);
    map.once("style.load", markReady);
    map.once("load", () => {
      markReady();
      el.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
    });

    // slow turn, paused while someone drags it; the satellites are redrawn every frame
    let raf = 0;
    let pausedUntil = 0;
    const still = reduced();
    const t0 = Date.now();
    const p0 = performance.now();
    const frame = () => {
      if (!still && performance.now() > pausedUntil && !map.isMoving()) {
        const c = map.getCenter();
        map.setCenter([c.lng + 0.035, c.lat]);
      }
      const cv = canvas.current;
      if (cv) drawSatellites(cv, map, still ? t0 : t0 + (performance.now() - p0) * ORBIT_SPEEDUP, themeRef.current);
      raf = requestAnimationFrame(frame);
    };
    const hold = () => (pausedUntil = performance.now() + 2500);
    map.on("dragstart", hold);
    map.on("drag", hold);
    raf = requestAnimationFrame(frame);

    return () => {
      cancelAnimationFrame(raf);
      map.remove();
      mapRef.current = null;
    };
  }, []);

  // light theme swaps earth at night for the daytime blue marble
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    return whenStyled(map, () => {
      map.setLayoutProperty("night", "visibility", theme === "light" ? "none" : "visible");
      map.setLayoutProperty("day", "visibility", theme === "light" ? "visible" : "none");
    });
  }, [theme, ready]);

  // last complete month of fire, from the api
  useEffect(() => {
    if (!ready || USING_MOCK) {
      onMonth?.(null);
      return;
    }
    const ctrl = new AbortController();
    (async () => {
      const h = await getHealth(ctrl.signal);
      const end = h?.coverage?.end;
      if (!end) return;
      let [y, m] = end.split("-").map(Number);
      const d = Number(end.slice(8, 10));
      if (d < new Date(Date.UTC(y, m, 0)).getUTCDate()) {
        m -= 1;
        if (m === 0) {
          m = 12;
          y -= 1;
        }
      }
      const g = await getGrid(
        { year: y, month: m, unit: "viirs", daynight: "both", hide_industrial: false, res: 0.25 },
        ctrl.signal,
      );
      const map = mapRef.current;
      if (!g || !map || ctrl.signal.aborted) return;
      whenStyled(map, () => (map.getSource("fire") as GeoJSONSource | undefined)?.setData(toPoints(g)));
      onMonth?.(`${MONTHS[m - 1]} ${y}`);
    })().catch(() => onMonth?.(null));
    return () => ctrl.abort();
  }, [ready, onMonth]);

  // maplibre's css makes its own container position:relative, so it gets a wrapper that holds the size
  return (
    <div className="absolute inset-0" aria-hidden>
      <div ref={box} className="h-full w-full" />
      <canvas ref={canvas} className="pointer-events-none absolute inset-0 h-full w-full" />
    </div>
  );
}
