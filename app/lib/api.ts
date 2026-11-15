// The only link between the app and the backend. Shapes follow the API
// contract in docs/API_FOR_APP.md. If NEXT_PUBLIC_API_URL is not set, answers
// come from lib/mock.json (fake data, same shape) instead.

import mock from "@/lib/mock.json";
import type { Polygon } from "@/lib/geo";

// ---------- Types (API contract) ----------

export type Unit = "viirs" | "modis";
export type DayNight = "D" | "N" | "both";

export type RequestBody = {
  geometry: Polygon;
  start: string; // YYYY-MM-DD
  end: string; // YYYY-MM-DD
  unit: Unit;
  daynight: DayNight;
  hide_industrial: boolean;
  harmonized: boolean;
};

export type MonthRow = {
  year: number;
  month: number; // 1-12
  count: number;
  lo: number;
  hi: number;
  raw_modis: number;
  raw_viirs: number;
  /** Letters: M = MODIS-only years, C = cross-calibrated, D = orbit drift, B = blind spot */
  flags: string;
  // Optional extras from the real API (docs/API_FOR_APP.md):
  /** The month is still in progress; data runs to `through_day`. */
  partial?: boolean;
  through_day?: number | null;
  /** Near-real-time data (from Aug 2026); NASA finalises it about 3 months later. */
  preliminary?: boolean;
  /** Where the number came from, e.g. "VIIRS", "MODIS→VIIRS", "MODIS (raw)". */
  source?: string;
  /** Part of `count` from flares, plants and volcanoes. */
  industrial?: number;
  /** Raw MODIS detections no VIIRS satellite saw that day (VIIRS years only); not in `count`. */
  modis_only?: number | null;
};

export type Trend = {
  sen_slope_per_year: number;
  p_value: number;
  pct_per_decade?: number;
  significant?: boolean;
  direction?: string;
  /** First and last full year used. */
  years?: [number, number];
};

export type CalendarResponse = { unit: Unit; months: MonthRow[] };

export type SummaryResponse = {
  this_month: {
    year: number;
    month: number;
    count: number;
    median: number;
    p25: number;
    p75: number;
    pct_diff: number;
    anomaly: number;
    /** Optional: days of this month covered by data so far (if the API sends it). */
    days_covered?: number;
    /** Optional (real API): what the count is compared with, e.g. "Nov 1–13 in other years". */
    compared_with?: string;
    /** Optional (real API): the API's own verdict, e.g. "below normal". Preferred over the app's bands. */
    anomaly_label?: string;
    /** Optional (real API): the per-year counts the median came from (same days, when partial). */
    same_days_past?: { year: number; count: number }[];
    /** Optional (real API): median of the whole month, for context when the month is partial. */
    full_month_median?: number;
    preliminary?: boolean;
    flags?: string;
    /** Optional: raw MODIS detections no VIIRS satellite saw (not in `count`). */
    modis_only?: number | null;
  };
  /** Optional (real API): share of hotspots from flares/industry; show `warning` when it isn't null. */
  industrial?: { share: number; warning: string | null };
  busiest_months: { month: number; median: number }[];
  busiest_years: { year: number; total: number }[];
  season: { start_month: number; peak_month: number; end_month: number };
  critical_next: { month: number; median: number; note: string }[];
  /** Sen's slope of full-year totals, two-sided Mann-Kendall p-value. Extras come from the real API. */
  trend: Trend;
  /** Optional: the same trend from 2012 on only (VIIRS throughout, no conversion). */
  trend_viirs?: Trend | null;
  /** Optional: busiest full years, skipping years with blind-spot (B) months. */
  busiest_years_no_blind?: { year: number; total: number }[];
  /** Optional (real API): yearly totals the trend was fitted to. */
  annual?: { year: number; count: number }[];
  /**
   * Optional (real API): how well this area's record holds across the 2012 switch
   * to VIIRS. null when the request doesn't cover 2008–2015, is raw, or is in MODIS units.
   */
  reliability?: {
    region: string;
    /** Harmonized change across 2012 minus MODIS's own change (fraction). */
    leftover_2012: number;
    verdict: "good" | "fair" | "caution" | "too few fires";
    modis_fires: [number, number];
    note: string;
    /** Optional: the same check inside the overlap years, i.e. the yardstick's own noise here. */
    noise_2012?: number;
    /** Optional: 90% interval for leftover_2012. */
    leftover_2012_range?: [number, number] | null;
  } | null;
};

export type Preset = { id: string; name: string; geometry: Polygon };

