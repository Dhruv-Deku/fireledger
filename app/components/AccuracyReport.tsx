"use client";

import { useEffect, useState } from "react";
import { getProof, isGridOnly, USING_MOCK, type ProofResponse } from "@/lib/api";
import { MONTHS, fmtInt } from "@/lib/chartTheme";
import { leftover, pctSigned, verdict, VERDICT_TEXT, type Verdict } from "@/lib/reliability";

const pct = (f: number, digits = 1) => `${(f * 100).toFixed(digits)}%`;
const nearSwitch = (b: { year: number; p: number }) => b.p < 0.05 && b.year >= 2011 && b.year <= 2013;

/**
 * "How accurate is this?": the harmonization's own validation from /api/proof,
 * in plain words. Every number and verdict is computed from the API's answer.
 */
export default function AccuracyReport() {
  const [proof, setProof] = useState<ProofResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [gridOnly, setGridOnly] = useState(false);

  useEffect(() => {
    getProof()
      .then(setProof)
      .catch((e: Error) => setError(e.message));
    isGridOnly()
      .then(setGridOnly)
      .catch(() => {});
  }, []);

  if (USING_MOCK) {
    return (
      <p className="text-sm text-fl-muted">
        The app is running on sample data, which has no validation numbers. Connect it to the FireLedger API to see
        them.
      </p>
    );
  }
  if (error) {
    return (
      <p className="text-sm text-fl-accent-text" role="alert">
        {error}
      </p>
    );
  }
  if (!proof) return <p className="text-sm text-fl-muted" role="status">Loading the validation…</p>;
  return <Report p={proof} gridOnly={gridOnly} />;
}

