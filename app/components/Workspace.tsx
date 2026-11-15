"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import {
  getCalendar,
  getPresets,
  getSummary,
  getGrid,
  getHealth,
  isGridOnly,
  getCountedHotspots,
  USING_MOCK,
  type GridResponse,
  type Hotspot,
  type Unit,
  type CalendarResponse,
  type Preset,
  type RequestBody,
  type SummaryResponse,
} from "@/lib/api";
import { polygonBounds, type AreaOfInterest, type Polygon } from "@/lib/geo";
import {
  DEFAULT_VIEW,
  forgetPlace,
  learnPlace,
  loadPlaces,
  loadSetup,
  placeKey,
  rankPlaces,
  saveSetup,
  type LearnedPlace,
  type MapView,
} from "@/lib/mapPrefs";
import type { Cursor, DotsLayer, DrawMode, Viewport } from "./GlobeMap";
import WhereCard, { type PlaceItem } from "./WhereCard";
import FiltersPill from "./FiltersPill";
import { RECORD_START, type Filters } from "./FiltersPanel";
import MapSwitcher from "./MapSwitcher";
import RegionPanel from "./RegionPanel";
import StatusStrip from "./StatusStrip";
import FireLayerControl, { type Period } from "./FireLayerControl";
import FireTimeline from "./FireTimeline";

/** The month before `p`. */
const prevMonth = (p: Period): Period => (p.month === 1 ? { year: p.year - 1, month: 12 } : { year: p.year, month: p.month - 1 });
const nextMonth = (p: Period): Period => (p.month === 12 ? { year: p.year + 1, month: 1 } : { year: p.year, month: p.month + 1 });
const monthIdx = (p: Period) => p.year * 12 + p.month - 1;
/** Pause on each month while playing the timeline (after its map has loaded). */
const PLAY_STEP_MS = 900;
const RECORD_FIRST: Period = { year: 2000, month: 11 };
/** World grid, served today. */
const GRID_RES = 0.25;
/**
 * Finer cells for the visible area as you zoom in, finest first. Needs `res` +
 * `bbox` on /api/grid (docs/REQUEST_FINER_GRID.md); a size the API refuses is
 * skipped until reload, so the map falls back to the next coarser one.
 */
const FINE_STEPS = [
  { zoom: 7, res: 0.05 },
  { zoom: 5, res: 0.1 },
];
/** From this zoom, single detections (POST /api/hotspots.csv) replace the cells. */
const DOTS_ZOOM = 9;
/** Largest box, in square degrees, to fetch single detections for. A busy month
 * can hold ~6,000 detections per square degree (Central Africa, Dec 2025). */
const DOTS_MAX_AREA = 6;
/** Single-detection answers kept in memory (each can be a few MB). */
const DOTS_CACHE_SIZE = 12;

type Box = [number, number, number, number];
/** `bounds` rounded outward to multiples of `step` degrees, as "w,s,e,n" (stable for effect deps). */
function boxKey([w, s, e, n]: Box, step: number) {
  return [
    Math.max(-180, Math.floor(w / step) * step),
    Math.max(-90, Math.floor(s / step) * step),
    Math.min(180, Math.ceil(e / step) * step),
    Math.min(90, Math.ceil(n / step) * step),
  ].join(",");
}
const parseBox = (key: string) => key.split(",").map(Number) as Box;
const pad2 = (n: number) => String(n).padStart(2, "0");

/** The weight range sets the dot colours and the legend. */
function toDotsLayer(points: Hotspot[], unit: Unit): DotsLayer {
  let lo = Infinity;
  let hi = 0;
  for (const p of points) {
    if (p.weight < lo) lo = p.weight;
    if (p.weight > hi) hi = p.weight;
  }
  return { points, unit, lo: points.length ? lo : 0, hi };
}

// MapLibre needs the browser (WebGL), so never render it on the server.
const GlobeMap = dynamic(() => import("./GlobeMap"), {
  ssr: false,
  loading: () => (
    <div className="absolute inset-0 grid place-items-center text-sm text-fl-muted">
      Loading globe
    </div>
  ),
});

/** Wait this long after the last change before asking for data. */
const DEBOUNCE_MS = 500;

export type DataState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  /** no hotspot at all in the area and dates (the API can't build a summary from nothing) */
  | { status: "empty"; calendar: CalendarResponse; request: RequestBody }
  | { status: "ready"; calendar: CalendarResponse; summary: SummaryResponse; request: RequestBody };

const today = () => new Date().toISOString().slice(0, 10);

