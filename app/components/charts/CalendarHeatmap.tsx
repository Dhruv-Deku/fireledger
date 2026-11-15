"use client";

import { useMemo, useState, type Ref } from "react";
import ReactECharts from "echarts-for-react";
import "@/lib/echartsGuard";
import type { EChartsOption } from "echarts";
import type { MonthRow } from "@/lib/api";
import { useTheme } from "@/lib/theme";
import {
  FLAG_TEXT,
  MONTHS,
  MONTHS_LONG,
  HATCH,
  chartFonts,
  fmtInt,
  prefersReducedMotion,
  themeColors,
  tooltipBase,
  type ThemeColors,
} from "@/lib/chartTheme";

type Cell = { value: [number, number, number]; row?: MonthRow; year: number; month: number; median?: number };
type Mode = "count" | "anomaly";

/** Anomaly colour: log2(count / median of the same month), clipped to 1/4x ... 4x. */
const ANOMALY_CLIP = 2;
// blue (less fire than normal) -> neutral -> orange (more); readable with red-green colour blindness
const ANOMALY_LOW = ["#2b6cb0", "#7fa7d4"];
const ANOMALY_HIGH = ["#e8955a", "#b4500e"];

function median(xs: number[]): number | undefined {
  if (!xs.length) return undefined;
  const a = [...xs].sort((x, y) => x - y);
  const h = a.length >> 1;
  return a.length % 2 ? a[h] : (a[h - 1] + a[h]) / 2;
}

function anomalyOf(count: number, med: number): number {
  if (med <= 0) return count > 0 ? ANOMALY_CLIP : 0;
  if (count <= 0) return -ANOMALY_CLIP;
  return Math.max(-ANOMALY_CLIP, Math.min(ANOMALY_CLIP, Math.log2(count / med)));
}

type Props = {
  months: MonthRow[];
  /** Request range (YYYY-MM-DD); sets which years get a row. */
  start: string;
  end: string;
  unitText: string;
  chartRef?: Ref<ReactECharts>;
};

/**
 * Burning activity calendar: months across, years down. Three layers:
 * months with fire (inferno ramp), months with zero (panel colour) and months
 * with no data at all (hatched), so "no fire" never looks like "no data".
 * "vs normal" recolours each month against the median of the same month over
 * the years shown (blue = less fire than normal, orange = more).
 */
