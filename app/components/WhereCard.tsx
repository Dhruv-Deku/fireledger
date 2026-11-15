"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Circle, Hexagon, Loader2, Search, Square, X } from "lucide-react";
import { circlePolygon, type AreaOfInterest } from "@/lib/geo";
import { POINT_RADIUS_KM, searchPlaces, type PlaceResult } from "@/lib/placeSearch";
import type { DrawMode } from "./GlobeMap";

/** A place you can jump to: learned from use, or a built-in preset. */
export type PlaceItem = {
  key: string;
  area: AreaOfInterest;
  /** Learned from use (can be forgotten) vs. a built-in preset. */
  learned: boolean;
  uses?: number;
};

type Props = {
  aoi: AreaOfInterest | null;
  drawMode: DrawMode;
  onDrawMode: (m: DrawMode) => void;
  onAoi: (a: AreaOfInterest | null) => void;
  places: PlaceItem[];
  activeKey: string | null;
  onForget: (key: string) => void;
  /** Results panel hidden while an area is chosen: offer to show it again. */
  onShowResults?: () => void;
  offline: boolean;
};

const field =
  "h-9 w-full rounded-lg border border-fl-border bg-fl-subtle px-2.5 text-sm text-fl-text outline-none transition-colors placeholder:text-fl-faint hover:border-fl-faint focus-visible:border-fl-accent";

const tool =
  "flex h-9 flex-1 items-center justify-center gap-1.5 rounded-lg border text-[13px] transition-colors";

/**
 * The one place to choose an area, floating over the map's top-left corner:
 * search, draw, a circle by coordinates, and the places you use most.
 */
