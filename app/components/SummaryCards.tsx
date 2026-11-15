"use client";

import { useState } from "react";
import type { MonthRow, SummaryResponse, Trend as TrendT } from "@/lib/api";
import { MONTHS, MONTHS_LONG, anomalyStyle, fmtInt, pctWords } from "@/lib/chartTheme";
import { mannKendallP, senSlope } from "@/lib/stats";

/**
 * The top of the results: this month against normal (plus the industrial
 * warning when the API sends one). The calendar follows, then SummaryHistory.
 */
export function SummaryHeadline({
  summary,
  requestEnd,
  unitText,
  onHideIndustrial,
  smallArea,
}: {
  summary: SummaryResponse;
  /** End date of the search (YYYY-MM-DD), used when the API doesn't say how far the data goes. */
  requestEnd: string;
  unitText: string;
  /** Set while industrial hotspots are shown: one click turns them off. */
  onHideIndustrial?: () => void;
  /** Set when the area is about one grid cell or smaller; errors from the api's held-out test. */
  smallArea?: { km2: number; cellError?: number; regionError?: number; gridNote?: string } | null;
}) {
  return (
    <div className="flex flex-col gap-6">
      {summary.industrial?.warning && (
        <div role="note" className="flex flex-col items-start gap-2 text-sm leading-snug text-fl-muted">
          <p>
            <span className="font-medium text-fl-accent-text">Industrial fires: </span>
            {summary.industrial.warning}
          </p>
          {onHideIndustrial && (
            <button
              type="button"
              onClick={onHideIndustrial}
              className="rounded-full border border-fl-border px-3 py-1 text-[13px] text-fl-text transition-colors hover:border-fl-faint"
            >
              Hide industrial hotspots
            </button>
          )}
        </div>
      )}
      {smallArea && (
        <p role="note" className="text-sm leading-snug text-fl-muted">
          <span className="font-medium text-fl-text">Small area </span>
          (<span className="num">{fmtInt(smallArea.km2)}</span> km²): month-to-month counts are less exact here.
          {smallArea.cellError != null && smallArea.regionError != null && (
            <>
              {" "}
              In the held-out test a single grid cell misses by{" "}
              <span className="num text-fl-text">{(smallArea.cellError * 100).toFixed(0)}%</span> on average, a large
              region by <span className="num text-fl-text">{(smallArea.regionError * 100).toFixed(0)}%</span>.
            </>
          )}
          {smallArea.gridNote && <> {smallArea.gridNote}</>}
        </p>
      )}
      <ThisMonth tm={summary.this_month} requestEnd={requestEnd} unitText={unitText} />
    </div>
  );
}

/** Busiest months and years, fire season, coming up, trend. */
export function SummaryHistory({
  summary,
  months = [],
  switchCaveat,
}: {
  summary: SummaryResponse;
  /** Calendar months, to mark busiest years that contain blind-spot (B) months. */
  months?: MonthRow[];
  /** One line when years either side of 2012 compare less exactly here, else null. */
  switchCaveat?: string | null;
}) {
  const blindYears = new Set(months.filter((m) => m.flags.includes("B")).map((m) => m.year));
  return (
    <div className="flex flex-col gap-6">
      {/* Two columns only when the results panel itself is wide enough. */}
      <div className="grid gap-x-8 gap-y-7 border-t border-fl-border pt-6 @[500px]:grid-cols-2">
        <BusiestMonths items={summary.busiest_months} />
        <BusiestYears
          items={summary.busiest_years}
          noBlind={summary.busiest_years_no_blind ?? busiestYearsFrom(months, blindYears)}
          blindYears={blindYears}
          caveat={switchCaveat}
        />
        <FireSeason season={summary.season} />
        <ComingUp items={summary.critical_next} />
        <div className="@[500px]:col-span-2">
          <Trend
            trend={summary.trend}
            viirs={summary.trend_viirs !== undefined ? summary.trend_viirs : viirsTrendFrom(summary)}
            annual={summary.annual ?? []}
            blindYears={blindYears}
            caveat={switchCaveat}
          />
        </div>
      </div>
    </div>
  );
}