/** GET /api/grid: harmonized hotspot count per grid cell, for the map layer (real API only). */
export type GridResponse = {
  year: number;
  month: number | null;
  unit: Unit;
  res: number;
  max: number;
  /** [lat, lon, count] at each cell centre; only cells with fire. */
  cells: [number, number, number][];
};

export type GridQuery = {
  year: number;
  month: number;
  unit: Unit;
  daynight: DayNight;
  hide_industrial: boolean;
  res: number;
  /** Optional [west, south, east, north]: only cells in this box (for fine resolutions). */
  bbox?: [number, number, number, number];
};

/** One detection from POST /api/hotspots.csv?full=true: the fields the map uses. */
export type Hotspot = {
  lat: number;
  lon: number;
  date: string; // acq_date, YYYY-MM-DD
  time: string; // acq_time, HHMM UTC (leading zeros dropped)
  satellite: string;
  daynight: string;
  frp: number;
  confidence: string;
  /** Pixel size on the ground, km: across the orbit (scan) and along it (track). */
  scan: number;
  track: number;
  /** How many hotspots this detection counts as, in the chosen unit. */
  weight: number;
};

/** GET /api/proof: the harmonization's validation (only the fields the app shows). */
export type ProofResponse = {
  method: string;
  test_years: number[];
  /** Error of each method step, held-out years. wape = weighted absolute % error as a fraction. */
  ablation: {
    step: string;
    wape_cell_month: number;
    wape_region_month: number;
    wape_world_month: number;
    bias_region_month: number;
  }[];
  conformal_bands: {
    modis_to_snpp: { coverage_crossfit: { cell_month: number; region_month: number; world_month: number } };
  };
  snpp_to_noaa20_wape: { cell_month: number; region_month: number; world_month: number };
  /** Change across the 2012 sensor switch (2012–15 vs 2008–11), as fractions, per UN subregion. */
  jumps: {
    region: string;
    hotspots_per_year: number;
    step2012_raw_merged: number;
    step2012_modis_vegetation: number;
    step2012_harmonized_vegetation: number;
    industrial_share: number;
    pettitt_raw: { year: number; p: number };
    pettitt_harmonized: { year: number; p: number };
  }[];
  burned_area: {
    regions: {
      region: string;
      step2012: { modis: number; raw_merged: number; harmonized: number };
      spearman_vs_burned: { raw_merged: number; harmonized: number };
    }[];
  };
  /** Optional: industrial sites' own conversion, held-out error now ("shipped") vs before ("nov14"). */
  industrial_heldout?: Record<"shipped" | "nov14", { wape_region_month: number; wape_cell_month: number; bias: number }>;
  /** Optional: India's official crop-fire counts (ICAR-IARI CREAMS) against ours, per state and season. */
  creams?: {
    source: string;
    note: string;
    /** Last day of data, YYYY-MM-DD. */
    through: string;
    by_state_season: Record<
      string,
      {
        seasons: { year: number; first: string; last: string; days: number; creams: number; fireledger: number }[];
        /** How well the day-by-day counts line up (rank correlation). */
        daily_spearman_fireledger: number;
      }
    >;
  };
};

/** GET /api/health: how far the data goes. */
export type HealthResponse = {
  ok: boolean;
  /** "full" or "grid-only": a grid-only server has monthly totals but no single hotspots (no CSV) */
  mode?: string;
  coverage?: { start: string; end: string };
  month_to_date?: { month: number; through_day: number };
};

// ---------- Config ----------

const API_URL = process.env.NEXT_PUBLIC_API_URL?.replace(/\/+$/, "") || "";

/** True while the app runs on fake data (no NEXT_PUBLIC_API_URL). */
export const USING_MOCK = API_URL === "";

export class ApiError extends Error {}

async function post(path: string, body: RequestBody, signal?: AbortSignal): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(`Can't reach the FireLedger API at ${API_URL}.`);
  }
  if (!res.ok) throw new ApiError(await errorMessage(res, path));
  return res;
}

/** Plain words for an API error. The API sends {"detail": ...} for 400/404 (docs/API_FOR_APP.md). */
async function errorMessage(res: Response, path: string): Promise<string> {
  if (res.status === 413) {
    return "That's too many hotspots for one file. Pick a shorter date range or a smaller area.";
  }
  if (res.status === 503) {
    return "Single hotspots aren't on this server for this area. The public server has them inside the 9 preset regions; elsewhere it has monthly totals only.";
  }
  try {
    const body = await res.json();
    const d = body?.detail;
    if (typeof d === "string" && d) return `The API couldn't answer: ${d}`;
    if (Array.isArray(d) && d[0]?.msg) return `The API couldn't answer: ${d[0].msg}`;
  } catch {
    /* not JSON */
  }
  return `The API answered ${res.status} for ${path}.`;
}