/** "Drawn area · 30.9°N, 75.9°E": names a drawn shape by its centre so it's recognisable later. */
function drawnLabel(g: Polygon) {
  const [[w, s], [e, n]] = polygonBounds(g);
  const lat = (s + n) / 2;
  const lon = (w + e) / 2;
  return `Drawn area · ${Math.abs(lat).toFixed(1)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lon).toFixed(1)}°${lon >= 0 ? "E" : "W"}`;
}

/** Defaults, overlaid with the setup saved on this browser (remember my setup). */
function initialSetup() {
  const t = today();
  const defaults: Filters = {
    start: RECORD_START,
    end: t,
    unit: "viirs",
    daynight: "both",
    hide_industrial: false,
    harmonized: true,
  };
  const s = loadSetup<Filters>();
  let filters = defaults;
  if (s?.filters) {
    // An end date that was "today" when saved means "up to now".
    const end = s.filters.end === s.savedOn || s.filters.end > t ? t : s.filters.end;
    filters = { ...defaults, ...s.filters, end, start: s.filters.start > end ? RECORD_START : s.filters.start };
  }
  return { view: s?.view ?? DEFAULT_VIEW, aoi: linkedArea() ?? s?.aoi ?? null, filters };
}

/** a box sent by a link, e.g. from the home page feed: /app?area=w,s,e,n&label=... */
function linkedArea(): AreaOfInterest | null {
  const q = new URLSearchParams(window.location.search);
  const b = q.get("area")?.split(",").map(Number);
  if (!b || b.length !== 4 || !b.every(Number.isFinite)) return null;
  const [w, s, e, n] = b;
  if (w >= e || s >= n) return null;
  return {
    geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
    label: q.get("label") || "Linked area",
  };
}