/**
 * busiest full years without blind-spot years, from the calendar, same rule as the
 * api's busiest_years: a year counts when all 12 months are there and finished.
 */
function busiestYearsFrom(months: MonthRow[], blindYears: Set<number>) {
  const byYear = new Map<number, { total: number; n: number }>();
  for (const m of months) {
    if (m.partial) continue;
    const y = byYear.get(m.year) ?? { total: 0, n: 0 };
    y.total += m.count;
    y.n += 1;
    byYear.set(m.year, y);
  }
  return [...byYear.entries()]
    .filter(([year, y]) => y.n === 12 && !blindYears.has(year))
    .map(([year, y]) => ({ year, total: y.total }))
    .sort((a, b) => b.total - a.total)
    .slice(0, 3);
}

/** the trend from 2012 on only, worked out here until the api sends trend_viirs */
function viirsTrendFrom(summary: SummaryResponse): TrendT | null {
  const last = summary.trend.years?.[1];
  const years = (summary.annual ?? []).filter((a) => a.year >= 2012 && (last == null || a.year <= last));
  if (years.length < 6) return null;
  const slope = senSlope(years.map((a) => ({ x: a.year, y: a.count })));
  const p = mannKendallP(years.map((a) => a.count));
  return {
    sen_slope_per_year: slope,
    p_value: p,
    significant: p < 0.05,
    direction: slope > 0 ? "rising" : "falling",
    years: [years[0].year, years[years.length - 1].year],
  };
}

// ---------- This month vs normal ----------

const daysIn = (y: number, m: number) => new Date(Date.UTC(y, m, 0)).getUTCDate();

/**
 * How much of "this month" the numbers cover. Prefers the API's days_covered;
 * otherwise falls back to the search end date or today, whichever is earlier.
 */
function coverage(tm: SummaryResponse["this_month"], requestEnd: string) {
  const total = daysIn(tm.year, tm.month);
  if (tm.days_covered != null) {
    return { lastDay: Math.min(tm.days_covered, total), total, fromApi: true };
  }
  let lastDay = total;
  const [ey, em, ed] = requestEnd.split("-").map(Number);
  if (ey === tm.year && em === tm.month) lastDay = Math.min(lastDay, ed);
  const now = new Date();
  if (now.getUTCFullYear() === tm.year && now.getUTCMonth() + 1 === tm.month) {
    lastDay = Math.min(lastDay, now.getUTCDate());
  }
  return { lastDay, total, fromApi: false };
}

