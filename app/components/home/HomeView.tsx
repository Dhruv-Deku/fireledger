"use client";

import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import Image from "next/image";
import Link from "next/link";
import { getProof, USING_MOCK } from "@/lib/api";
import { leftover, pctSigned } from "@/lib/reliability";
import BurningNow from "./BurningNow";

const HomeGlobe = dynamic(() => import("./HomeGlobe"), { ssr: false });
// kept here, not imported from HomeGlobe, so maplibre stays out of the first page load
const ORBIT_SPEEDUP = 120;

const SEASONS = [
  {
    id: "punjab",
    place: "Punjab, India",
    when: "November 2016",
    month: "2016-11",
    date: "1 Nov 2016",
    img: "/seasons/punjab-2016-11.jpg",
    text: "Rice stubble burned after the harvest. The smoke settled over Delhi for days.",
  },
  {
    id: "central-kalimantan",
    place: "Central Kalimantan, Indonesia",
    when: "September 2015",
    month: "2015-09",
    date: "24 Sep 2015",
    img: "/seasons/central-kalimantan-2015-09.jpg",
    text: "Peatland fires in a strong El Niño year. The haze covered much of Southeast Asia.",
  },
  {
    id: "california",
    place: "California, USA",
    when: "September 2020",
    month: "2020-09",
    date: "9 Sep 2020",
    img: "/seasons/california-2020-09.jpg",
    text: "The August Complex and dozens more. San Francisco woke under an orange sky that day.",
  },
  {
    id: "mato-grosso",
    place: "Mato Grosso, Brazil",
    when: "August 2019",
    month: "2019-08",
    date: "15 Aug 2019",
    img: "/seasons/mato-grosso-2019-08.jpg",
    text: "Dry-season burning where farmland meets the Amazon. The brown patches are cleared land.",
  },
  {
    id: "yakutia",
    place: "Sakha (Yakutia), Russia",
    when: "August 2021",
    month: "2021-08",
    date: "6 Aug 2021",
    img: "/seasons/yakutia-2021-08.jpg",
    text: "Siberia's largest fire season on record. Its smoke drifted as far as the North Pole.",
  },
];

const SATELLITES = [
  { name: "Terra", sensor: "MODIS", year: 1999, note: "Launched Dec 1999. The record starts with its first fire data, Nov 2000." },
  { name: "Aqua", sensor: "MODIS", year: 2002, note: "Launched May 2002. An afternoon pass, when most crop fires burn." },
  { name: "Suomi NPP", sensor: "VIIRS", year: 2011, note: "Launched Oct 2011. VIIRS fires join the record in Jan 2012." },
  { name: "NOAA-20", sensor: "VIIRS", year: 2017, note: "Launched Nov 2017. The unit every year is converted to." },
  { name: "NOAA-21", sensor: "VIIRS", year: 2022, note: "Launched Nov 2022." },
];

