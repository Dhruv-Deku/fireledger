"use client";

import { useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Pause, Play } from "lucide-react";
import type { MonthRow } from "@/lib/api";
import { MONTHS, fmtInt, themeColors } from "@/lib/chartTheme";
import { useTheme } from "@/lib/theme";
import type { Period } from "./FireLayerControl";

type Props = {
  /** The chosen area's monthly record (harmonized or raw), or null with no area. */
  months: MonthRow[] | null;
  loading: boolean;
  areaLabel: string | null;
  /** Month shown on the map. */
  period: Period;
  min: Period;
  max: Period;
  /** Day the latest month runs to, if it's still in progress. */
  latestThroughDay?: number;
  onPeriod: (p: Period) => void;
  playing: boolean;
  onPlay: () => void;
  /** the area's "2012 switch" verdict from the api; fair or caution tint the years the test compares */
  switchVerdict?: string | null;
};

const idx = (p: Period) => p.year * 12 + p.month - 1;
const fromIdx = (i: number): Period => ({ year: Math.floor(i / 12), month: (i % 12) + 1 });
const VIIRS_FROM = { year: 2012, month: 1 };

/**
 * The fire record as a ribbon along the bottom of the map: one bar per month
 * since Nov 2000, height and colour by hotspot count (same scale as the
 * calendar). It is also the clock: click, drag or use the arrow keys to pick
 * the month the map shows, or press play to run through the years.
 */