function ThisMonth({
  tm,
  requestEnd,
  unitText,
}: {
  tm: SummaryResponse["this_month"];
  requestEnd: string;
  unitText: string;
}) {
  const a = anomalyStyle(tm.anomaly);
  const cov = coverage(tm, requestEnd);
  const partial = cov.lastDay < cov.total;
  const mon = MONTHS[tm.month - 1];
  const monthLong = MONTHS_LONG[tm.month - 1];
  const period = partial ? `${mon} 1–${cov.lastDay}, ${tm.year}` : `${monthLong} ${tm.year}`;

  return (
    <article aria-label="This month compared with normal" className="flex flex-col gap-3">
      <p className="text-[13px] text-fl-muted">
        {partial ? (
          <>
            <span className="num text-fl-text">{period}</span> so far. Data through{" "}
            <span className="num text-fl-text">{cov.lastDay} {mon}</span>
            {!cov.fromApi && " (your end date)"}.
          </>
        ) : (
          <span className="num text-fl-text">{period}</span>
        )}
      </p>

      <p className="flex items-baseline gap-2">
        <span className="num text-[44px] font-light leading-none tracking-tight text-fl-text">
          {fmtInt(tm.count)}
        </span>
        <span className="text-sm text-fl-muted">hotspots, {unitText}</span>
      </p>
      {typeof tm.modis_only === "number" && tm.modis_only > 0 && (
        <p className="-mt-1 text-[13px] leading-snug text-fl-muted">
          Plus <span className="num text-fl-text">{fmtInt(tm.modis_only)}</span> detections only MODIS saw. VIIRS
          missed them, so they aren&apos;t in this count.
        </p>
      )}

      <p className="max-w-[52ch] text-base leading-relaxed text-fl-text">
        <strong className="font-medium" style={{ color: a.text }}>
          <span aria-hidden>{a.arrow} </span>
          {pctWords(tm.pct_diff)}
        </strong>{" "}
        {/* A part month is compared with the same days in other years (the API says which), not whole months. */}
        for {partial ? `${mon} 1–${cov.lastDay}` : monthLong}.{" "}
        {tm.compared_with ? `The median of ${tm.compared_with} is ` : "The median is "}
        <span className="num">{fmtInt(tm.median)}</span>.
        {partial && " The month is not over, so this count can still grow."}
      </p>

      {tm.same_days_past?.length ? (
        <PastYearsStrip tm={tm} color={a.text} what={tm.compared_with ?? `${monthLong} in other years`} />
      ) : (
        <RangeBar tm={tm} color={a.text} />
      )}
      {partial && tm.full_month_median != null && (
        <p className="text-xs text-fl-muted">
          A whole {monthLong} usually has <span className="num text-fl-text">{fmtInt(tm.full_month_median)}</span>{" "}
          (median).
        </p>
      )}
      {tm.preliminary && (
        <p className="text-xs text-fl-muted">
          <span className="text-fl-accent-text">Preliminary.</span> Near-real-time data; NASA finalises it about
          3 months later.
        </p>
      )}

      <dl className="grid grid-cols-2 gap-4">
        <Stat label="Middle half of years" value={`${fmtInt(tm.p25)}–${fmtInt(tm.p75)}`} />
        <Stat
          // The API's own verdict wins over the app's z-score bands when it sends one.
          label={tm.anomaly_label ? tm.anomaly_label[0].toUpperCase() + tm.anomaly_label.slice(1) : a.badge}
          value={`${tm.anomaly > 0 ? "+" : ""}${tm.anomaly.toFixed(1)}`}
          color={a.text}
        />
      </dl>
    </article>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <dt className="text-xs text-fl-muted">{label}</dt>
      <dd className="num text-[15px] text-fl-text" style={color ? { color } : undefined}>
        {value}
      </dd>
    </div>
  );
}

/** Track with the middle-50% band, a median tick and this month's marker. */
function RangeBar({ tm, color }: { tm: SummaryResponse["this_month"]; color: string }) {
  const max = Math.max(tm.count, tm.p75, tm.median) * 1.15 || 1;
  const pct = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  return (
    <div className="py-1.5" aria-hidden>
      <div className="relative h-1.5 rounded-full bg-fl-track">
        <div
          className="absolute inset-y-0 bg-fl-muted/40"
          style={{ left: pct(tm.p25), width: `calc(${pct(tm.p75)} - ${pct(tm.p25)})` }}
        />
        <div className="absolute -inset-y-1 w-px bg-fl-muted" style={{ left: pct(tm.median) }} />
        <div
          className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-fl-bg"
          style={{ left: pct(tm.count), background: color }}
        />
      </div>
    </div>
  );
}

/**
 * The comparison the median came from: one dot per earlier year (the same days
 * of the month when it's partial), the middle half as a band, the median as a
 * tick, and this year as the coloured dot. Hover a dot for its year.
 */
