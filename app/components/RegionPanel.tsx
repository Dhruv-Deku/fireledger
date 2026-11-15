"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import Link from "next/link";
import type ReactECharts from "echarts-for-react";
import { X } from "lucide-react";
import { getProof, isGridOnly, USING_MOCK, type SummaryResponse } from "@/lib/api";
import { MONTHS, unitLabel } from "@/lib/chartTheme";
import { useTheme } from "@/lib/theme";
import { areaKm2, type AreaOfInterest } from "@/lib/geo";
import { pctSigned } from "@/lib/reliability";
import type { DataState } from "./Workspace";
import CalendarHeatmap from "./charts/CalendarHeatmap";
import MonthlyLine from "./charts/MonthlyLine";
import DailyCard from "./charts/DailyCard";
import { SummaryHeadline, SummaryHistory } from "./SummaryCards";
import DownloadMenu from "./DownloadMenu";

type Props = {
  aoi: AreaOfInterest;
  data: DataState;
  onClose: () => void;
  /** turns on "Hide industrial hotspots" in the filters */
  onHideIndustrial?: () => void;
};

/** "2000-11-01" -> "Nov 2000" */
const monthYear = (d: string) => `${MONTHS[Number(d.slice(5, 7)) - 1]} ${d.slice(0, 4)}`;

/** The highlighted region's results, beside the globe. */
export default function RegionPanel({ aoi, data, onClose, onHideIndustrial }: Props) {
  // The calendar's ECharts instance, for the "Calendar PNG" download.
  const calendarRef = useRef<ReactECharts>(null);
  const ready = data.status === "ready" || data.status === "empty" ? data : null;

  return (
    <section
      data-region-panel
      aria-label={`Results for ${aoi.label}`}
      // Docked on the right edge (Watch Duty / Worldview style). Below lg it
      // slides over the map instead, full height, so the map stays usable.
      className="scrollbar-dark @container absolute inset-y-0 right-0 z-30 flex w-full flex-col overflow-y-auto border-l border-fl-border bg-fl-surface sm:w-[480px] lg:static lg:z-auto lg:w-[min(540px,36vw)] lg:shrink-0"
    >
      <header className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b border-fl-border bg-fl-surface px-6 py-4">
        <div className="min-w-0">
          <h2 className="truncate text-[22px] font-medium leading-tight tracking-tight text-fl-text">{aoi.label}</h2>
          <p className="mt-1 truncate text-[13px] text-fl-muted">
            {ready
              ? <>
                  {ready.request.harmonized
                    ? `${unitLabel(ready.calendar.unit, true)} hotspots`
                    : "Raw hotspot counts, not harmonized"}
                  , {monthYear(ready.request.start)} to {monthYear(ready.request.end)}
                </>
              : " "}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <DownloadMenu label={aoi.label} data={data} calendarRef={calendarRef} />
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-fl-muted transition-colors hover:text-fl-text"
            aria-label="Close results"
          >
            <X className="size-5" />
          </button>
        </div>
      </header>

      <div className="flex flex-col gap-8 px-6 py-6" aria-live="polite">
        {(data.status === "loading" || data.status === "idle") && <Skeleton />}

        {data.status === "error" && (
          <div className="text-sm text-fl-text" role="alert">
            <p className="font-medium text-fl-accent-text">Results did not load.</p>
            <p className="mt-0.5 text-fl-muted">{data.message}</p>
          </div>
        )}

        {data.status === "empty" && <Empty data={data} />}

        {data.status === "ready" && <Ready data={data} calendarRef={calendarRef} onHideIndustrial={onHideIndustrial} />}
      </div>
    </section>
  );
}

/** a grid-only server has monthly totals per 0.25° cell, not single hotspots */
function useGridOnly() {
  const [gridOnly, setGridOnly] = useState(false);
  useEffect(() => {
    isGridOnly()
      .then(setGridOnly)
      .catch(() => {});
  }, []);
  return gridOnly;
}

/** a 0.25° cell is 600-800 km²; areas under a few cells may hold no cell at all on a grid-only server */
const GRID_SMALL_KM2 = 3000;
const GRID_NOTE =
  "This server counts whole 0.25° grid cells (about 28 km across), so the edges of a small area are approximate and an area smaller than one cell can come back empty even where fires burn.";

/** No hotspot at all in the area and dates. */
function Empty({ data }: { data: Extract<DataState, { status: "empty" }> }) {
  const gridOnly = useGridOnly();
  const small = areaKm2(data.request.geometry) < GRID_SMALL_KM2;
  return (
    <div className="text-sm text-fl-muted" role="status">
      <p className="font-medium text-fl-text">No hotspots here.</p>
      <p className="mt-0.5">
        No satellite detected a fire in this area from {monthYear(data.request.start)} to{" "}
        {monthYear(data.request.end)}.
      </p>
      {gridOnly && small && <p className="mt-2">{GRID_NOTE} Draw a larger area to be sure.</p>}
    </div>
  );
}

/** Shapes of the real layout, so nothing jumps when the numbers arrive. */
function Skeleton() {
  return (
    <div role="status" className="flex animate-pulse flex-col gap-8" aria-label="Loading results">
      <div className="flex flex-col gap-3">
        <div className="h-3 w-40 rounded bg-fl-track" />
        <div className="h-11 w-48 rounded bg-fl-track" />
        <div className="h-3 w-72 max-w-full rounded bg-fl-track" />
      </div>
      <div className="h-56 rounded-md bg-fl-track" />
      <div className="h-40 rounded-md bg-fl-track" />
    </div>
  );
}

function SectionHead({ title, note }: { title: string; note: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <h3 className="text-[17px] font-medium tracking-tight text-fl-text">{title}</h3>
      <p className="text-sm text-fl-muted">{note}</p>
    </div>
  );
}

