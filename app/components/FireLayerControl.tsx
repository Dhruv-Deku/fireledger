"use client";

import { Eye, EyeOff, Loader2 } from "lucide-react";
import { INFERNO, fmtInt } from "@/lib/chartTheme";

export type Period = { year: number; month: number };

type Props = {
  on: boolean;
  onToggle: () => void;
  loading: boolean;
  error: string | null;
  /** Largest cell count in the shown month (top of the legend). */
  maxCount: number | null;
  /** Cell size of the shown grid, in degrees (from the API's answer). */
  cellRes: number;
  /** Set when single detections replace the cells (close zoom): how many, and their weight range. */
  dots: { count: number; lo: number; hi: number } | null;
  /** Why single detections aren't shown although zoomed in close, if they aren't. */
  note: string | null;
  unitText: string;
};

/** Same as the map: one colour when every detection counts the same (INFERNO's middle stop). */
const UNIFORM = INFERNO[(INFERNO.length - 1) / 2];
const fmtW = (w: number) => (w >= 10 ? w.toFixed(0) : w.toFixed(1));

/**
 * Legend for the fire layer, bottom-left of the map, with a show/hide toggle.
 * The month is picked on the timeline under the map.
 */
export default function FireLayerControl(p: Props) {
  const mid = p.maxCount ? Math.round(Math.sqrt(p.maxCount)) : null; // middle of a log scale from 1

  return (
    <div className="glass absolute bottom-3 left-3 z-10 w-[200px] p-3 text-xs text-fl-muted sm:w-[232px]">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-[13px] font-medium text-fl-text">
          Fire layer
          {p.loading && <Loader2 className="size-3.5 animate-spin text-fl-muted" aria-label="Loading" />}
        </span>
        <button
          type="button"
          onClick={p.onToggle}
          aria-pressed={p.on}
          aria-label={p.on ? "Hide fire layer" : "Show fire layer"}
          className="rounded p-1 text-fl-muted transition-colors hover:text-fl-text"
        >
          {p.on ? <Eye className="size-4" /> : <EyeOff className="size-4" />}
        </button>
      </div>

      {p.on &&
        (p.error ? (
          <p className="mt-2 text-fl-accent-text" role="alert">{p.error}</p>
        ) : p.dots ? (
          <div className="mt-2">
            {p.dots.count === 0 ? (
              <p className="leading-snug">No detections here in this month.</p>
            ) : p.dots.hi / p.dots.lo > 1.05 ? (
              <>
                <div className="h-2 rounded-sm" style={{ background: `linear-gradient(to right, ${INFERNO.join(", ")})` }} aria-hidden />
                <div className="num mt-1 flex justify-between text-[10px]">
                  <span>{fmtW(p.dots.lo)}</span>
                  <span>{fmtW(p.dots.hi)}</span>
                </div>
              </>
            ) : (
              <div className="flex items-center gap-2">
                <span className="size-3 rounded-sm" style={{ background: UNIFORM }} aria-hidden />
                <span className="num text-[11px]">each counts as {fmtW(p.dots.lo)}</span>
              </div>
            )}
            {p.dots.count > 0 && (
              <p className="mt-1.5 leading-snug">
                <span className="num text-fl-text">{fmtInt(p.dots.count)}</span> detections, each at its pixel size.
                Colour: hotspots it counts as, {p.unitText}. Click one for details.
              </p>
            )}
          </div>
        ) : (
          <div className="mt-2">
            <div className="h-2 rounded-sm" style={{ background: `linear-gradient(to right, ${INFERNO.join(", ")})` }} aria-hidden />
            <div className="num mt-1 flex justify-between text-[10px]">
              <span>1</span>
              <span>{mid ? fmtInt(mid) : ""}</span>
              <span>{p.maxCount ? fmtInt(p.maxCount) : "–"}</span>
            </div>
            <p className="mt-1.5 hidden leading-snug sm:block">
              Hotspots per {p.cellRes}° cell (about {Math.round(p.cellRes * 111)} km), {p.unitText}.
              {p.note ? "" : " Zoom in close for single detections."}
            </p>
            {p.note && <p className="mt-1.5 leading-snug text-fl-accent-text">{p.note}</p>}
          </div>
        ))}
    </div>
  );
}
