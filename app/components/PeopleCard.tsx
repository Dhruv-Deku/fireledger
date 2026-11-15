"use client";

import { useEffect, useState } from "react";
import { getPeople, type PeopleResponse, type PeopleWindow, type RequestBody } from "@/lib/api";
import { fmtInt } from "@/lib/chartTheme";

const ordinal = (n: number) => {
  const s = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${s}`;
};

/** 6,527,551 -> "6.5 million"; small numbers stay whole */
const people = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)} million` : n >= 1e4 ? `${Math.round(n / 1e3)} thousand` : fmtInt(n);

function Row({ label, w }: { label: string; w: PeopleWindow }) {
  const h = w.headline;
  const p = Math.round(h.pct_diff);
  const vs = Math.abs(p) < 5 ? "about normal" : `${Math.abs(p)}% ${p > 0 ? "more" : "fewer"} than normal`;
  return (
    <div className="flex flex-col gap-0.5">
      <p className="text-[15px] text-fl-text">
        <span className="num">{people(h.people)}</span> people{" "}
        <span className="text-fl-muted">
          ({label}, cells with {fmtInt(h.min_fires)}+ fires)
        </span>
      </p>
      <p className="text-sm text-fl-muted">
        {vs} ({ordinal(h.rank)} of {h.of_years} years)
      </p>
    </div>
  );
}

/**
 * People living near fires (POST /api/people): WorldPop 2020 people in 0.25° cells
 * by how many fires each cell had. Hidden if the call fails or nobody lives there.
 */
export default function PeopleCard({ request }: { request: RequestBody }) {
  const [data, setData] = useState<PeopleResponse | null>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    getPeople(request, ctrl.signal)
      .then(setData)
      .catch(() => setData(null));
    return () => ctrl.abort();
  }, [request]);

  if (!data || !data.area_population || (!data.last_30_days && !data.last_12_months)) return null;
  return (
    <div className="flex flex-col gap-3 border-t border-fl-border pt-6">
      <div className="flex flex-col gap-0.5">
        <h3 className="text-[17px] font-medium tracking-tight text-fl-text">People near fires</h3>
        <p className="text-sm text-fl-muted">
          Of <span className="num text-fl-text">{people(data.area_population)}</span> people in this area, how many
          live where fires were frequent.
        </p>
      </div>
      {data.last_30_days && <Row label="last 30 days" w={data.last_30_days} />}
      {data.last_12_months && <Row label="last 12 months" w={data.last_12_months} />}
      <p className="text-xs leading-snug text-fl-muted">
        People living in ~28 km grid cells, from {data.population_source}. Where people live, not smoke exposure.
        Normal is the median of the same window in earlier years.
      </p>
    </div>
  );
}