function PastYearsStrip({
  tm,
  color,
  what,
}: {
  tm: SummaryResponse["this_month"];
  color: string;
  what: string;
}) {
  const past = tm.same_days_past ?? [];
  const max = Math.max(tm.count, ...past.map((p) => p.count)) * 1.05 || 1;
  const pct = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  return (
    <figure className="flex flex-col gap-1.5">
      <div className="relative h-6" role="img" aria-label={`${past.length} earlier years of ${what}, with this year marked`}>
        <div className="absolute inset-x-0 top-1/2 h-px bg-fl-border" />
        <div
          className="absolute top-1/2 h-2 -translate-y-1/2 bg-fl-muted/25"
          style={{ left: pct(tm.p25), width: `calc(${pct(tm.p75)} - ${pct(tm.p25)})` }}
        />
        <div className="absolute inset-y-0.5 w-px bg-fl-muted" style={{ left: pct(tm.median) }} />
        {past.map((p) => (
          <span
            key={p.year}
            title={`${p.year}: ${fmtInt(p.count)}`}
            className="absolute top-1/2 size-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-fl-muted"
            style={{ left: pct(p.count) }}
          />
        ))}
        <span
          title={`${tm.year}: ${fmtInt(tm.count)}`}
          className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-fl-surface"
          style={{ left: pct(tm.count), background: color }}
        />
      </div>
      <figcaption className="text-xs text-fl-muted">
        Each grey dot is {what.replace(/ in other years$/, "")} in one earlier year ({past.length} years). The line is
        the median, the band the middle half.
      </figcaption>
    </figure>
  );
}

// ---------- History blocks ----------

function Block({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-[15px] font-medium text-fl-text">{title}</h3>
        {note && <span className="text-[13px] text-fl-muted">{note}</span>}
      </div>
      {children}
    </section>
  );
}

function RankList({ rows }: { rows: { label: string; value: number; blindSpot?: boolean }[] }) {
  const top = rows[0]?.value || 1;
  return (
    <ol className="flex flex-col gap-2">
      {rows.map((r) => (
        <li key={r.label} className="flex flex-col gap-1">
          <div className="flex items-baseline justify-between gap-2 text-sm">
            <span className="flex items-baseline gap-1.5 text-fl-text">
              {r.label}
              {r.blindSpot && (
                <abbr
                  title="Has blind-spot months: VIIRS would see at least 8× more fires than MODIS did, so the total leans hard on the conversion"
                  className="num inline-grid size-4 place-items-center self-center rounded-[3px] bg-fl-text text-[9px] font-bold text-fl-bg no-underline"
                >
                  B
                </abbr>
              )}
            </span>
            <span className="num text-fl-text">{fmtInt(r.value)}</span>
          </div>
          <div className="h-[3px] bg-fl-track">
            <div className="h-full bg-fl-accent" style={{ width: `${(r.value / top) * 100}%` }} />
          </div>
        </li>
      ))}
    </ol>
  );
}

function BusiestMonths({ items }: { items: SummaryResponse["busiest_months"] }) {
  return (
    <Block title="Busiest months" note="median">
      <RankList rows={items.map((m) => ({ label: MONTHS_LONG[m.month - 1], value: m.median }))} />
    </Block>
  );
}

/**
 * busiest years leave out blind-spot years by default: their totals lean on the
 * conversion most (punjab's 2001 tops the raw list for that reason). one click puts them back.
 */
