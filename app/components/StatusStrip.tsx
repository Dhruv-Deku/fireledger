"use client";

import { USING_MOCK } from "@/lib/api";
import type { Cursor } from "./GlobeMap";

const fmt = (v: number, pos: string, neg: string) => `${Math.abs(v).toFixed(3)}° ${v >= 0 ? pos : neg}`;

/**
 * Small readouts floating on the map: the cursor's position bottom-right (like
 * Zoom Earth), and a sample-data notice top-centre while the API isn't connected.
 */
export default function StatusStrip({ cursor }: { cursor: Cursor | null }) {
  return (
    <>
      {cursor && (
        <p
          aria-hidden
          className="num pointer-events-none absolute bottom-3 right-12 z-10 hidden rounded-md bg-fl-ctrl-bg px-2 py-1 text-[11px] text-fl-muted sm:block"
        >
          {fmt(cursor.lat, "N", "S")}&nbsp;&nbsp;{fmt(cursor.lng, "E", "W")}
        </p>
      )}
      {USING_MOCK && (
        <p
          role="status"
          className="glass pointer-events-none absolute left-1/2 top-3 z-10 hidden -translate-x-1/2 px-3 py-1.5 text-xs text-fl-text lg:block"
        >
          <span className="font-medium text-fl-accent-text">Sample data.</span> Numbers are made up until the live API is
          connected.
        </p>
      )}
    </>
  );
}
