"use client";

import { OWN_API_URL, USING_MOCK } from "@/lib/api";
import type { Cursor } from "./GlobeMap";

const fmt = (v: number, pos: string, neg: string) => `${Math.abs(v).toFixed(3)}° ${v >= 0 ? pos : neg}`;

/**
 * Small readouts floating on the map: the cursor's position bottom-right (like
 * Zoom Earth), and a notice top-centre while the data is fake (API not connected)
 * or comes from the viewer's own API (?api=, run_local.py).
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
      {OWN_API_URL && (
        <p
          role="status"
          className="glass absolute left-1/2 top-3 z-10 hidden -translate-x-1/2 px-3 py-1.5 text-xs text-fl-text lg:block"
        >
          <span className="font-medium text-fl-accent-text">Your API:</span> {OWN_API_URL} ·{" "}
          <a href="?api=default" className="underline underline-offset-2 hover:text-fl-accent-text">
            use the site&apos;s
          </a>
        </p>
      )}
    </>
  );
}