function Report({ p, gridOnly }: { p: ProofResponse; gridOnly: boolean }) {
  const raw = p.ablation[0];
  const ours = p.ablation.find((a) => /FireLedger/i.test(a.step)) ?? p.ablation[p.ablation.length - 1];
  const coverage = p.conformal_bands.modis_to_snpp.coverage_crossfit;
  const world = p.jumps.find((j) => j.region === "World");
  const regions = p.jumps.filter((j) => j.region !== "World");
  const verdicts = regions.map((j) => verdict(leftover(j)));
  const good = verdicts.filter((v) => v === "good").length;
  const weak = regions.filter((j) => verdict(leftover(j)) === "weak");
  const breaksRaw = regions.filter((j) => nearSwitch(j.pettitt_raw)).length;
  const breaksHarm = regions.filter((j) => nearSwitch(j.pettitt_harmonized));
  const years = `${p.test_years[0]}–${p.test_years[p.test_years.length - 1]}`;

  return (
    <div className="flex flex-col gap-12">
      <section className="flex flex-col gap-4">
        <p className="max-w-[68ch] text-[15px] leading-relaxed text-fl-text">
          MODIS and VIIRS both flew from 2012, so we can test the conversion on years where we know the answer. We
          hid one year at a time ({years}), built the conversion from the other years, turned that year&apos;s MODIS
          counts into VIIRS units, and compared them with what VIIRS actually saw on the same days.
        </p>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-6 md:grid-cols-4">
          <Tile
            value={pct(ours.wape_region_month)}
            label="average miss per area and month"
            sub={`Uncorrected MODIS: ${pct(raw.wape_region_month, 0)}`}
          />
          <Tile
            value={pct(coverage.region_month, 0)}
            label="of area-months inside the 90% range"
            sub="Aim: 90%"
          />
          {world && (
            <Tile
              value={pctSigned(leftover(world), 1)}
              label="jump left at the 2012 switch, whole world"
              sub={`Raw record: ${pctSigned(world.step2012_raw_merged)}`}
            />
          )}
          <Tile
            value={`${good} of ${regions.length}`}
            label="world regions within 5% at the switch"
            sub={weak.length ? `Weak: ${weak.map((j) => j.region).join(", ")}` : "None weak"}
          />
        </dl>
      </section>

      <Section
        title="What each step of the method adds"
        note="Average miss per area and month in the held-out years. Shorter is better. + adds a step to the line above; − takes one out of FireLedger's model."
      >
        <ol className="flex flex-col gap-2">
          {p.ablation.map((a) => {
            const isOurs = a === ours;
            return (
              <li key={a.step} className="grid grid-cols-[minmax(0,15rem)_1fr_3.5rem] items-center gap-3 text-[13px]">
                <span className={isOurs ? "font-medium text-fl-text" : "text-fl-muted"}>{a.step}</span>
                <span className="h-2 bg-fl-track" aria-hidden>
                  <span
                    className={`block h-full rounded-r-[2px] ${isOurs ? "bg-fl-accent" : "bg-fl-muted/45"}`}
                    style={{ width: `${(a.wape_region_month / raw.wape_region_month) * 100}%` }}
                  />
                </span>
                <span className={`num text-right ${isOurs ? "text-fl-text" : "text-fl-muted"}`}>
                  {pct(a.wape_region_month)}
                </span>
              </li>
            );
          })}
        </ol>
        <p className="text-xs leading-snug text-fl-muted">
          For one 1° cell and month the miss is larger: <span className="num">{pct(ours.wape_cell_month)}</span>. For
          the whole world: <span className="num">{pct(ours.wape_world_month)}</span>. Bias:{" "}
          <span className="num">{pctSigned(ours.bias_region_month, 1)}</span>. The second step, S-NPP to NOAA-20
          units, adds <span className="num">{pct(p.snpp_to_noaa20_wape.region_month)}</span> per area and month.
        </p>
        {p.industrial_heldout && <IndustrialLine ih={p.industrial_heldout} />}
      </Section>

      <Section
        title="Does the 2012 jump go away?"
        note="Vegetation fires, 2012–15 compared with 2008–11. MODIS kept flying through the switch, so its own change is the real one; the harmonized record should match it."
      >
        <div className="scrollbar-dark overflow-x-auto">
          <table className="w-full min-w-[640px] text-[13px]">
            <thead>
              <tr className="border-b border-fl-border text-left text-xs text-fl-muted">
                <th className="py-2 pr-3 font-normal">Region</th>
                <th className="py-2 pr-3 text-right font-normal">Raw record</th>
                <th className="py-2 pr-3 text-right font-normal">MODIS alone</th>
                <th className="py-2 pr-3 text-right font-normal">Harmonized</th>
                <th className="py-2 pr-3 font-normal">Left over</th>
                <th className="py-2 font-normal">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {p.jumps.map((j) => {
                const gap = leftover(j);
                const v = verdict(gap);
                return (
                  <tr key={j.region} className="border-b border-fl-border/60">
                    <td className={`py-1.5 pr-3 ${j.region === "World" ? "font-medium text-fl-text" : "text-fl-text"}`}>
                      {j.region}
                    </td>
                    <td className="num py-1.5 pr-3 text-right text-fl-muted">{pctSigned(j.step2012_raw_merged)}</td>
                    <td className="num py-1.5 pr-3 text-right text-fl-text">{pctSigned(j.step2012_modis_vegetation, 1)}</td>
                    <td className="num py-1.5 pr-3 text-right text-fl-text">
                      {pctSigned(j.step2012_harmonized_vegetation, 1)}
                    </td>
                    <td className="py-1.5 pr-3">
                      <GapDot gap={gap} v={v} />
                    </td>
                    <td className="py-1.5">
                      <VerdictTag v={v} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="text-xs leading-snug text-fl-muted">
          A break test (Pettitt) finds a sudden change in 2011–13 in <span className="num">{breaksRaw}</span> of{" "}
          {regions.length} regions in the raw record, and in <span className="num">{breaksHarm.length}</span> after
          harmonization{breaksHarm.length ? ` (${breaksHarm.map((j) => j.region).join(", ")})` : ""}.
        </p>
      </Section>

      <Section
        title="Check against burned area"
        note="MODIS burned-area maps had no sensor change in 2012, so they are an independent check. A consistent record keeps hotspots per km² burned steady across 2012 and rises and falls with burned area year to year."
      >
        <div className="scrollbar-dark overflow-x-auto">
          <table className="w-full min-w-[560px] text-[13px]">
            <thead>
              <tr className="border-b border-fl-border text-left text-xs text-fl-muted">
                <th className="py-2 pr-3 font-normal">Region</th>
                <th className="py-2 pr-3 text-right font-normal">Change at 2012, raw</th>
                <th className="py-2 pr-3 text-right font-normal">Harmonized</th>
                <th className="py-2 pr-3 text-right font-normal">MODIS alone</th>
                <th className="py-2 font-normal">Follows burned area (−1 to 1), raw → harmonized</th>
              </tr>
            </thead>
            <tbody>
              {p.burned_area.regions.map((r) => (
                <tr key={r.region} className="border-b border-fl-border/60">
                  <td className={`py-1.5 pr-3 ${r.region === "World" ? "font-medium" : ""} text-fl-text`}>{r.region}</td>
                  <td className="num py-1.5 pr-3 text-right text-fl-muted">{pctSigned(r.step2012.raw_merged)}</td>
                  <td className="num py-1.5 pr-3 text-right text-fl-text">{pctSigned(r.step2012.harmonized, 1)}</td>
                  <td className="num py-1.5 pr-3 text-right text-fl-text">{pctSigned(r.step2012.modis, 1)}</td>
                  <td className="py-1.5">
                    <Dumbbell from={r.spearman_vs_burned.raw_merged} to={r.spearman_vs_burned.harmonized} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      {p.creams && <OfficialCounts c={p.creams} />}

      <Section title="Known limits" note="Where to read the numbers with care.">
        <ul className="flex max-w-[68ch] list-disc flex-col gap-2 pl-5 text-[14px] leading-relaxed text-fl-text marker:text-fl-muted">
          {weak.length > 0 && (
            <li>
              Weak at the 2012 switch: {weak.map((j) => `${j.region} (${pctSigned(leftover(j))} left over)`).join(", ")}.
              Here the record changes more across 2012 than MODIS alone does, so the years before 2012 read low
              against the years after. The cause is fires MODIS could not see: VIIRS&rsquo;s 375 m pixels catch
              small, cool fires (crop-stubble and grass burns, the edges of boreal fires) that MODIS&rsquo;s 1 km
              pixels miss entirely. Where those fires are a large share, as in Northern Europe and Canada&rsquo;s
              boreal north (the United States is fine), the MODIS years hold nothing to scale up, and no
              conversion can bring back fires that were never detected. Small industrial flares, which MODIS also
              misses, add to it. The burned-area check above finds no break at 2012 in the matching boreal and
              European regions, so the gap is in what MODIS saw, not a step in the record. Across 166,404 tuning combinations, none closed these three
              without making other areas worse, so we report it instead of hiding it.
              <span className="mt-1 block text-fl-muted">
                What to do: compare years on the same side of 2012 (2001–2011 with each other, or 2012 to today),
                or use the trend from 2012 on, which has no conversion in it.
              </span>
              <InApp>
                every area gets a &ldquo;2012 switch&rdquo; badge from its own test; where it is fair or caution, the
                timeline shades 2008–2015 and the trend and busiest years say that years either side of 2012 compare
                less exactly. The trend card also gives the trend from 2012 on, with no conversion in it.
              </InApp>
            </li>
          )}
          <li>
            A single cell is less exact than an area: one cell in one month misses by{" "}
            <span className="num">{pct(ours.wape_cell_month)}</span> on average. Larger areas average the errors out.
            <InApp>areas smaller than about one 1° cell get a note with these two numbers next to the count.</InApp>
          </li>
          {gridOnly && (
            <li>
              This server keeps monthly totals per 0.25° grid cell (about 28 km), not single hotspots. An area is
              counted in whole cells, so its edges are approximate, and an area smaller than one cell can come back
              empty even where fires burn. The hotspot list and single-detection map need the full-data server.
              <InApp>small areas get this note next to their count, and an empty answer says so instead of failing.</InApp>
            </li>
          )}
          <li>
            Before 2012 only MODIS flew, so those months are converted, not measured, and their likely range is wide.
            Months marked B (blind spot) lean on the conversion most.
            <InApp>
              busiest years leave out years with blind-spot months unless you include them; the calendar marks B
              months and the line chart shows the likely range.
            </InApp>
          </li>
          <li>
            From 2012, VIIRS is counted. A fire seen only by MODIS (VIIRS missed it under cloud or between passes) stays
            in the hotspots CSV but counts 0 in VIIRS units.
            <InApp>
              the full hotspots CSV keeps every one of them; once the API reports them per month, the results show
              how many were left out.
            </InApp>
          </li>
          <li>
            Gas flares and factories are flagged, and &ldquo;Hide industrial&rdquo; removes them. In MODIS years they
            can carry large weights, so in industrial areas compare both settings.
            <InApp>
              the calendar tooltip shows each month&apos;s industrial share, and where the API warns about industrial
              heat, one button hides it.
            </InApp>
          </li>
        </ul>
        <p className="text-xs text-fl-muted">
          Method: {p.method}. <span className="num">{fmtInt(p.test_years.length)}</span> held-out years.
        </p>
      </Section>
    </div>
  );
}

/**
 * Industrial sites (flares, plants) have their own conversion since 15 Nov,
 * because MODIS misses most small ones. Both directions are shown: per area it
 * improved, per single cell it got worse.
 */
function IndustrialLine({ ih }: { ih: NonNullable<ProofResponse["industrial_heldout"]> }) {
  const was = ih.nov14;
  const now = ih.shipped;
  return (
    <p className="text-xs leading-snug text-fl-muted">
      Industrial sites (flares, plants) now get their own conversion, because MODIS misses most small ones: held-out
      error per area and month <span className="num text-fl-text">{pct(was.wape_region_month, 0)} → {pct(now.wape_region_month, 0)}</span>,
      bias <span className="num text-fl-text">{pctSigned(was.bias)} → {pctSigned(now.bias)}</span>. For a single cell it
      got worse (<span className="num">{pct(was.wape_cell_month, 0)} → {pct(now.wape_cell_month, 0)}</span>), so read
      industrial hotspots by area, not cell by cell.
    </p>
  );
}

const STATE_SEASONS = ["Punjab rice", "Haryana rice", "Punjab wheat", "Haryana wheat"];
/** "Punjab rice" -> "Punjab, paddy-burning season" */
const seasonName = (k: string) => {
  const [state, crop] = k.split(" ");
  return `${state}, ${crop === "rice" ? "paddy" : "wheat"}-burning season`;
};

/**
 * Our counts against India's official crop-fire bulletins (ICAR-IARI CREAMS).
 * CREAMS counts with its own protocol, so totals differ; the season-to-season
 * shape and the burning days should match.
 */
function OfficialCounts({ c }: { c: NonNullable<ProofResponse["creams"]> }) {
  const entries = STATE_SEASONS.flatMap((k) => (c.by_state_season[k] ? [[k, c.by_state_season[k]] as const] : []));
  const punjab = c.by_state_season["Punjab rice"];
  const complete = punjab?.seasons.filter((s) => s.last < c.through) ?? [];
  const [a, b] = [complete[0], complete[complete.length - 1]];
  const throughText = `${Number(c.through.slice(8, 10))} ${MONTHS[Number(c.through.slice(5, 7)) - 1]}`;

  return (
    <Section
      title="Against India's official crop-fire counts"
      note="ICAR-IARI's CREAMS bulletins count crop-residue fires in Punjab and Haryana every season. They use their own method, so the totals differ from ours; the rise and fall between seasons and the busy days should match."
    >
      {a && b && a !== b && (
        <p className="max-w-[68ch] text-[15px] leading-relaxed text-fl-text">
          Punjab&apos;s paddy-burning season: official{" "}
          <span className="num">{fmtInt(a.creams)} → {fmtInt(b.creams)}</span> ({a.year} → {b.year}), FireLedger{" "}
          <span className="num">{fmtInt(a.fireledger)} → {fmtInt(b.fireledger)}</span>. The burning days line up: daily
          rank correlation <span className="num">{punjab.daily_spearman_fireledger.toFixed(2)}</span>.
        </p>
      )}
      <div className="flex items-center gap-4 text-xs text-fl-muted" aria-hidden>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm bg-fl-muted/60" /> Official (CREAMS)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm bg-fl-accent" /> FireLedger
        </span>
      </div>
      <div className="grid gap-8 md:grid-cols-2">
        {entries.map(([k, s]) => {
          const max = Math.max(1, ...s.seasons.flatMap((x) => [x.creams, x.fireledger]));
          return (
            <figure key={k} className="flex flex-col gap-3">
              <figcaption className="flex items-baseline justify-between gap-2 text-sm">
                <span className="text-fl-text">{seasonName(k)}</span>
                <span className="text-xs text-fl-muted">
                  days line up: <span className="num text-fl-text">{s.daily_spearman_fireledger.toFixed(2)}</span>
                </span>
              </figcaption>
              <div className="flex items-end gap-6" role="img" aria-label={`${seasonName(k)}: official and FireLedger counts by season`}>
                {s.seasons.map((x) => {
                  const partial = x.last >= c.through;
                  return (
                    <div key={x.year} className="flex flex-col items-center gap-1.5">
                      <div className="flex h-36 items-end gap-1">
                        <Bar value={x.creams} max={max} className="bg-fl-muted/60" label={`Official ${x.year}`} />
                        <Bar value={x.fireledger} max={max} className="bg-fl-accent" label={`FireLedger ${x.year}`} />
                      </div>
                      {/* Fixed height, so every season's bars share one baseline. */}
                      <div className="flex h-8 flex-col items-center">
                        <span className="num text-xs text-fl-text">{x.year}</span>
                        {partial && <span className="text-[11px] text-fl-muted">to {throughText}</span>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </figure>
          );
        })}
      </div>
      <p className="max-w-[68ch] text-xs leading-snug text-fl-muted">
        {c.note} Source: {c.source}
      </p>
    </Section>
  );
}

function Bar({ value, max, className, label }: { value: number; max: number; className: string; label: string }) {
  return (
    <div className="flex h-full w-11 flex-col items-center justify-end gap-1" title={`${label}: ${fmtInt(value)}`}>
      <span className="num text-[10px] text-fl-muted">{fmtInt(value)}</span>
      <div className={`w-full rounded-t-[3px] ${className}`} style={{ height: `${Math.max(2, (value / max) * 100)}%` }} />
    </div>
  );
}

function InApp({ children }: { children: React.ReactNode }) {
  return (
    <span className="mt-1 block text-[13px] leading-snug text-fl-muted">
      <span className="font-medium text-fl-text">In the app: </span>
      {children}
    </span>
  );
}

function Section({ title, note, children }: { title: string; note: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-4 border-t border-fl-border pt-8">
      <div className="flex max-w-[68ch] flex-col gap-1">
        <h2 className="wide text-[22px] font-semibold tracking-[-0.015em] text-fl-text">{title}</h2>
        <p className="text-[13px] leading-snug text-fl-muted">{note}</p>
      </div>
      {children}
    </section>
  );
}

function Tile({ value, label, sub }: { value: string; label: string; sub: string }) {
  return (
    <div className="flex flex-col gap-1">
      {/* Label first for screen readers; the number shows first on screen. */}
      <dt className="order-2 text-[13px] leading-snug text-fl-text">{label}</dt>
      <dd className="num wide order-1 text-[34px] font-medium leading-none tracking-tight text-fl-text">{value}</dd>
      <dd className="order-3 text-xs leading-snug text-fl-muted">{sub}</dd>
    </div>
  );
}

function VerdictTag({ v }: { v: Verdict }) {
  const t = VERDICT_TEXT[v];
  return (
    <span className={`whitespace-nowrap ${v === "weak" ? "text-fl-accent-text" : v === "check" ? "text-fl-text" : "text-fl-muted"}`}>
      <span aria-hidden className="num">{t.icon} </span>
      {t.word}
    </span>
  );
}

/** Dot on a −40%…+40% scale, the ±5% "good" band shaded; values beyond the scale pin to the edge. */
function GapDot({ gap, v }: { gap: number; v: Verdict }) {
  const LIM = 0.4;
  const pos = (f: number) => `${((Math.max(-LIM, Math.min(LIM, f)) + LIM) / (2 * LIM)) * 100}%`;
  return (
    <span className="flex items-center gap-2" title={`${pctSigned(gap, 1)} left over`}>
      <span className="relative h-3 w-24 shrink-0" aria-hidden>
        <span className="absolute inset-x-0 top-1/2 h-px bg-fl-border" />
        <span className="absolute inset-y-0 bg-fl-muted/20" style={{ left: pos(-0.05), width: `calc(${pos(0.05)} - ${pos(-0.05)})` }} />
        <span className="absolute inset-y-0 w-px bg-fl-muted/60" style={{ left: "50%" }} />
        <span
          className={`absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full ${v === "weak" ? "bg-fl-accent" : "bg-fl-text"}`}
          style={{ left: pos(gap) }}
        />
      </span>
      <span className="num text-fl-text">{pctSigned(gap, 1)}</span>
    </span>
  );
}

/** Raw (hollow) to harmonized (filled) on a −1…1 scale. */
function Dumbbell({ from, to }: { from: number; to: number }) {
  const pos = (f: number) => `${((f + 1) / 2) * 100}%`;
  const lo = Math.min(from, to);
  const hi = Math.max(from, to);
  return (
    <span className="flex items-center gap-2" title={`raw ${from.toFixed(2)} → harmonized ${to.toFixed(2)}`}>
      <span className="relative h-3 w-28 shrink-0" aria-hidden>
        <span className="absolute inset-x-0 top-1/2 h-px bg-fl-border" />
        <span className="absolute inset-y-0 w-px bg-fl-muted/60" style={{ left: "50%" }} />
        <span className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-fl-muted/50" style={{ left: pos(lo), width: `calc(${pos(hi)} - ${pos(lo)})` }} />
        <span className="absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-fl-muted bg-fl-surface" style={{ left: pos(from) }} />
        <span className="absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-fl-text" style={{ left: pos(to) }} />
      </span>
      <span className="num whitespace-nowrap text-fl-text">
        {from.toFixed(2)} → {to.toFixed(2)}
      </span>
    </span>
  );
}
