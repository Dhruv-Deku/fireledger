// Map view settings, "remember my setup", and places the app learns from use.
// Everything lives in this browser's localStorage; every access is wrapped in
// try/catch because storage can be blocked (private mode, previews).

import type { AreaOfInterest } from "@/lib/geo";

export type Projection = "auto" | "globe" | "flat";
export type Base = "physical" | "satellite" | "terrain" | "political" | "street" | "night";
export type MapView = { projection: Projection; base: Base; terrain: boolean };

export const DEFAULT_VIEW: MapView = { projection: "auto", base: "physical", terrain: false };

const KEY_SETUP = "fl-setup-v1";
const KEY_PLACES = "fl-places-v1";

function read<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage blocked: the app still works, it just won't remember */
  }
}

// ---------- Remember my setup ----------

/** What the app restores on the next visit. Filters are opaque here (owned by Workspace). */
export type Setup<F> = {
  view: MapView;
  aoi: AreaOfInterest | null;
  filters: F | null;
  /** YYYY-MM-DD the setup was saved, so an end date of "today" can roll forward. */
  savedOn?: string;
};

export function loadSetup<F>(): Setup<F> | null {
  const s = read<Setup<F>>(KEY_SETUP);
  if (!s || typeof s !== "object") return null;
  return {
    view: { ...DEFAULT_VIEW, ...(s.view ?? {}) },
    aoi: s.aoi ?? null,
    filters: s.filters ?? null,
    savedOn: s.savedOn,
  };
}

export function saveSetup<F>(setup: Setup<F>) {
  write(KEY_SETUP, setup);
}

// ---------- Learned places ----------

/**
 * Areas the user has looked at, with how often and how recently. Ranking by
 * use is the app's "learning": the places strip offers the areas you actually
 * work on first. No data leaves the browser.
 */
export type LearnedPlace = AreaOfInterest & { key: string; uses: number; lastUsed: number };

const MAX_PLACES = 12;

/** Stable key for an area: its label plus a rounded first vertex. */
export function placeKey(a: AreaOfInterest) {
  const [x, y] = a.geometry.coordinates[0][0];
  return `${a.label}|${x.toFixed(2)},${y.toFixed(2)}`;
}

export function loadPlaces(): LearnedPlace[] {
  const list = read<LearnedPlace[]>(KEY_PLACES);
  return Array.isArray(list) ? list : [];
}

/** Record one use of an area and return the updated list. */
export function learnPlace(a: AreaOfInterest, now = Date.now()): LearnedPlace[] {
  const key = placeKey(a);
  const list = loadPlaces();
  const hit = list.find((p) => p.key === key);
  if (hit) {
    hit.uses += 1;
    hit.lastUsed = now;
  } else {
    list.push({ ...a, key, uses: 1, lastUsed: now });
  }
  const ranked = rankPlaces(list).slice(0, MAX_PLACES);
  write(KEY_PLACES, ranked);
  return ranked;
}

export function forgetPlace(key: string): LearnedPlace[] {
  const list = loadPlaces().filter((p) => p.key !== key);
  write(KEY_PLACES, list);
  return list;
}

/**
 * Most-used first; recency breaks ties and slowly outweighs old habits
 * (a use counts half as much after 30 days).
 */
export function rankPlaces(list: LearnedPlace[], now = Date.now()): LearnedPlace[] {
  const HALF_LIFE = 30 * 24 * 3600 * 1000;
  const score = (p: LearnedPlace) => p.uses * Math.pow(0.5, (now - p.lastUsed) / HALF_LIFE);
  return [...list].sort((a, b) => score(b) - score(a) || b.lastUsed - a.lastUsed);
}