export default function FireTimeline(p: Props) {
  const { theme } = useTheme();
  const i0 = idx(p.min);
  const n = idx(p.max) - i0 + 1;
  const cur = idx(p.period) - i0;
  const [hover, setHover] = useState<number | null>(null);
  const dragging = useRef(false);
  const barsRef = useRef<HTMLDivElement>(null);

  const { counts, max, ramp, track } = useMemo(() => {
    void theme; // colours are read from CSS: recompute when the theme flips
    const C = themeColors();
    const counts = new Array<MonthRow | undefined>(n);
    let max = 0;
    for (const m of p.months ?? []) {
      const k = idx(m) - i0;
      if (k < 0 || k >= n) continue;
      counts[k] = m;
      if (m.count > max) max = m.count;
    }
    return { counts, max, ramp: C.inferno, track: C.zero };
  }, [p.months, i0, n, theme]);

  // Colour: linear, like the calendar. Height: square root, so quiet months
  // still show next to a record month (exact numbers are in the readout).
  const colour = (c: number) => ramp[Math.min(ramp.length - 1, Math.floor((c / (max || 1)) * ramp.length))];
  const height = (c: number) => Math.max(4, Math.sqrt(c / (max || 1)) * 100);

  const pick = (k: number) => p.onPeriod(fromIdx(i0 + Math.max(0, Math.min(n - 1, k))));
  const at = (e: PointerEvent) => {
    const r = barsRef.current!.getBoundingClientRect();
    return Math.floor(((e.clientX - r.left) / r.width) * n);
  };
  function onKey(e: KeyboardEvent) {
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowDown: -1, ArrowUp: 1, PageDown: -12, PageUp: 12 }[e.key];
    if (step) pick(cur + step);
    else if (e.key === "Home") pick(0);
    else if (e.key === "End") pick(n - 1);
    else return;
    e.preventDefault();
  }

  const shown = hover ?? cur;
  const shownP = fromIdx(i0 + shown);
  const shownRow = counts[shown];
  const isLatest = shown === n - 1;
  const monthText = `${MONTHS[shownP.month - 1]}${isLatest && p.latestThroughDay ? ` 1–${p.latestThroughDay}` : ""} ${shownP.year}`;
  const pos = (k: number) => `${((k + 0.5) / n) * 100}%`;
  const years: number[] = [];
  // Every five years, plus 2012 where VIIRS joins (2010 would collide with it).
  for (let y = p.min.year + 1; y <= p.max.year; y++) if ((y % 5 === 0 && y !== 2010) || y === 2012) years.push(y);
  const viirsAt = idx(VIIRS_FROM) - i0;
  const weakSwitch = p.switchVerdict === "fair" || p.switchVerdict === "caution";

  return (
    <div className="flex h-[84px] shrink-0 items-stretch gap-4 border-t border-fl-border bg-fl-surface px-3 sm:px-4">
      <div className="flex w-[148px] shrink-0 items-center gap-3 sm:w-[196px]">
        <button
          type="button"
          onClick={p.onPlay}
          aria-label={p.playing ? "Pause" : "Play through the months"}
          className="grid size-9 shrink-0 place-items-center rounded-full border border-fl-border text-fl-text transition-colors hover:border-fl-faint"
        >
          {p.playing ? <Pause className="size-4" /> : <Play className="size-4 translate-x-px" />}
        </button>
        <div className="flex min-w-0 flex-col" aria-live="polite">
          <span className="num text-[15px] leading-tight text-fl-text">{monthText}</span>
          <span className="truncate text-xs leading-snug text-fl-muted">
            {p.loading
              ? "Loading the record…"
              : shownRow
                ? `${fmtInt(shownRow.count)} hotspots${p.areaLabel ? ` in ${p.areaLabel}` : ""}`
                : p.months
                  ? "Outside your dates"
                  : "Pick an area to see its record"}
          </span>
        </div>
      </div>

      <div className="relative min-w-0 flex-1 pb-5 pt-3">
        <div
          ref={barsRef}
          role="slider"
          tabIndex={0}
          aria-label="Month shown on the map"
          aria-valuemin={0}
          aria-valuemax={n - 1}
          aria-valuenow={cur}
          aria-valuetext={monthText}
          onKeyDown={onKey}
          onPointerDown={(e) => {
            dragging.current = true;
            e.currentTarget.setPointerCapture(e.pointerId);
            pick(at(e));
          }}
          onPointerMove={(e) => {
            const k = Math.max(0, Math.min(n - 1, at(e)));
            if (dragging.current) pick(k);
            else setHover(k);
          }}
          onPointerUp={() => (dragging.current = false)}
          onPointerLeave={() => setHover(null)}
          className="relative h-full cursor-pointer touch-none rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-fl-accent"
        >
          {/* where the 2012 test says the switch isn't clean here, tint the 2008-15 years it compares */}
          {weakSwitch && (
            <span
              aria-hidden
              className="absolute inset-y-0 rounded-sm"
              style={{
                left: `${(Math.max(0, idx({ year: 2008, month: 1 }) - i0) / n) * 100}%`,
                width: `${(96 / n) * 100}%`,
                background:
                  p.switchVerdict === "caution"
                    ? "color-mix(in srgb, var(--fl-anom-4) 16%, transparent)"
                    : "color-mix(in srgb, var(--fl-muted) 12%, transparent)",
              }}
            />
          )}
          {/* The sensor switch: VIIRS joins in January 2012. */}
          {viirsAt > 0 && viirsAt < n && (
            <span aria-hidden className="absolute inset-y-0 w-px bg-fl-faint/40" style={{ left: `${(viirsAt / n) * 100}%` }} />
          )}
          <svg className="absolute inset-0 size-full" viewBox={`0 0 ${n} 100`} preserveAspectRatio="none" aria-hidden>
            {p.months
              ? counts.map((m, k) =>
                  m ? (
                    m.count > 0 ? (
                      <rect
                        key={k}
                        x={k + 0.08}
                        width={0.84}
                        y={100 - height(m.count)}
                        height={height(m.count)}
                        fill={colour(m.count)}
                        opacity={m.partial ? 0.5 : 1}
                      />
                    ) : (
                      <rect key={k} x={k + 0.08} width={0.84} y={97} height={3} fill={track} />
                    )
                  ) : null,
                )
              : // No area yet: a quiet baseline so the clock still reads as a clock.
                <rect x={0} width={n} y={99} height={1} fill={track} />}
          </svg>
          {p.loading && <span aria-hidden className="absolute inset-0 animate-pulse rounded-sm bg-fl-subtle" />}
          {hover !== null && hover !== cur && (
            <span aria-hidden className="absolute inset-y-0 w-px bg-fl-muted/70" style={{ left: pos(hover) }} />
          )}
          <span aria-hidden className="absolute -inset-y-1 w-0.5 -translate-x-1/2 rounded-full bg-fl-accent" style={{ left: pos(cur) }}>
            <span className="absolute -top-1 left-1/2 size-2 -translate-x-1/2 rounded-full bg-fl-accent" />
          </span>
        </div>
        <div aria-hidden className="num pointer-events-none absolute inset-x-0 bottom-0 h-4 text-[11px] text-fl-faint">
          {years.map((y) => (
            <span
              key={y}
              className={`absolute -translate-x-1/2 ${y === 2012 ? "text-fl-muted" : ""} ${y === 2015 ? "hidden sm:inline" : ""}`}
              style={{ left: `${((idx({ year: y, month: 1 }) - i0) / n) * 100}%` }}
            >
              {y === 2012 ? (weakSwitch ? `2012 VIIRS (${p.switchVerdict})` : "2012 VIIRS") : y}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