function logMock(path: string, body?: RequestBody) {
  if (process.env.NODE_ENV !== "production") {
    console.debug(`[mock api] ${path}`, body ?? "");
  }
}

// ---------- Mock helpers ----------

const monthIndex = (year: number, month: number) => year * 12 + (month - 1);

/** Keep the months that overlap [start, end] (both YYYY-MM-DD). */
export function filterMonths(months: MonthRow[], start: string, end: string): MonthRow[] {
  const [sy, sm] = start.split("-").map(Number);
  const [ey, em] = end.split("-").map(Number);
  const lo = monthIndex(sy, sm);
  const hi = monthIndex(ey, em);
  return months.filter((m) => {
    const i = monthIndex(m.year, m.month);
    return i >= lo && i <= hi;
  });
}

// ---------- Endpoints ----------

export async function getCalendar(body: RequestBody, signal?: AbortSignal): Promise<CalendarResponse> {
  if (USING_MOCK) {
    logMock("POST /api/calendar", body);
    const cal = mock.calendar as CalendarResponse;
    // Only the date range changes the mock; other filters return the same fake numbers.
    return { unit: cal.unit, months: filterMonths(cal.months, body.start, body.end) };
  }
  return (await post("/api/calendar", body, signal)).json() as Promise<CalendarResponse>;
}

export async function getSummary(body: RequestBody, signal?: AbortSignal): Promise<SummaryResponse> {
  if (USING_MOCK) {
    logMock("POST /api/summary", body);
    return mock.summary as SummaryResponse;
  }
  return (await post("/api/summary", body, signal)).json() as Promise<SummaryResponse>;
}

/**
 * Fetches the hotspots CSV and saves it as a file. It's a POST, so a plain
 * <a href> can't do it: we fetch, wrap the text in a Blob and click a temporary link.
 * `full` asks for every field the API has (`?full=true`: pixel size, brightness,
 * the weight's 90% range, whether the row counts, ...) instead of the contract's 11 columns.
 */
export async function downloadCsv(body: RequestBody, filename = "fireledger-hotspots.csv", full = false): Promise<void> {
  let blob: Blob;
  if (USING_MOCK) {
    logMock("POST /api/hotspots.csv", body);
    blob = new Blob([mock.hotspots_csv_sample], { type: "text/csv" });
  } else {
    blob = await (await post(`/api/hotspots.csv${full ? "?full=true" : ""}`, body)).blob();
  }
  saveBlob(blob, filename);
}

/** POST /api/daily: the last 30 days with data, each against the same day in earlier years */
export type DailyResponse = {
  window: { start: string; end: string; days: number };
  days: {
    date: string;
    /** null when no satellite observed that day */
    count: number | null;
    raw_modis: number;
    raw_viirs: number;
    median: number;
    p25: number;
    p75: number;
    n_years: number;
    /** null when the median is 0 */
    pct_diff: number | null;
    preliminary?: boolean;
  }[];
  last_7_days: DailySummary;
  last_30_days: DailySummary;
};
/** rank 1 = the most fire of all years */
export type DailySummary = { count: number; median: number; pct_diff: number; rank: number; of_years: number };

export async function getDaily(body: RequestBody, signal?: AbortSignal): Promise<DailyResponse | null> {
  if (USING_MOCK) return null;
  return (await post("/api/daily", body, signal)).json() as Promise<DailyResponse>;
}

/** POST /api/people: people living in 0.25° cells by how many fires each cell had (WorldPop 2020) */
export type PeopleTier = {
  min_fires: number;
  people: number;
  cells: number;
  share_of_area: number;
  median_people: number;
  pct_diff: number;
  rank: number;
  of_years: number;
};
export type PeopleWindow = { from: string; to: string; headline: PeopleTier; tiers: PeopleTier[] };
export type PeopleResponse = {
  area_population: number;
  population_source: string;
  note: string;
  last_30_days?: PeopleWindow;
  last_12_months?: PeopleWindow;
};

export async function getPeople(body: RequestBody, signal?: AbortSignal): Promise<PeopleResponse | null> {
  if (USING_MOCK) return null;
  return (await post("/api/people", body, signal)).json() as Promise<PeopleResponse>;
}

export async function getPresets(signal?: AbortSignal): Promise<Preset[]> {
  if (USING_MOCK) {
    logMock("GET /api/presets");
    return mock.presets as Preset[];
  }
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/presets`, { signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(`Can't reach the FireLedger API at ${API_URL}.`);
  }
  if (!res.ok) throw new ApiError(await errorMessage(res, "/api/presets"));
  return res.json();
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(`Can't reach the FireLedger API at ${API_URL}.`);
  }
  if (!res.ok) throw new ApiError(await errorMessage(res, path.split("?")[0]));
  return res.json() as Promise<T>;
}

