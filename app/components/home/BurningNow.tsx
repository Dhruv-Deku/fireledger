"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { getCalendar, getGrid, getHealth, USING_MOCK, type GridResponse } from "@/lib/api";
import { MONTHS, MONTHS_LONG, fmtInt } from "@/lib/chartTheme";

const HALF = 0.75; // each area is a 1.5° box around its busiest cell
const SPACING = 3; // seeds closer than this (degrees) are the same fire region
const CARDS = 6;

type Area = { lat: number; lon: number; box: [number, number, number, number] };
type Card = Area & {
  name: string | null;
  count: number | null;
  median: number | null;
  /** the api's count for the box: still loading, answered, or didn't come back */
  status: "loading" | "done" | "error";
  img: string;
  imgDate: string;
};

/**
 * pick the busiest separate areas from a month's grid: take cells from the top,
 * skip any within SPACING of one already picked, rank by the fire inside its box
 */
function busiestAreas(g: GridResponse): Area[] {
  const cells = [...g.cells].sort((a, b) => b[2] - a[2]);
  const seeds: [number, number][] = [];
  for (const [lat, lon] of cells) {
    if (seeds.length >= CARDS * 3) break;
    if (seeds.some(([a, b]) => Math.abs(a - lat) < SPACING && Math.abs(b - lon) < SPACING)) continue;
    seeds.push([lat, lon]);
  }
  const inBox = (lat: number, lon: number, [sl, so]: [number, number]) =>
    Math.abs(lat - sl) <= HALF && Math.abs(lon - so) <= HALF;
  return seeds
    .map(([lat, lon]) => ({
      lat,
      lon,
      total: g.cells.reduce((s, c) => (inBox(c[0], c[1], [lat, lon]) ? s + c[2] : s), 0),
    }))
    .sort((a, b) => b.total - a.total)
    .slice(0, CARDS)
    .map(({ lat, lon }) => ({
      lat,
      lon,
      box: [
        +(lon - HALF).toFixed(2),
        +(lat - HALF).toFixed(2),
        +(lon + HALF).toFixed(2),
        +(lat + HALF).toFixed(2),
      ] as [number, number, number, number],
    }));
}

const boxPolygon = ([w, s, e, n]: Area["box"]) => ({
  type: "Polygon" as const,
  coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]],
});

// nasa gibs: noaa-20 true colour with that day's viirs fire detections (red) on top
const gibsImage = ([w, s, e, n]: Area["box"], date: string) =>
  "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi?SERVICE=WMS&REQUEST=GetMap&VERSION=1.3.0" +
  "&LAYERS=VIIRS_NOAA20_CorrectedReflectance_TrueColor,VIIRS_NOAA20_Thermal_Anomalies_375m_All" +
  `&CRS=EPSG:4326&BBOX=${s},${w},${n},${e}&WIDTH=640&HEIGHT=640&FORMAT=image/jpeg&TIME=${date}`;

// openstreetmap's free reverse lookup allows one request a second
let lastLookup = 0;
async function placeName(lat: number, lon: number, signal: AbortSignal): Promise<string | null> {
  const wait = lastLookup + 1100 - Date.now();
  lastLookup = Math.max(Date.now(), lastLookup + 1100);
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  const r = await fetch(
    `https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=5&accept-language=en&lat=${lat}&lon=${lon}`,
    { signal },
  );
  if (!r.ok) return null;
  const j = await r.json();
  const a = j?.address ?? {};
  const region = a.state ?? a.region ?? a.province ?? a.county;
  const country = a.country;
  return [region, country].filter(Boolean).join(", ") || null;
}

/**
 * home page feed: the month's busiest fire areas, straight from the fire grid,
 * each compared with the same month in other years. loads when scrolled into view.
 */
