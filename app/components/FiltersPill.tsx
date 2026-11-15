"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, SlidersHorizontal } from "lucide-react";
import { MONTHS } from "@/lib/chartTheme";
import FiltersPanel, { RECORD_START, type Filters } from "./FiltersPanel";

const DAYNIGHT = { both: "day and night", D: "day only", N: "night only" } as const;
/** "2010-03-01" -> "Mar 2010" */
const monthYear = (d: string) => `${MONTHS[Number(d.slice(5, 7)) - 1]} ${d.slice(0, 4)}`;

/** The filters in words, e.g. "VIIRS units, day and night, 2000 to today". */
export function filtersSummary(f: Filters, maxDate: string) {
  const parts = [f.unit === "viirs" ? "VIIRS units" : "MODIS units", DAYNIGHT[f.daynight]];
  const from = f.start === RECORD_START ? "2000" : monthYear(f.start);
  const to = f.end === maxDate ? "today" : monthYear(f.end);
  parts.push(`${from} to ${to}`);
  if (f.hide_industrial) parts.push("no industrial");
  return parts.join(", ");
}

/**
 * Filters folded into one pill over the map. It says what's set; click to
 * change. Raw (not harmonized) is shown apart, because it changes what every
 * number means.
 */
export default function FiltersPill({
  value,
  maxDate,
  onChange,
}: {
  value: Filters;
  maxDate: string;
  onChange: (f: Filters) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on a click outside or Escape.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className="glass flex h-9 max-w-full items-center gap-2 px-3.5 text-[13px] text-fl-text transition-colors hover:border-fl-faint"
      >
        <SlidersHorizontal className="size-4 shrink-0 text-fl-muted" aria-hidden />
        {!value.harmonized && <span className="shrink-0 font-medium text-fl-accent-text">Raw counts,</span>}
        <span className="truncate">{filtersSummary(value, maxDate)}</span>
        <ChevronDown className={`size-4 shrink-0 text-fl-muted transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
      </button>
      {open && (
        <div role="dialog" aria-label="Filters" className="glass absolute left-0 top-full z-20 mt-2 w-[min(320px,calc(100vw-24px))] p-4">
          <FiltersPanel value={value} maxDate={maxDate} onChange={onChange} />
        </div>
      )}
    </div>
  );
}