/**
 * Fire layer for the map. The mock has no grid data, so this returns null
 * rather than inventing cells.
 */
export async function getGrid(q: GridQuery, signal?: AbortSignal): Promise<GridResponse | null> {
  if (USING_MOCK) return null;
  const params = new URLSearchParams({
    year: String(q.year),
    month: String(q.month),
    unit: q.unit,
    daynight: q.daynight,
    hide_industrial: String(q.hide_industrial),
    res: String(q.res),
  });
  if (q.bbox) params.set("bbox", q.bbox.join(","));
  return getJson<GridResponse>(`/api/grid?${params}`, signal);
}

/**
 * The detections that count (weight > 0) in an area, for the map's close-up view.
 * `?full=true` adds each pixel's size. Rows that count 0 are left out: S-NPP on
 * days NOAA-20 also observed, and MODIS once VIIRS is counted, so a fire seen by
 * two satellites is drawn once. The mock has no real detections: returns null.
 */
export async function getCountedHotspots(body: RequestBody, signal?: AbortSignal): Promise<Hotspot[] | null> {
  if (USING_MOCK) return null;
  const text = await (await post("/api/hotspots.csv?full=true", body, signal)).text();
  const lines = text.split(/\r?\n/);
  const head = csvCells(lines[0] ?? "");
  const at = (name: string) => head.indexOf(name);
  const c = {
    lat: at("latitude"), lon: at("longitude"), date: at("acq_date"), time: at("acq_time"),
    sat: at("satellite"), dn: at("daynight"), frp: at("frp"), conf: at("confidence"),
    scan: at("scan"), track: at("track"), weight: at("weight"),
  };
  const out: Hotspot[] = [];
  for (let i = 1; i < lines.length; i++) {
    if (!lines[i]) continue;
    const r = csvCells(lines[i]);
    const weight = Number(r[c.weight]);
    if (!(weight > 0)) continue;
    out.push({
      lat: Number(r[c.lat]),
      lon: Number(r[c.lon]),
      date: r[c.date],
      time: r[c.time],
      satellite: r[c.sat],
      daynight: r[c.dn],
      frp: Number(r[c.frp]),
      confidence: r[c.conf],
      scan: Number(r[c.scan]),
      track: Number(r[c.track]),
      weight,
    });
  }
  return out;
}

/** One CSV line into cells; handles "quoted, cells" (e.g. a country name with a comma). */
function csvCells(line: string): string[] {
  if (!line.includes('"')) return line.split(",");
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (quoted) {
      if (ch !== '"') {
        cur += ch;
      } else if (line[i + 1] === '"') {
        cur += '"';
        i++;
      } else {
        quoted = false;
      }
    } else if (ch === '"') {
      quoted = true;
    } else if (ch === ",") {
      out.push(cur);
      cur = "";
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

// The proof doesn't change while the app is open: fetch once, share the answer.
let proofOnce: Promise<ProofResponse> | null = null;

/** Validation numbers. The mock has none, so this returns null rather than inventing them. */
export async function getProof(): Promise<ProofResponse | null> {
  if (USING_MOCK) return null;
  proofOnce ??= getJson<ProofResponse>("/api/proof").catch((e) => {
    proofOnce = null; // let the next caller retry
    throw e;
  });
  return proofOnce;
}

export async function getHealth(signal?: AbortSignal): Promise<HealthResponse | null> {
  if (USING_MOCK) return null;
  return getJson<HealthResponse>("/api/health", signal);
}

let healthOnce: Promise<HealthResponse | null> | null = null;

async function serverMode(): Promise<string | undefined> {
  healthOnce ??= getHealth().catch(() => {
    healthOnce = null;
    return null;
  });
  return (await healthOnce)?.mode;
}

/** true when areas are counted from 0.25° cells (the public server), so small areas' edges are approximate */
export async function isGridOnly(): Promise<boolean> {
  const m = await serverMode();
  return m === "grid-only" || m === "grid+presets";
}

/** where the server has single hotspots (CSV, close-up dots): the full server everywhere, the public one in the presets */
export type HotspotsWhere = "everywhere" | "presets" | "none";
export async function hotspotsWhere(): Promise<HotspotsWhere> {
  const m = await serverMode();
  return m === "grid-only" ? "none" : m === "grid+presets" ? "presets" : "everywhere";
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