export default function WhereCard(p: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<PlaceResult[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [circleOpen, setCircleOpen] = useState(false);
  const [lat, setLat] = useState("30.9");
  const [lon, setLon] = useState("75.85");
  const [radius, setRadius] = useState("50");
  const [circleError, setCircleError] = useState<string | null>(null);

  // Search on Enter only: OpenStreetMap's free service doesn't allow search-as-you-type.
  async function submitSearch(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (q.length < 2 || searching) return;
    setSearching(true);
    setSearchError(null);
    try {
      const r = await searchPlaces(q);
      setResults(r);
      if (r.length === 0) setSearchError(`Nothing called “${q}” found. Add the state or country.`);
    } catch (err) {
      setResults(null);
      setSearchError((err as Error).message);
    } finally {
      setSearching(false);
    }
  }

  function pickResult(r: PlaceResult) {
    const short = r.name.split(", ").slice(0, 2).join(", ");
    p.onAoi({ geometry: r.geometry, label: r.approx ? `${POINT_RADIUS_KM} km around ${short}` : short });
    setResults(null);
    setQuery("");
  }

  function submitCircle(e: FormEvent) {
    e.preventDefault();
    const la = Number(lat), lo = Number(lon), r = Number(radius);
    if (!Number.isFinite(la) || la < -90 || la > 90) return setCircleError("Latitude must be between −90 and 90.");
    if (!Number.isFinite(lo) || lo < -180 || lo > 180) return setCircleError("Longitude must be between −180 and 180.");
    if (!Number.isFinite(r) || r < 1 || r > 500) return setCircleError("Radius must be between 1 and 500 km.");
    setCircleError(null);
    p.onAoi({ geometry: circlePolygon(la, lo, r), label: `${r} km around ${la.toFixed(2)}°, ${lo.toFixed(2)}°` });
    setCircleOpen(false);
  }

  const toggleDraw = (m: Exclude<DrawMode, null>) => {
    setCircleOpen(false);
    p.onDrawMode(p.drawMode === m ? null : m);
  };
  const toolClass = (on: boolean) =>
    `${tool} ${on ? "border-fl-accent bg-fl-accent/15 text-fl-accent-text" : "border-fl-border text-fl-text hover:border-fl-faint"}`;

  return (
    <section aria-label="Choose an area" data-where-card className="glass flex flex-col gap-3 p-3 shadow-none">
      <form onSubmit={submitSearch} role="search" className="relative">
        <label htmlFor="place-search" className="sr-only">
          Search a place
        </label>
        <input
          id="place-search"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search a district, state or town"
          className={`${field} h-10 pr-10 text-[15px]`}
          autoComplete="off"
        />
        <button
          type="submit"
          aria-label="Search"
          className="absolute inset-y-0 right-0 grid w-10 place-items-center text-fl-muted hover:text-fl-text"
        >
          {searching ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Search className="size-4" aria-hidden />}
        </button>
      </form>

      {searchError && <p className="text-[13px] text-fl-accent-text" role="alert">{searchError}</p>}
      {results && results.length > 0 && (
        <ul className="-mt-1 flex flex-col overflow-hidden rounded-lg border border-fl-border" aria-label="Search results">
          {results.map((r, i) => (
            <li key={i} className="border-t border-fl-border first:border-t-0">
              <button
                type="button"
                onClick={() => pickResult(r)}
                className="flex w-full flex-col items-start gap-0.5 px-3 py-2 text-left transition-colors hover:bg-fl-accent/10"
              >
                <span className="line-clamp-2 text-sm leading-snug text-fl-text">{r.name}</span>
                <span className="text-xs text-fl-muted">
                  {r.approx ? `${r.kind}, ${POINT_RADIUS_KM} km circle (no boundary in OpenStreetMap)` : `${r.kind}, its boundary`}
                </span>
              </button>
            </li>
          ))}
          <li className="border-t border-fl-border px-3 py-1.5 text-[11px] text-fl-faint">Search by OpenStreetMap</li>
        </ul>
      )}

      <div className="flex gap-2" role="group" aria-label="Draw an area">
        <button type="button" onClick={() => toggleDraw("polygon")} aria-pressed={p.drawMode === "polygon"} className={toolClass(p.drawMode === "polygon")}>
          <Hexagon className="size-4" aria-hidden /> Shape
        </button>
        <button type="button" onClick={() => toggleDraw("rectangle")} aria-pressed={p.drawMode === "rectangle"} className={toolClass(p.drawMode === "rectangle")}>
          <Square className="size-4" aria-hidden /> Box
        </button>
        <button
          type="button"
          onClick={() => {
            p.onDrawMode(null);
            setCircleOpen((v) => !v);
          }}
          aria-expanded={circleOpen}
          className={toolClass(circleOpen)}
        >
          <Circle className="size-4" aria-hidden /> Circle
        </button>
      </div>

      {p.drawMode && (
        <p className="text-[13px] leading-snug text-fl-text" role="status">
          {p.drawMode === "polygon"
            ? "Click to add corners on the map. Click the first corner to finish."
            : "Click one corner on the map, then the opposite corner."}
        </p>
      )}

      {circleOpen && (
        <form onSubmit={submitCircle} className="flex flex-col gap-2" noValidate aria-label="Circle by coordinates">
          <div className="grid grid-cols-3 gap-2">
            <NumField label="Latitude" value={lat} onChange={setLat} />
            <NumField label="Longitude" value={lon} onChange={setLon} />
            <NumField label="Radius, km" value={radius} onChange={setRadius} />
          </div>
          {circleError && <p className="text-[13px] text-fl-accent-text" role="alert">{circleError}</p>}
          <button type="submit" className="h-9 rounded-lg bg-fl-text text-[13px] font-medium text-fl-bg transition-opacity hover:opacity-90">
            Use this circle
          </button>
        </form>
      )}

      {p.aoi && (
        <div className="flex items-center gap-2 border-t border-fl-border pt-3">
          <span aria-hidden className="size-3 shrink-0 rounded-[3px] border-2 border-fl-accent bg-fl-accent/20" />
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-fl-text" title={p.aoi.label}>
            {p.aoi.label}
          </span>
          {p.onShowResults && (
            <button type="button" onClick={p.onShowResults} className="shrink-0 text-[13px] text-fl-accent-text hover:underline">
              Show results
            </button>
          )}
          <button
            type="button"
            onClick={() => p.onAoi(null)}
            aria-label="Clear area"
            className="shrink-0 rounded p-1 text-fl-muted transition-colors hover:text-fl-text"
          >
            <X className="size-4" />
          </button>
        </div>
      )}

      <PlaceChips places={p.places} activeKey={p.activeKey} onPick={(a) => p.onAoi(a)} onForget={p.onForget} />

      {p.offline && (
        <p className="text-xs text-fl-muted">Offline: NASA imagery is missing. Drawing still works.</p>
      )}
    </section>
  );
}

function NumField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex flex-col gap-1 text-xs text-fl-muted">
      {label}
      <input className={`${field} num`} inputMode="decimal" value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

/** Places you use most, then presets, as one scrolling row of chips. */
function PlaceChips({
  places,
  activeKey,
  onPick,
  onForget,
}: {
  places: PlaceItem[];
  activeKey: string | null;
  onPick: (a: AreaOfInterest) => void;
  onForget: (key: string) => void;
}) {
  const rowRef = useRef<HTMLDivElement>(null);
  // Keep the chosen place in view.
  useEffect(() => {
    if (!activeKey) return;
    const el = rowRef.current?.querySelector<HTMLElement>(`[data-key="${CSS.escape(activeKey)}"]`);
    el?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [activeKey]);

  if (places.length === 0) return null;
  return (
    <div
      ref={rowRef}
      role="group"
      aria-label="Places"
      // A mouse wheel scrolls the row sideways; the scrollbar stays hidden.
      onWheel={(e) => {
        if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) e.currentTarget.scrollLeft += e.deltaY;
      }}
      className="-mx-3 flex gap-1.5 overflow-x-auto px-3 [mask-image:linear-gradient(to_right,transparent,black_12px,black_calc(100%-24px),transparent)] [scrollbar-width:none]"
    >
      {places.map((pl) => {
        const on = pl.key === activeKey;
        return (
          <span
            key={pl.key}
            data-key={pl.key}
            className={`group flex shrink-0 items-center rounded-full border text-[13px] transition-colors ${
              on ? "border-fl-accent/70 text-fl-text" : "border-fl-border text-fl-muted hover:text-fl-text"
            }`}
          >
            <button
              type="button"
              onClick={() => onPick(pl.area)}
              aria-pressed={on}
              className="h-8 max-w-[200px] truncate px-3"
              title={pl.learned ? `Used ${pl.uses} time${pl.uses === 1 ? "" : "s"}` : "Preset region"}
            >
              {pl.area.label}
            </button>
            {pl.learned && !on && (
              <button
                type="button"
                onClick={() => onForget(pl.key)}
                aria-label={`Forget ${pl.area.label}`}
                className="-ml-2 mr-1.5 rounded-full p-0.5 text-fl-faint opacity-0 transition-opacity hover:text-fl-text focus-visible:opacity-100 group-hover:opacity-100"
              >
                <X className="size-3.5" />
              </button>
            )}
          </span>
        );
      })}
    </div>
  );
}