export default function HomeView() {
  const [month, setMonth] = useState<string | null>(null);
  const onMonth = useCallback((m: string | null) => setMonth(m), []);

  return (
    <>
      <section className="relative h-[100svh] min-h-[560px] overflow-hidden space-backdrop">
        <HomeGlobe onMonth={onMonth} />
        {/* keeps the headline readable where it overlaps the globe on small screens */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-[linear-gradient(90deg,var(--fl-bg)_0%,color-mix(in_srgb,var(--fl-bg)_70%,transparent)_32%,transparent_62%)] max-lg:bg-[linear-gradient(0deg,var(--fl-bg)_8%,color-mix(in_srgb,var(--fl-bg)_75%,transparent)_45%,transparent_75%)]"
        />
        <div className="pointer-events-none relative flex h-full flex-col justify-end px-5 pb-12 sm:px-10 lg:justify-center lg:pb-0">
          <div className="pointer-events-auto flex max-w-[38rem] flex-col gap-6">
            <h1 className="wide text-[clamp(2.4rem,5.6vw,4.9rem)] font-semibold leading-[0.98] tracking-[-0.03em] text-fl-text">
              Every fire seen from orbit since 2000, on one scale.
            </h1>
            <p className="max-w-[34rem] text-[17px] leading-relaxed text-fl-muted">
              MODIS and VIIRS count the same fires differently. FireLedger turns both into one record, so you can
              compare this month with any month back to 2000, for any area you draw.
            </p>
            <div className="flex flex-wrap gap-3">
              <Link
                href="/app"
                className="inline-flex h-12 items-center rounded-full bg-fl-accent px-6 text-[15px] font-semibold text-fl-on-accent transition-transform hover:-translate-y-px"
              >
                Open the app
              </Link>
              <Link
                href="/accuracy"
                className="inline-flex h-12 items-center rounded-full border border-fl-text/25 px-6 text-[15px] font-medium text-fl-text transition-colors hover:border-fl-text/60"
              >
                Accuracy and trust
              </Link>
            </div>
            <p className="max-w-[34rem] text-[13px] leading-snug text-fl-faint">
              {month
                ? `On the globe: fire hotspots in ${month}, harmonized to VIIRS units. Drag to turn it.`
                : USING_MOCK
                  ? "Sample mode: connect the API to see real hotspots on the globe."
                  : "Drag to turn the globe."}{" "}
              The five satellites fly at their real heights and tilts, with each orbit set to its
              equator-crossing time against today&apos;s sun, sped up {ORBIT_SPEEDUP}×. Where each one sits along
              its orbit is illustrative.
            </p>
          </div>
        </div>
      </section>

      <BurningNow />
      <Seasons />
      <Record />

      <footer className="border-t border-fl-border px-5 py-6 text-center text-xs leading-relaxed text-fl-muted">
        Data: NASA FIRMS (MODIS C6.1, VIIRS 375 m) · Built for NASA Space Apps Challenge 2026
        <br />
        Images: NASA Worldview and GIBS (MODIS Terra true colour, Black Marble, Blue Marble)
      </footer>
    </>
  );
}

function Seasons() {
  const [active, setActive] = useState(0);
  return (
    <section className="mx-auto flex max-w-6xl flex-col gap-8 px-5 py-20 sm:px-10">
      <div className="flex max-w-[40rem] flex-col gap-3">
        <h2 className="wide text-[clamp(1.7rem,3.2vw,2.6rem)] font-semibold leading-[1.05] tracking-[-0.02em] text-fl-text">
          Five fire seasons, seen from orbit
        </h2>
        <p className="text-[16px] leading-relaxed text-fl-muted">
          True-colour images from NASA&apos;s Terra satellite on the day. Pick one to open that place and month in
          the app.
        </p>
      </div>

      <div className="flex h-[min(580px,72vh)] gap-2 max-md:h-[820px] max-md:flex-col" role="list">
        {SEASONS.map((s, i) => {
          const on = i === active;
          return (
            <div
              key={s.id}
              role="listitem"
              onMouseEnter={() => setActive(i)}
              className={`relative min-h-0 min-w-0 basis-0 overflow-hidden rounded-[20px] transition-[flex-grow] duration-700 ease-[cubic-bezier(.2,.7,.2,1)] ${
                on ? "grow-[7]" : "grow"
              }`}
            >
              <Image
                src={s.img}
                alt={`${s.place} from orbit on ${s.date}`}
                fill
                sizes="(max-width: 768px) 100vw, 60vw"
                className={`object-cover transition-[filter,transform] duration-700 ${
                  on ? "scale-100" : "scale-[1.04] brightness-[0.55] grayscale"
                }`}
              />
              <button
                type="button"
                onClick={() => setActive(i)}
                onFocus={() => setActive(i)}
                aria-expanded={on}
                aria-label={`${s.place}, ${s.when}`}
                className="absolute inset-0 rounded-[20px] focus-visible:outline-offset-[-3px]"
              />
              <span className="num pointer-events-none absolute left-3 top-3 rounded-full bg-black/45 px-2.5 py-1 text-xs text-white backdrop-blur">
                {s.when.slice(-4)}
              </span>
              {!on && (
                <span className="pointer-events-none absolute bottom-4 left-3 text-[13px] font-medium text-white/90 [writing-mode:vertical-rl] rotate-180 max-md:rotate-0 max-md:[writing-mode:horizontal-tb]">
                  {s.place.split(",")[0]}
                </span>
              )}
              {on && (
                <div className="glass absolute bottom-4 left-4 right-4 flex max-w-[26rem] flex-col gap-2 p-4 sm:bottom-5 sm:left-5">
                  <p className="wide text-[19px] font-semibold leading-tight text-fl-text">{s.place}</p>
                  <p className="text-[13px] text-fl-muted">
                    {s.when}, image from {s.date}
                  </p>
                  <p className="text-[14px] leading-snug text-fl-text">{s.text}</p>
                  <Link
                    href={`/app?place=${s.id}&month=${s.month}`}
                    className="relative z-10 mt-1 w-fit text-sm font-semibold text-fl-accent-text hover:underline"
                  >
                    Open this season in the app
                  </Link>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function Record() {
  const [jump, setJump] = useState<{ raw: number; left: number } | null>(null);
  useEffect(() => {
    getProof()
      .then((p) => {
        const w = p?.jumps.find((j) => j.region === "World");
        if (w) setJump({ raw: w.step2012_raw_merged, left: leftover(w) });
      })
      .catch(() => {});
  }, []);

  const from = 1999;
  const to = 2026;
  const at = (y: number) => `${((y - from) / (to - from)) * 100}%`;

  return (
    <section className="border-t border-fl-border">
      <div className="mx-auto flex max-w-6xl flex-col gap-10 px-5 py-20 sm:px-10">
        <div className="flex max-w-[42rem] flex-col gap-3">
          <h2 className="wide text-[clamp(1.7rem,3.2vw,2.6rem)] font-semibold leading-[1.05] tracking-[-0.02em] text-fl-text">
            Five satellites, one record
          </h2>
          <p className="text-[16px] leading-relaxed text-fl-muted">
            VIIRS sees smaller fires than MODIS. When it joined in 2012, raw hotspot counts jumped
            {jump ? ` ${pctSigned(jump.raw)} worldwide` : ""} while the fires themselves did not. FireLedger converts
            every year to the same unit, so the jump is gone
            {jump ? `: ${pctSigned(jump.left, 1)} is left over` : ""}.
          </p>
        </div>

        <div className="relative pt-2">
          <div className="relative h-px bg-fl-border">
            <span className="absolute -top-px h-[3px] rounded-full bg-fl-muted/50" style={{ left: at(2000.85), right: 0 }} />
          </div>
          <ol className="mt-6 grid gap-6 sm:grid-cols-5">
            {SATELLITES.map((s) => (
              <li key={s.name} className="relative flex flex-col gap-1.5">
                <span className="num text-[13px] text-fl-faint">{s.year}</span>
                <span className="wide text-[18px] font-semibold text-fl-text">{s.name}</span>
                <span className={`w-fit rounded-full px-2 py-0.5 text-xs ${s.sensor === "VIIRS" ? "bg-fl-accent/15 text-fl-accent-text" : "bg-fl-subtle text-fl-muted"}`}>
                  {s.sensor}
                </span>
                <span className="text-[14px] leading-snug text-fl-muted">{s.note}</span>
              </li>
            ))}
          </ol>
        </div>

        <div className="flex flex-wrap gap-3">
          <Link href="/accuracy" className="text-sm font-semibold text-fl-accent-text hover:underline">
            See how the conversion is tested
          </Link>
        </div>
      </div>
    </section>
  );
}
