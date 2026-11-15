"use client";

import type { DayNight, Unit } from "@/lib/api";

export type Filters = {
  start: string;
  end: string;
  unit: Unit;
  daynight: DayNight;
  hide_industrial: boolean;
  harmonized: boolean;
};

/** First month in the record: Terra MODIS starts Nov 2000. */
export const RECORD_START = "2000-11-01";

type Props = {
  value: Filters;
  maxDate: string;
  onChange: (f: Filters) => void;
};

const dateField =
  "num h-9 w-full rounded-md border border-fl-border bg-transparent px-2 text-[13px] text-fl-text outline-none transition-colors hover:border-fl-faint focus-visible:border-fl-accent";

export default function FiltersPanel({ value, maxDate, onChange }: Props) {
  const set = <K extends keyof Filters>(k: K, v: Filters[K]) => onChange({ ...value, [k]: v });

  function setDate(k: "start" | "end", v: string) {
    if (!v) return; // ignore a cleared date input
    const next = { ...value, [k]: v };
    // Keep start <= end by moving the other end along.
    if (next.start > next.end) {
      if (k === "start") next.end = v;
      else next.start = v;
    }
    onChange(next);
  }

  return (
    <section className="flex flex-col gap-4" aria-label="Filters">

      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-xs text-fl-muted">Dates</legend>
        <div className="grid grid-cols-2 gap-2">
          <input
            type="date"
            aria-label="From date"
            className={dateField}
            min={RECORD_START}
            max={maxDate}
            value={value.start}
            onChange={(e) => setDate("start", e.target.value)}
          />
          <input
            type="date"
            aria-label="To date"
            className={dateField}
            min={RECORD_START}
            max={maxDate}
            value={value.end}
            onChange={(e) => setDate("end", e.target.value)}
          />
        </div>
      </fieldset>

      <Segmented
        label="Units"
        value={value.unit}
        onChange={(v) => set("unit", v)}
        options={[
          { value: "viirs", label: "VIIRS-equivalent" },
          { value: "modis", label: "MODIS-equivalent" },
        ]}
      />

      <Segmented
        label="Day or night"
        value={value.daynight}
        onChange={(v) => set("daynight", v)}
        options={[
          { value: "both", label: "Both" },
          { value: "D", label: "Day" },
          { value: "N", label: "Night" },
        ]}
      />

      <Segmented
        // The before/after switch (docs/API_FOR_APP.md): raw shows the record as the
        // satellites left it, with the fake 2012 jump; harmonized removes it.
        label="Before / after"
        value={value.harmonized ? "harmonized" : "raw"}
        onChange={(v) => set("harmonized", v === "harmonized")}
        options={[
          { value: "raw", label: "Before: raw" },
          { value: "harmonized", label: "After: harmonized" },
        ]}
      />

      <Switch
        label="Hide industrial hotspots"
        hint="Gas flares and factories that burn all year"
        checked={value.hide_industrial}
        onChange={(v) => set("hide_industrial", v)}
      />
    </section>
  );
}

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5" role="radiogroup" aria-label={label}>
      <span className="text-xs text-fl-muted">{label}</span>
      <div className="flex gap-px overflow-hidden rounded-md border border-fl-border bg-fl-border">
        {options.map((o) => {
          const on = o.value === value;
          return (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => onChange(o.value)}
              className={`h-8 flex-1 px-2 text-xs transition-colors focus-visible:-outline-offset-2 ${
                // Neutral selection: orange is kept for the fire data and the chosen area.
                on
                  ? "bg-fl-text/[0.14] font-medium text-fl-text"
                  : "bg-fl-surface text-fl-muted hover:text-fl-text"
              }`}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Switch({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex items-center justify-between gap-3 rounded-md text-left"
    >
      <span className="flex flex-col">
        <span className="text-[13px] text-fl-text">{label}</span>
        {hint && <span className="text-xs text-fl-muted">{hint}</span>}
      </span>
      <span
        aria-hidden
        className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${
          checked ? "bg-fl-accent" : "bg-fl-track"
        }`}
      >
        <span
          className={`absolute top-0.5 size-4 rounded-full transition-transform ${
            checked ? "translate-x-4 bg-fl-on-accent" : "translate-x-0.5 bg-fl-muted"
          }`}
        />
      </span>
    </button>
  );
}