export default function CalendarHeatmap({ months, start, end, unitText, chartRef }: Props) {
  const { theme } = useTheme();
  const [mode, setMode] = useState<Mode>("count");
  const { option, height, busiest, hasBlindSpots } = useMemo(() => {
    void theme; // redraw when the theme flips: colours are read from CSS at render
    const C = themeColors();
    const { mono, sans } = chartFonts();
    const y0 = Number(start.slice(0, 4));
    const y1 = Number(end.slice(0, 4));
    const years: number[] = [];
    for (let y = y0; y <= y1; y++) years.push(y);

    const byKey = new Map(months.map((m) => [`${m.year}-${m.month}`, m]));
    // normal = median of the same calendar month over the years shown (months still filling in left out)
    const medians = MONTHS.map((_, i) =>
      median(months.filter((m) => m.month === i + 1 && !m.partial).map((m) => m.count)),
    );
    const anomaly = mode === "anomaly";
    const fire: Cell[] = [];
    const zero: Cell[] = [];
    const missing: Cell[] = [];
    const blindSpots: [number, number][] = [];
    let max = 0;
    let busiest: MonthRow | null = null;

    years.forEach((year, yi) => {
      for (let month = 1; month <= 12; month++) {
        const row = byKey.get(`${year}-${month}`);
        const cell: Cell = { value: [month - 1, yi, row?.count ?? 0], row, year, month, median: medians[month - 1] };
        if (row?.flags.includes("B")) blindSpots.push([month - 1, yi]);
        if (!row) missing.push(cell);
        else if (anomaly) {
          // a month in progress can't be judged yet; a month no year has data for has no normal
          if (row.partial || cell.median === undefined) missing.push(cell);
          else fire.push({ ...cell, value: [month - 1, yi, anomalyOf(row.count, cell.median)] });
          if (row.count > max) {
            max = row.count;
            busiest = row;
          }
        } else if (row.count > 0) {
          fire.push(cell);
          if (row.count > max) {
            max = row.count;
            busiest = row;
          }
        } else zero.push(cell);
      }
    });

    const cellStyle = { borderColor: C.bg, borderWidth: 2, borderRadius: 3 };

    const option: EChartsOption = {
      animation: !prefersReducedMotion(),
      textStyle: { fontFamily: sans },
      grid: { top: 28, right: 12, bottom: 56, left: 48 },
      xAxis: {
        type: "category",
        data: MONTHS,
        position: "top",
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: C.muted, fontFamily: mono, fontSize: 11 },
        splitArea: { show: false },
      },
      yAxis: {
        type: "category",
        data: years.map(String),
        inverse: true, // oldest year at the top, reading down like a ledger
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: C.muted, fontFamily: mono, fontSize: 11 },
      },
      // ECharts needs a visualMap for every heatmap series. Only the first one
      // is a real (visible) colour scale; the other two pin a fixed colour.
      visualMap: [
        anomaly
          ? {
              type: "continuous",
              seriesIndex: 0,
              min: -ANOMALY_CLIP,
              max: ANOMALY_CLIP,
              inRange: { color: [...ANOMALY_LOW, C.zero, ...ANOMALY_HIGH] },
              orient: "horizontal",
              left: "center",
              bottom: 4,
              itemWidth: 10,
              itemHeight: 240,
              text: ["4× normal or more", "¼ of normal or less"],
              textGap: 8,
              textStyle: { color: C.muted, fontFamily: mono, fontSize: 11 },
              calculable: false,
            }
          : {
          type: "continuous",
          seriesIndex: 0,
          min: 0,
          max: Math.max(1, Math.ceil(max)),
          inRange: { color: C.inferno },
          orient: "horizontal",
          left: "center",
          bottom: 4,
          itemWidth: 10,
          itemHeight: 240,
          text: [fmtInt(max), "0"],
          textGap: 8,
          textStyle: { color: C.muted, fontFamily: mono, fontSize: 11 },
          calculable: false,
            },
        { type: "continuous", show: false, seriesIndex: 1, min: 0, max: 1, inRange: { color: [C.zero, C.zero] } },
        { type: "continuous", show: false, seriesIndex: 2, min: 0, max: 1, inRange: { color: [C.nodata, C.nodata] } },
      ],
      tooltip: {
        ...tooltipBase(mono, C),
        formatter: (p) => tooltipHtml((p as unknown as { data: Cell }).data, unitText, C, anomaly),
      },
      series: [
        { name: "fire", type: "heatmap", data: fire, itemStyle: cellStyle, emphasis: { itemStyle: { borderColor: C.text, borderWidth: 1 } } },
        { name: "zero", type: "heatmap", data: zero, itemStyle: { ...cellStyle, color: C.zero }, emphasis: { itemStyle: { borderColor: C.text, borderWidth: 1 } } },
        {
          name: "nodata",
          type: "heatmap",
          data: missing,
          itemStyle: { ...cellStyle, color: C.nodata, decal: { ...HATCH, color: C.hatch } },
          emphasis: { disabled: true },
        },
        {
          // "B" (blind spot) months: a letter in the cell's corner, so the
          // warning doesn't depend on colour. Silent, so hover reaches the cell.
          name: "blind-spot",
          type: "scatter",
          data: blindSpots,
          silent: true,
          symbolSize: 0,
          label: {
            show: true,
            formatter: "B",
            position: "inside",
            offset: [9, -4],
            color: "#ffffff",
            textBorderColor: "rgba(0,0,0,0.85)",
            textBorderWidth: 2,
            fontFamily: mono,
            fontSize: 9,
            fontWeight: "bold",
          },
          z: 3,
        },
      ],
    };

    return {
      option,
      height: years.length * 22 + 28 + 56,
      busiest: busiest as MonthRow | null,
      hasBlindSpots: blindSpots.length > 0,
    };
  }, [months, start, end, unitText, theme, mode]);

  return (
    <figure className="flex flex-col gap-2">
      <div className="flex items-center gap-1 text-xs" role="group" aria-label="Calendar colours">
        {(
          [
            ["count", "Counts"],
            ["anomaly", "vs normal"],
          ] as const
        ).map(([m, label]) => (
          <button
            key={m}
            type="button"
            aria-pressed={mode === m}
            onClick={() => setMode(m)}
            className={`rounded-md border border-fl-border px-2 py-0.5 ${
              mode === m ? "bg-fl-text text-fl-bg" : "text-fl-muted hover:text-fl-text"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      <ReactECharts
        ref={chartRef}
        option={option}
        notMerge
        style={{ height, width: "100%" }}
        opts={{ renderer: "canvas" }}
      />
      <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fl-muted">
        {mode === "anomaly" ? (
          <span>
            Each month against the median of the same month in the years shown: orange = more fire than normal,
            blue = less, grey = about normal. The month still in progress is left out.
          </span>
        ) : (
          <span className="flex items-center gap-1.5">
            <Swatch style={{ background: "var(--fl-zero)" }} /> Zero hotspots
          </span>
        )}
        <span className="flex items-center gap-1.5">
          <Swatch className="hatch" /> No data (outside range or not yet happened)
        </span>
      </figcaption>
      {hasBlindSpots && (
        <p className="flex items-start gap-2 text-xs leading-snug text-fl-muted">
          <span className="num mt-px inline-grid size-4 shrink-0 place-items-center rounded-[3px] bg-fl-text text-[9px] font-bold text-fl-bg">
            B
          </span>
          <span>
            <span className="text-fl-text">Blind spot.</span> VIIRS would see at least 8× more fires than MODIS
            did, so these months lean hard on the conversion. Read them with care.
          </span>
        </p>
      )}
      {busiest && (
        <p className="text-sm text-fl-muted">
          Busiest month in this range:{" "}
          <span className="num text-fl-text">
            {MONTHS_LONG[busiest.month - 1]} {busiest.year}
          </span>{" "}
          with <span className="num text-fl-text">{fmtInt(busiest.count)}</span> hotspots ({unitText}).
        </p>
      )}
    </figure>
  );
}

function Swatch({ className = "", style }: { className?: string; style?: React.CSSProperties }) {
  return (
    <span
      aria-hidden
      className={`inline-block size-3 shrink-0 rounded-[3px] border border-fl-border ${className}`}
      style={style}
    />
  );
}

function tooltipHtml(cell: Cell, unitText: string, C: ThemeColors, anomaly = false) {
  const title = `<div style="font-weight:500;margin-bottom:4px">${MONTHS_LONG[cell.month - 1]} ${cell.year}</div>`;
  const r = cell.row;
  if (!r) return `${title}<span style="color:${C.muted}">No data for this month</span>`;
  const flags = r.flags
    ? r.flags.split("").map((f) => `${f} = ${FLAG_TEXT[f] ?? "?"}`).join(", ")
    : "none";
  const line = (k: string, v: string) =>
    `<div style="display:flex;justify-content:space-between;gap:16px"><span style="color:${C.muted}">${k}</span><span>${v}</span></div>`;
  const note = (t: string) => `<div style="color:${C.accentText};font-size:11px;margin-top:4px">${t}</div>`;
  return (
    title +
    line("Hotspots", `${fmtInt(r.count)}`) +
    `<div style="color:${C.faint};font-size:11px;margin:-2px 0 4px">${unitText}</div>` +
    (anomaly && cell.median !== undefined && !r.partial
      ? line(`Normal ${MONTHS[cell.month - 1]} (median)`, fmtInt(cell.median)) +
        line("vs normal", cell.median > 0 ? `${r.count >= cell.median ? "+" : ""}${Math.round((r.count / cell.median - 1) * 100)}%` : "n/a")
      : "") +
    line("Likely range", `${fmtInt(r.lo)} – ${fmtInt(r.hi)}`) +
    line("Raw MODIS", fmtInt(r.raw_modis)) +
    line("Raw VIIRS", fmtInt(r.raw_viirs)) +
    (r.industrial && r.industrial > 0 && r.count > 0
      ? line("Industrial", `${fmtInt(r.industrial)} (${Math.round((r.industrial / r.count) * 100)}%)`)
      : "") +
    (typeof r.modis_only === "number" && r.modis_only > 0 ? line("Only MODIS saw", fmtInt(r.modis_only)) : "") +
    (r.source ? line("From", r.source) : "") +
    line("Flags", flags) +
    (r.partial ? note(`Month in progress: data to day ${r.through_day ?? "?"}`) : "") +
    (r.preliminary ? note("Preliminary: NASA finalises it about 3 months later") : "") +
    (r.flags.includes("B") ? note("Blind spot: leans hard on the conversion") : "")
  );
}