export default function BurningNow() {
  const ref = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const [month, setMonth] = useState<{ year: number; month: number } | null>(null);
  const [cards, setCards] = useState<Card[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(([e]) => e.isIntersecting && setVisible(true), { rootMargin: "300px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  useEffect(() => {
    if (!visible || USING_MOCK) return;
    const ctrl = new AbortController();
    (async () => {
      const h = await getHealth(ctrl.signal);
      const end = h?.coverage?.end;
      if (!end) throw new Error("no coverage");
      let [y, m] = end.split("-").map(Number);
      if (Number(end.slice(8, 10)) < new Date(Date.UTC(y, m, 0)).getUTCDate()) {
        m -= 1;
        if (m === 0) [y, m] = [y - 1, 12];
      }
      setMonth({ year: y, month: m });
      const g = await getGrid({ year: y, month: m, unit: "viirs", daynight: "both", hide_industrial: false, res: 0.25 }, ctrl.signal);
      if (!g) throw new Error("no grid");
      const imgDate = `${y}-${String(m).padStart(2, "0")}-15`;
      const areas = busiestAreas(g);
      const base: Card[] = areas.map((a) => ({
        ...a,
        name: null,
        count: null,
        median: null,
        status: "loading",
        img: gibsImage(a.box, imgDate),
        imgDate,
      }));
      setCards(base);

      // the api's own count for each box, and the same month in every other full year
      const today = new Date().toISOString().slice(0, 10);
      areas.forEach((a, i) => {
        getCalendar(
          { geometry: boxPolygon(a.box), start: "2001-01-01", end: today, unit: "viirs", daynight: "both", hide_industrial: false, harmonized: true },
          ctrl.signal,
        )
          .then((cal) => {
            const same = cal.months.filter((r) => r.month === m && !r.partial);
            const now = same.find((r) => r.year === y)?.count ?? null;
            const past = same.filter((r) => r.year !== y).map((r) => r.count).sort((p, q) => p - q);
            const mid = past.length ? (past.length % 2 ? past[past.length >> 1] : (past[past.length / 2 - 1] + past[past.length / 2]) / 2) : null;
            setCards((cs) => cs && cs.map((c, k) => (k === i ? { ...c, count: now, median: mid, status: "done" } : c)));
          })
          .catch((e: Error) => {
            if (e.name === "AbortError") return;
            setCards((cs) => cs && cs.map((c, k) => (k === i ? { ...c, status: "error" } : c)));
          });
      });
      // names one by one, to keep to openstreetmap's limit
      for (let i = 0; i < areas.length; i++) {
        const name = await placeName(areas[i].lat, areas[i].lon, ctrl.signal).catch(() => null);
        if (ctrl.signal.aborted) return;
        setCards((cs) => cs && cs.map((c, k) => (k === i ? { ...c, name } : c)));
      }
    })().catch((e: Error) => {
      if (e.name !== "AbortError") setFailed(true);
    });
    return () => ctrl.abort();
  }, [visible]);

  if (USING_MOCK) return null;
  const monthText = month ? `${MONTHS_LONG[month.month - 1]} ${month.year}` : "last month";

  return (
    <section ref={ref} className="border-t border-fl-border">
      <div className="mx-auto flex max-w-6xl flex-col gap-8 px-5 py-20 sm:px-10">
        <div className="flex max-w-[42rem] flex-col gap-3">
          <h2 className="wide text-[clamp(1.7rem,3.2vw,2.6rem)] font-semibold leading-[1.05] tracking-[-0.02em] text-fl-text">
            Burning in {monthText}
          </h2>
          <p className="text-[16px] leading-relaxed text-fl-muted">
            The busiest fire areas of the last full month, ranked from the harmonized fire grid. Each one is compared
            with the same month in earlier years. Images: NASA&apos;s NOAA-20 satellite on the 15th, red dots are the
            fires it detected that day.
          </p>
        </div>

        {failed && <p className="text-sm text-fl-muted">The fire feed couldn&apos;t load. The map still works.</p>}

        <ol className="grid gap-5 md:grid-cols-2">
          {(cards ?? Array.from({ length: CARDS }, () => null)).map((c, i) => (
            <li key={i} className="flex gap-4 rounded-[20px] border border-fl-border bg-fl-surface p-3 sm:p-4">
              <div className="relative size-28 shrink-0 overflow-hidden rounded-[14px] bg-fl-track sm:size-36">
                {c && (
                  // eslint-disable-next-line @next/next/no-img-element -- remote nasa image, sized by the wms request
                  <img src={c.img} alt={`Satellite view of ${c.name ?? "this area"} on ${c.imgDate}`} className="size-full object-cover" loading="lazy" />
                )}
              </div>
              <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                <span className="num text-xs text-fl-faint">{i + 1}</span>
                {c ? (
                  <>
                    <p className="wide text-[17px] font-semibold leading-tight text-fl-text">
                      {c.name ?? `${Math.abs(c.lat).toFixed(1)}°${c.lat >= 0 ? "N" : "S"}, ${Math.abs(c.lon).toFixed(1)}°${c.lon >= 0 ? "E" : "W"}`}
                    </p>
                    <p className="text-sm text-fl-muted">
                      {c.count != null ? (
                        <>
                          <span className="num text-fl-text">{fmtInt(c.count)}</span> hotspots in{" "}
                          {month ? MONTHS[month.month - 1] : ""}, VIIRS units
                        </>
                      ) : c.status === "error" ? (
                        "The count didn't load. Open it in the app."
                      ) : c.status === "done" ? (
                        "No count for this month yet."
                      ) : (
                        "Counting…"
                      )}
                    </p>
                    {c.count != null && c.median != null && c.median > 0 && (
                      <p className="text-sm text-fl-text">
                        {(() => {
                          const typical = `a typical ${month ? MONTHS_LONG[month.month - 1] : "month"}`;
                          const pct = Math.round(((c.count - c.median) / c.median) * 100);
                          // past +200% a multiple reads better than a percentage
                          if (pct > 200) return `${Math.round(c.count / c.median)}× ${typical}`;
                          return Math.abs(pct) < 5 ? "About normal for the month" : `${Math.abs(pct)}% ${pct > 0 ? "above" : "below"} ${typical}`;
                        })()}
                      </p>
                    )}
                    <Link
                      href={`/app?area=${c.box.join(",")}&label=${encodeURIComponent(c.name ?? "Busiest fire area")}${month ? `&month=${month.year}-${String(month.month).padStart(2, "0")}` : ""}`}
                      className="mt-auto w-fit text-sm font-semibold text-fl-accent-text hover:underline"
                    >
                      Open in the app
                    </Link>
                  </>
                ) : (
                  <div className="flex animate-pulse flex-col gap-2" aria-hidden>
                    <span className="h-4 w-40 rounded bg-fl-track" />
                    <span className="h-3 w-28 rounded bg-fl-track" />
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
        <p className="text-xs text-fl-faint">Place names: OpenStreetMap. Images: NASA GIBS.</p>
      </div>
    </section>
  );
}