function BusiestYears({
  items,
  noBlind,
  blindYears,
  caveat,
}: {
  items: SummaryResponse["busiest_years"];
  noBlind: { year: number; total: number }[];
  blindYears: Set<number>;
  caveat?: string | null;
}) {
  const [withBlind, setWithBlind] = useState(false);
  const dropped = items.filter((y) => blindYears.has(y.year)).map((y) => y.year);
  const rows = withBlind || dropped.length === 0 ? items : noBlind;
  return (
    <Block title="Busiest years" note="total">
      <RankList rows={rows.map((y) => ({ label: String(y.year), value: y.total, blindSpot: blindYears.has(y.year) }))} />
      {dropped.length > 0 && (
        <p className="text-xs leading-snug text-fl-muted">
          {withBlind
            ? "Including years with blind-spot months. "
            : `Leaves out ${dropped.join(" and ")}: ${dropped.length > 1 ? "years" : "a year"} with blind-spot months, which lean hard on the conversion. `}
          <button
            type="button"
            onClick={() => setWithBlind((v) => !v)}
            aria-pressed={withBlind}
            className="text-fl-text underline underline-offset-2 hover:text-fl-accent-text"
          >
            {withBlind ? "Leave them out" : "Include them"}
          </button>
        </p>
      )}
      {caveat && <p className="text-xs leading-snug text-fl-muted">{caveat}</p>}
    </Block>
  );
}

/** Months from start to end, wrapping past December (e.g. Nov to Feb). */
function seasonMonths(start: number, end: number) {
  const out = new Set<number>();
  let m = start;
  for (let i = 0; i < 12; i++) {
    out.add(m);
    if (m === end) break;
    m = (m % 12) + 1;
  }
  return out;
}

function FireSeason({ season }: { season: SummaryResponse["season"] }) {
  const inSeason = seasonMonths(season.start_month, season.end_month);
  return (
    <Block title="Fire season">
      <p className="text-sm text-fl-text">
        {MONTHS_LONG[season.start_month - 1]} to {MONTHS_LONG[season.end_month - 1]}, peaking in{" "}
        {MONTHS_LONG[season.peak_month - 1]}.
      </p>
      <div className="grid grid-cols-12 gap-0.5" aria-hidden>
        {MONTHS.map((name, i) => {
          const m = i + 1;
          const peak = m === season.peak_month;
          return (
            <div key={name} className="flex flex-col items-center gap-1">
              <div
                className={`h-4 w-full ${
                  peak ? "bg-fl-accent" : inSeason.has(m) ? "bg-fl-accent/35" : "bg-fl-track"
                }`}
              />
              <span className="num text-[10px] text-fl-muted">{name[0]}</span>
            </div>
          );
        })}
      </div>
    </Block>
  );
}

const median = (xs: number[]) => {
  const s = [...xs].sort((a, b) => a - b);
  const h = s.length >> 1;
  return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2;
};

/**
 * Long-term trend of full-year totals (Sen's slope, Mann-Kendall p from the API),
 * in words, with the yearly totals as bars and the trend as a line over them.
 */