export default function Workspace() {
  // Workspace renders only in the browser (see ClientWorkspace), so reading
  // localStorage for the initial state is safe.
  const [boot] = useState(initialSetup);
  const [aoi, setAoi] = useState<AreaOfInterest | null>(boot.aoi);
  const [drawMode, setDrawMode] = useState<DrawMode>(null);
  const [cursor, setCursor] = useState<Cursor | null>(null);
  const [online, setOnline] = useState(true);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [maxDate] = useState(today);
  const [filters, setFilters] = useState<Filters>(boot.filters);
  const [data, setData] = useState<DataState>({ status: "idle" });
  const [regionOpen, setRegionOpen] = useState(!!boot.aoi);
  const showRegion = !!aoi && regionOpen;
  const [view, setView] = useState<MapView>(boot.view);
  const [learned, setLearned] = useState<LearnedPlace[]>(() => rankPlaces(loadPlaces()));
  // Slider order is fixed for this visit (ranking from past visits), so stepping
  // with ‹ › never bounces between two places. New ranking applies next visit.
  const [rankedAtLoad] = useState(() => learned.map((p) => p.key));

  // ---- Fire layer (GET /api/grid) ----
  const [fireOn, setFireOn] = useState(true);
  const [latest, setLatest] = useState<{ period: Period; throughDay?: number }>(() => {
    const now = new Date();
    return { period: { year: now.getUTCFullYear(), month: now.getUTCMonth() + 1 } };
  });
  // a link from the home page can ask for a place and a month: /app?place=punjab&month=2016-11
  const [link] = useState(() => {
    const q = new URLSearchParams(window.location.search);
    const m = q.get("month")?.match(/^(\d{4})-(\d{2})$/);
    return { place: q.get("place"), period: m ? { year: Number(m[1]), month: Number(m[2]) } : null };
  });
  // otherwise the latest complete month (the current one is still filling in)
  const [period, setPeriod] = useState<Period>(() => link.period ?? prevMonth(latest.period));
  const [grid, setGrid] = useState<GridResponse | null>(null);
  const [gridLoading, setGridLoading] = useState(false);
  const [gridError, setGridError] = useState<string | null>(null);
  const gridCache = useRef(new Map<string, GridResponse | null>());
  const [viewport, setViewport] = useState<Viewport | null>(null);
  // Cell sizes the API refused (400 "res must be ..."); skipped until reload.
  const [refused, setRefused] = useState<number[]>([]);

  // Cell size and area for the fire layer: world at 0.25° when zoomed out,
  // finer cells for the visible box (rounded out to whole degrees) when zoomed in.
  const step = viewport ? FINE_STEPS.find((s) => viewport.zoom >= s.zoom && !refused.includes(s.res)) : undefined;
  const cellRes = step?.res ?? GRID_RES;
  const cellBox = step && viewport ? boxKey(viewport.bounds, 1) : "";

  // Close up: every detection that counts, drawn at its pixel size, instead of cells.
  const [dots, setDots] = useState<DotsLayer | null>(null);
  const [dotsLoading, setDotsLoading] = useState(false);
  const [dotsError, setDotsError] = useState<string | null>(null);
  const dotsCache = useRef(new Map<string, DotsLayer>());
  // a grid-only server has no single hotspots, so the map keeps its cells at every zoom
  const [gridOnly, setGridOnly] = useState(false);
  useEffect(() => {
    isGridOnly()
      .then(setGridOnly)
      .catch(() => {});
  }, []);
  const dotsBox = viewport && viewport.zoom >= DOTS_ZOOM ? boxKey(viewport.bounds, 0.5) : "";
  const dotsTooBig = !!dotsBox && (() => {
    const [w, s, e, n] = parseBox(dotsBox);
    return (e - w) * (n - s) > DOTS_MAX_AREA;
  })();
  // The last answer stays up while the next box loads, so panning doesn't flash.
  const dotsShown = fireOn && dotsBox && !dotsTooBig && !dotsError && !gridOnly ? dots : null;

  // How far the data goes, from the API.
  useEffect(() => {
    const ctrl = new AbortController();
    getHealth(ctrl.signal)
      .then((h) => {
        const end = h?.coverage?.end;
        if (!end) return;
        const [y, m, d] = end.split("-").map(Number);
        const last: Period = { year: y, month: m };
        const daysInLast = new Date(Date.UTC(y, m, 0)).getUTCDate();
        const partial = d < daysInLast;
        setLatest({ period: last, throughDay: partial ? d : undefined });
        if (!link.period) setPeriod(partial ? prevMonth(last) : last);
      })
      .catch(() => {
        /* keep the date-based default */
      });
    return () => ctrl.abort();
  }, [link.period]);

  // Fetch the grid for the shown month, with the same filters as the results.
  useEffect(() => {
    if (!fireOn || USING_MOCK) return;
    const q = {
      year: period.year,
      month: period.month,
      unit: filters.unit,
      daynight: filters.daynight,
      hide_industrial: filters.hide_industrial,
      res: cellRes,
      bbox: cellBox ? parseBox(cellBox) : undefined,
    };
    const key = JSON.stringify(q);
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      const cached = gridCache.current.get(key);
      if (cached !== undefined) {
        setGrid(cached);
        setGridError(null);
        return;
      }
      setGridLoading(true);
      getGrid(q, ctrl.signal)
        .then((g) => {
          gridCache.current.set(key, g);
          setGrid(g);
          setGridError(null);
        })
        .catch((e: Error) => {
          if (e.name === "AbortError") return;
          // The API doesn't serve this cell size yet: skip it, the next coarser one loads.
          if (q.res !== GRID_RES && /res must/i.test(e.message)) return setRefused((r) => [...r, q.res]);
          setGridError(e.message);
        })
        // Also on abort: a cached answer for the next view sets no loading of its own.
        .finally(() => setGridLoading(false));
    }, 250);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [fireOn, period, filters.unit, filters.daynight, filters.hide_industrial, cellRes, cellBox]);

  // Fetch single detections for the visible box when zoomed in close.
  useEffect(() => {
    if (!fireOn || USING_MOCK || !dotsBox || dotsTooBig || gridOnly) return;
    const [w, s, e, n] = parseBox(dotsBox);
    const lastDay = new Date(Date.UTC(period.year, period.month, 0)).getUTCDate();
    const body: RequestBody = {
      geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] },
      start: `${period.year}-${pad2(period.month)}-01`,
      end: `${period.year}-${pad2(period.month)}-${pad2(lastDay)}`,
      unit: filters.unit,
      daynight: filters.daynight,
      hide_industrial: filters.hide_industrial,
      harmonized: true, // same as the cells: /api/grid is always harmonized
    };
    const key = JSON.stringify(body);
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      const cached = dotsCache.current.get(key);
      if (cached) {
        setDots(cached);
        setDotsError(null);
        return;
      }
      setDotsLoading(true);
      setDotsError(null);
      getCountedHotspots(body, ctrl.signal)
        .then((points) => {
          if (!points) return;
          const layer = toDotsLayer(points, body.unit);
          const cache = dotsCache.current;
          cache.set(key, layer);
          if (cache.size > DOTS_CACHE_SIZE) cache.delete(cache.keys().next().value!);
          setDots(layer);
        })
        .catch((err: Error) => {
          if (err.name === "AbortError") return;
          setDotsError(
            /too many hotspots/i.test(err.message)
              ? "Too many detections here to draw one by one. Zoom in further."
              : err.message,
          );
        })
        .finally(() => setDotsLoading(false));
    }, 250);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [fireOn, period, filters.unit, filters.daynight, filters.hide_industrial, dotsBox, dotsTooBig, gridOnly]);

  // Play the timeline: step a month once the map for this month has loaded.
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    if (!playing || gridLoading) return;
    const t = setTimeout(() => {
      if (monthIdx(period) >= monthIdx(latest.period)) setPlaying(false);
      else setPeriod(nextMonth(period));
    }, PLAY_STEP_MS);
    return () => clearTimeout(t);
  }, [playing, gridLoading, period, latest.period]);

  function togglePlay() {
    // From the end, play starts again at the first month.
    if (!playing && monthIdx(period) >= monthIdx(latest.period)) setPeriod(RECORD_FIRST);
    setPlaying(!playing);
  }

  // Remember my setup: save every change on this browser.
  useEffect(() => {
    saveSetup({ view, aoi, filters, savedOn: today() });
  }, [view, aoi, filters]);

  // Location slider: places ranked by past use, then presets, then areas new this visit.
  const places = useMemo<PlaceItem[]>(() => {
    const byKey = new Map<string, PlaceItem>();
    presets.forEach((p) => {
      const area = { geometry: p.geometry, label: p.name };
      byKey.set(placeKey(area), { key: placeKey(area), area, learned: false });
    });
    learned.forEach((p) =>
      byKey.set(p.key, { key: p.key, area: { geometry: p.geometry, label: p.label }, learned: true, uses: p.uses }),
    );
    const presetKeys = presets.map((p) => placeKey({ geometry: p.geometry, label: p.name }));
    const lastUsed = new Map(learned.map((p) => [p.key, p.lastUsed]));
    const sortKey = (k: string): [number, number] => {
      const r = rankedAtLoad.indexOf(k);
      if (r >= 0) return [0, r];
      const pi = presetKeys.indexOf(k);
      if (pi >= 0) return [1, pi];
      return [2, lastUsed.get(k) ?? 0];
    };
    return [...byKey.values()].sort((a, b) => {
      const [ga, ia] = sortKey(a.key);
      const [gb, ib] = sortKey(b.key);
      return ga - gb || ia - ib;
    });
  }, [learned, presets, rankedAtLoad]);

  /** A user choice of area (not a restore): show it and learn from it. */
  function chooseArea(next: AreaOfInterest) {
    setAoi(next);
    setRegionOpen(true);
    setLearned(learnPlace(next));
  }

  // Presets: once, from the API (or the mock).
  useEffect(() => {
    const ctrl = new AbortController();
    getPresets(ctrl.signal)
      .then((list) => {
        setPresets(list);
        const p = link.place ? list.find((x) => x.id === link.place) : undefined;
        if (p) chooseArea({ geometry: p.geometry, label: p.name });
      })
      .catch((e: Error) => {
        if (e.name !== "AbortError") console.warn("Presets failed to load:", e.message);
      });
    return () => ctrl.abort();
  }, [link.place]);

  // The request body for the current area + filters (API contract shape).
  const body = useMemo<RequestBody | null>(
    () => (aoi ? { geometry: aoi.geometry, ...filters } : null),
    [aoi, filters],
  );

  // Fetch calendar + summary when the area or a filter changes, debounced.
  useEffect(() => {
    if (!body) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      setData({ status: "loading" });
      Promise.allSettled([getCalendar(body, ctrl.signal), getSummary(body, ctrl.signal)]).then(([cal, sum]) => {
        if (ctrl.signal.aborted) return;
        // no fire at all: say so, whatever the summary did (today it fails on such areas)
        if (cal.status === "fulfilled" && cal.value.months.every((m) => m.count === 0 && m.raw_modis === 0 && m.raw_viirs === 0)) {
          return setData({ status: "empty", calendar: cal.value, request: body });
        }
        if (cal.status === "fulfilled" && sum.status === "fulfilled") {
          return setData({ status: "ready", calendar: cal.value, summary: sum.value, request: body });
        }
        const e = (cal.status === "rejected" ? cal.reason : (sum as PromiseRejectedResult).reason) as Error;
        if (e.name !== "AbortError") setData({ status: "error", message: e.message });
      });
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(t);
      ctrl.abort(); // a newer change wins; drop the older answer
    };
  }, [body]);

  function handleDrawn(geometry: Polygon) {
    chooseArea({ geometry, label: drawnLabel(geometry) });
    setDrawMode(null);
  }

  function handleAoi(next: AreaOfInterest | null) {
    if (next) return chooseArea(next);
    setAoi(null);
    setRegionOpen(false);
    setData({ status: "idle" });
  }

  // Map first (Zoom Earth, Ventusky, Watch Duty): the globe fills the window,
  // choosing an area and the filters float over its top-left corner, the
  // fire record runs along the bottom as the timeline, results dock right.
  return (
    <main className="relative flex min-h-0 flex-1 overflow-hidden" data-region-open={showRegion ? "" : undefined}>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="relative min-h-0 flex-1">
          <GlobeMap
            aoi={aoi}
            grid={fireOn && !dotsShown ? grid : null}
            dots={dotsShown}
            view={view}
            drawMode={drawMode}
            onDrawn={handleDrawn}
            onCursor={setCursor}
            onBasemap={setOnline}
            onViewport={setViewport}
          />

          {/* On a phone the card stops short of the zoom and map-type buttons. */}
          <div className="pointer-events-none absolute left-3 right-14 top-3 z-20 flex flex-col items-start gap-2 sm:right-auto sm:w-[360px]">
            <div className="pointer-events-auto w-full">
              <WhereCard
                aoi={aoi}
                drawMode={drawMode}
                onDrawMode={setDrawMode}
                onAoi={handleAoi}
                places={places}
                activeKey={aoi ? placeKey(aoi) : null}
                onForget={(key) => setLearned(rankPlaces(forgetPlace(key)))}
                onShowResults={aoi && !regionOpen ? () => setRegionOpen(true) : undefined}
                offline={!online}
              />
            </div>
            <div className="pointer-events-auto max-w-full">
              <FiltersPill value={filters} maxDate={maxDate} onChange={setFilters} />
            </div>
          </div>

          {/* 3d globe or 2d map, and the map type, under the zoom and compass buttons */}
          <div className="absolute right-2.5 top-[110px] z-10 flex flex-col items-end gap-2 sm:flex-row sm:items-center">
            <div role="radiogroup" aria-label="Globe or flat map" className="glass flex h-9 items-center gap-0.5 rounded-[10px]! p-[3px]">
              {(
                [
                  ["3D", "3D globe", view.projection !== "flat"],
                  ["2D", "2D map", view.projection === "flat"],
                ] as const
              ).map(([short, long, on]) => (
                <button
                  key={short}
                  type="button"
                  role="radio"
                  aria-checked={on}
                  onClick={() =>
                    setView((v) => ({
                      ...v,
                      projection: short === "2D" ? "flat" : v.projection === "flat" ? "auto" : v.projection,
                    }))
                  }
                  className={`h-full rounded-[7px] px-2.5 text-[13px] font-medium transition-colors ${
                    on ? "bg-fl-accent text-fl-on-accent" : "text-fl-muted hover:text-fl-text"
                  }`}
                >
                  <span className="sm:hidden">{short}</span>
                  <span className="hidden sm:inline">{long}</span>
                </button>
              ))}
            </div>
            <MapSwitcher view={view} onChange={setView} />
          </div>

          <StatusStrip cursor={cursor} />

          {!USING_MOCK && (
            <FireLayerControl
              on={fireOn}
              onToggle={() => setFireOn((v) => !v)}
              loading={gridLoading || dotsLoading}
              error={dotsShown ? null : gridError}
              maxCount={grid?.max ?? null}
              cellRes={grid?.res ?? GRID_RES}
              dots={dotsShown ? { count: dotsShown.points.length, lo: dotsShown.lo, hi: dotsShown.hi } : null}
              note={
                dotsBox
                  ? gridOnly
                    ? "Single detections need the full-data server; this one has monthly totals only."
                    : dotsTooBig
                      ? "Zoom in further to see single detections."
                      : dotsError
                  : null
              }
              unitText={filters.unit === "viirs" ? "VIIRS-equivalent" : "MODIS-equivalent"}
            />
          )}
        </div>

        <FireTimeline
          months={data.status === "ready" || data.status === "empty" ? data.calendar.months : null}
          loading={data.status === "loading"}
          areaLabel={aoi?.label ?? null}
          period={period}
          min={RECORD_FIRST}
          max={latest.period}
          latestThroughDay={latest.throughDay}
          onPeriod={setPeriod}
          playing={playing}
          onPlay={togglePlay}
          switchVerdict={data.status === "ready" ? data.summary.reliability?.verdict : null}
        />
      </div>

      {/* Right dock: the selected area's results */}
      {showRegion && (
        <RegionPanel
          aoi={aoi}
          data={data}
          onClose={() => setRegionOpen(false)}
          onHideIndustrial={() => setFilters((f) => ({ ...f, hide_industrial: true }))}
        />
      )}
    </main>
  );
}