type Reliability = NonNullable<SummaryResponse["reliability"]>;

/**
 * Calendar heading with the "2012 switch" badge from the API (summary.reliability):
 * how well this area's record holds where MODIS hands over to VIIRS. Neutral for
 * good and fair, amber for caution, never red: a caveat, not an error. Click it
 * for the API's one-line explanation.
 */
function CalendarHead({ note, reliability }: { note: string; reliability: Reliability | null | undefined }) {
  const [open, setOpen] = useState(false);
  const caution = reliability?.verdict === "caution";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-3">
        <SectionHead title="Burning calendar" note={note} />
        {reliability && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            title={reliability.note}
            className={`mt-0.5 flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors ${
              caution ? "border-[var(--fl-anom-4)] text-fl-text" : "border-fl-border text-fl-muted hover:text-fl-text"
            }`}
          >
            <span
              aria-hidden
              className="size-1.5 rounded-full"
              style={{ background: caution ? "var(--fl-anom-4)" : "var(--fl-muted)" }}
            />
            2012 switch: {reliability.verdict}
          </button>
        )}
      </div>
      {reliability && open && (
        <p className="text-[13px] leading-snug text-fl-muted">
          {reliability.note}{" "}
          {reliability.leftover_2012_range && (
            <>
              Likely range{" "}
              <span className="num text-fl-text">
                {pctSigned(reliability.leftover_2012_range[0], 1)} to {pctSigned(reliability.leftover_2012_range[1], 1)}
              </span>
              .{" "}
            </>
          )}
          {reliability.noise_2012 != null && (
            <>
              The same check inside the overlap years misses by{" "}
              <span className="num text-fl-text">{(reliability.noise_2012 * 100).toFixed(1)}%</span> here, so that is
              the noise to compare against.{" "}
            </>
          )}
          <Link href="/accuracy" className="text-fl-text underline underline-offset-2 hover:text-fl-accent-text">
            How accurate is this?
          </Link>
        </p>
      )}
    </div>
  );
}

/** areas under about one 1° cell get a note that single-cell errors apply */
const SMALL_AREA_KM2 = 12000;

function Ready({
  data,
  calendarRef,
  onHideIndustrial,
}: {
  data: Extract<DataState, { status: "ready" }>;
  calendarRef: RefObject<ReactECharts | null>;
  onHideIndustrial?: () => void;
}) {
  const { calendar, request, summary } = data;
  const unitText = unitLabel(calendar.unit, request.harmonized);
  // The inferno ramp runs dark→bright on dark, and pale→dark on light.
  const { theme } = useTheme();
  const moreIs = theme === "light" ? "Darker" : "Brighter";

  const km2 = areaKm2(request.geometry);
  const gridOnly = useGridOnly();
  const [errors, setErrors] = useState<{ cell: number; region: number } | null>(null);
  useEffect(() => {
    if (km2 >= SMALL_AREA_KM2) return;
    getProof()
      .then((p) => {
        const ours = p?.ablation.find((a) => /FireLedger/i.test(a.step));
        if (ours) setErrors({ cell: ours.wape_cell_month, region: ours.wape_region_month });
      })
      .catch(() => {});
  }, [km2]);

  // years either side of 2012 compare less exactly where the switch test isn't "good"
  const verdict = summary.reliability?.verdict;
  const spans2012 = request.start < "2012-01-01" && request.end >= "2012-01-01";
  const switchCaveat =
    spans2012 && (verdict === "fair" || verdict === "caution")
      ? `Years before and after 2012 compare less exactly here (2012 switch: ${verdict}).`
      : null;

  if (calendar.months.length === 0) {
    return (
      <div className="text-sm text-fl-muted">
        <p className="font-medium text-fl-text">No months in this date range.</p>
        <p className="mt-0.5">Set a start date before the end date in Filters.</p>
      </div>
    );
  }

  return (
    <>
      {/* This month first, then the calendar the challenge asks for, then the history. */}
      <SummaryHeadline
        summary={summary}
        requestEnd={request.end}
        unitText={unitText}
        onHideIndustrial={request.hide_industrial ? undefined : onHideIndustrial}
        smallArea={
          km2 < SMALL_AREA_KM2
            ? { km2, cellError: errors?.cell, regionError: errors?.region, gridNote: gridOnly ? GRID_NOTE : undefined }
            : null
        }
      />

      <DailyCard request={request} />

      <div className="flex flex-col gap-3 border-t border-fl-border pt-6">
        <CalendarHead
          note={`Hotspots per month, ${unitText}. ${moreIs} means more.`}
          reliability={data.summary.reliability}
        />
        <CalendarHeatmap
          months={calendar.months}
          start={request.start}
          end={request.end}
          unitText={unitText}
          chartRef={calendarRef}
        />
      </div>

      <SummaryHistory summary={summary} months={calendar.months} switchCaveat={switchCaveat} />

      <div className="flex flex-col gap-3 border-t border-fl-border pt-6">
        <SectionHead
          title="Month by month"
          note={
            request.harmonized
              ? "Line: hotspots per month. Band: likely range."
              : "Raw counts per sensor. The 2012 jump is VIIRS joining, not more fire."
          }
        />
        <MonthlyLine months={calendar.months} harmonized={request.harmonized} unitText={unitText} />
      </div>

      <p className="border-t border-fl-border pt-4 text-xs text-fl-muted">
        {USING_MOCK ? "Sample data, not real. " : ""}
        Data: NASA FIRMS (MODIS C6.1, VIIRS 375 m), harmonized by FireLedger.
      </p>
    </>
  );
}