function Trend({
  trend,
  viirs,
  annual,
  blindYears,
  caveat,
}: {
  trend: SummaryResponse["trend"];
  /** the trend from 2012 on, no conversion in it; null when there aren't enough years */
  viirs: TrendT | null;
  annual: { year: number; count: number }[];
  blindYears: Set<number>;
  caveat?: string | null;
}) {
  const slope = trend.sen_slope_per_year;
  const [y0, y1] = trend.years ?? [annual[0]?.year, annual[annual.length - 1]?.year];
  const years = annual.filter((a) => (y0 == null || a.year >= y0) && (y1 == null || a.year <= y1));
  const significant = trend.significant ?? trend.p_value < 0.05;
  const rising = slope > 0;
  const perYear = `${rising ? "+" : "−"}${fmtInt(Math.abs(slope))} hotspots a year`;
  const blindInRange = years.filter((a) => blindYears.has(a.year)).length;

  // Sen line: slope from the API, intercept = median of (count − slope × year).
  const max = Math.max(1, ...years.map((a) => a.count));
  const b = years.length ? median(years.map((a) => a.count - slope * a.year)) : 0;
  const yPct = (year: number) => 100 - (Math.max(0, b + slope * year) / max) * 100;

  return (
    <Block title="Trend" note={y0 != null && y1 != null ? `${y0}–${y1}, full years` : undefined}>
      <p className="text-sm leading-snug text-fl-text">
        {significant ? (
          <>
            {rising ? "Rising" : "Falling"}
            {trend.pct_per_decade != null ? ` by about ${Math.round(Math.abs(trend.pct_per_decade))}% per decade` : ""} (
            <span className="num">{perYear}</span>).
          </>
        ) : (
          <>No clear trend. Year-to-year swings are bigger than any steady change.</>
        )}{" "}
        <span className="text-fl-muted">
          <span className="num">{trend.p_value < 0.001 ? "p < 0.001" : `p = ${trend.p_value.toFixed(3)}`}</span>{" "}
          {significant ? "(under 0.05: unlikely to be chance)" : "(over 0.05: could be chance)"}
        </span>
      </p>
      {years.length > 1 && (
        <figure className="flex flex-col gap-1">
          <div
            className="relative flex h-12 items-end gap-[2px]"
            role="img"
            aria-label={`Yearly totals ${years[0].year} to ${years[years.length - 1].year}, with the trend line`}
          >
            {years.map((a) => (
              <div
                key={a.year}
                title={`${a.year}: ${fmtInt(a.count)}${blindYears.has(a.year) ? " (has blind-spot months)" : ""}`}
                className={`flex-1 rounded-t-[2px] ${blindYears.has(a.year) ? "bg-fl-muted/20" : "bg-fl-muted/45"}`}
                style={{ height: `${(a.count / max) * 100}%` }}
              />
            ))}
            <svg className="pointer-events-none absolute inset-0 size-full overflow-visible" viewBox={`0 0 ${years.length} 100`} preserveAspectRatio="none" aria-hidden>
              <line
                x1={0.5}
                x2={years.length - 0.5}
                y1={yPct(years[0].year)}
                y2={yPct(years[years.length - 1].year)}
                stroke="var(--fl-accent)"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            </svg>
          </div>
          <figcaption className="num flex justify-between text-[10px] text-fl-muted">
            <span>{years[0].year}</span>
            <span>{years[years.length - 1].year}</span>
          </figcaption>
        </figure>
      )}
      {blindInRange > 0 && (
        <p className="text-xs leading-snug text-fl-muted">
          {blindInRange} of these years have blind-spot months (paler bars), so their totals lean on the conversion.
        </p>
      )}
      {viirs && (
        <p className="border-t border-fl-border pt-3 text-sm leading-snug text-fl-text">
          <span className="text-fl-muted">
            From {viirs.years?.[0] ?? 2012} on, VIIRS only, no conversion:{" "}
          </span>
          {viirs.significant ?? viirs.p_value < 0.05 ? (
            <>
              {viirs.sen_slope_per_year > 0 ? "rising" : "falling"} by{" "}
              <span className="num">{fmtInt(Math.abs(viirs.sen_slope_per_year))}</span> hotspots a year
            </>
          ) : (
            <>no clear trend</>
          )}{" "}
          <span className="num text-fl-muted">
            ({viirs.p_value < 0.001 ? "p < 0.001" : `p = ${viirs.p_value.toFixed(3)}`})
          </span>
          .
        </p>
      )}
      {caveat && <p className="text-xs leading-snug text-fl-muted">{caveat}</p>}
    </Block>
  );
}

function ComingUp({ items }: { items: SummaryResponse["critical_next"] }) {
  return (
    <Block title="Coming up" note="from past years, not a forecast">
      {items.length === 0 ? (
        <p className="text-sm text-fl-muted">No historically busy months ahead.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((c) => (
            <li key={c.month} className="flex items-baseline justify-between gap-3 text-sm">
              <span className="text-fl-text">
                <span aria-hidden className="text-fl-accent-text">▲ </span>
                {MONTHS_LONG[c.month - 1]}, {c.note}
              </span>
              <span className="num shrink-0 text-fl-text">{fmtInt(c.median)}</span>
            </li>
          ))}
        </ul>
      )}
    </Block>
  );
}
