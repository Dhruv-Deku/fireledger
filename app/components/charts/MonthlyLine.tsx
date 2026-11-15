"use client";

import { useMemo, type Ref } from "react";
import ReactECharts from "echarts-for-react";
import "@/lib/echartsGuard";
import type { EChartsOption } from "echarts";
import type { MonthRow } from "@/lib/api";
import { MONTHS_LONG, chartFonts, fmtInt, prefersReducedMotion, themeColors, tooltipBase } from "@/lib/chartTheme";
import { useTheme } from "@/lib/theme";

type Props = {
  months: MonthRow[];
  harmonized: boolean;
  unitText: string;
  chartRef?: Ref<ReactECharts>;
};

const VIIRS_START = "2012-01-20"; // Suomi NPP VIIRS first data

const iso = (m: MonthRow) => `${m.year}-${String(m.month).padStart(2, "0")}-01`;

/**
 * Monthly series. Harmonized: one line with its lo–hi band (GWIS/GFW style).
 * Raw: raw MODIS and raw VIIRS as two lines, which shows the 2012 jump.
 */
export default function MonthlyLine({ months, harmonized, unitText, chartRef }: Props) {
  const { theme } = useTheme();
  const option = useMemo<EChartsOption>(() => {
    void theme; // redraw when the theme flips: colours are read from CSS at render
    const C = themeColors();
    const MODIS_COLOR = C.cool; // blue
    const VIIRS_COLOR = C.viirs; // orange (never red vs green)
    const { mono, sans } = chartFonts();
    const byDate = new Map(months.map((m) => [iso(m), m]));

    const axisCommon = {
      axisLine: { lineStyle: { color: C.border } },
      axisTick: { show: false },
      axisLabel: { color: C.muted, fontFamily: mono, fontSize: 11 },
      splitLine: { lineStyle: { color: C.border } },
    };

    const viirsMark = {
      silent: true,
      symbol: "none",
      lineStyle: { color: C.faint, type: "dashed" as const, width: 1 },
      label: { color: C.muted, fontFamily: mono, fontSize: 10, formatter: "VIIRS starts", position: "insideEndTop" as const },
      data: [{ xAxis: VIIRS_START }],
    };

    const series: EChartsOption["series"] = harmonized
      ? [
          {
            name: "lo",
            type: "line",
            stack: "band",
            data: months.map((m) => [iso(m), m.lo]),
            lineStyle: { opacity: 0 },
            symbol: "none",
            tooltip: { show: false },
          },
          {
            name: "Likely range",
            type: "line",
            stack: "band",
            data: months.map((m) => [iso(m), m.hi - m.lo]),
            lineStyle: { opacity: 0 },
            areaStyle: { color: C.band },
            symbol: "none",
          },
          {
            name: "Hotspots",
            type: "line",
            data: months.map((m) => [iso(m), m.count]),
            lineStyle: { color: C.accentText, width: 1.5 },
            itemStyle: { color: C.accentText },
            symbol: "none",
            markLine: viirsMark,
          },
        ]
      : [
          {
            name: "Raw MODIS",
            type: "line",
            data: months.map((m) => [iso(m), m.raw_modis]),
            lineStyle: { color: MODIS_COLOR, width: 1.5 },
            itemStyle: { color: MODIS_COLOR },
            symbol: "none",
            markLine: viirsMark,
          },
          {
            name: "Raw VIIRS",
            type: "line",
            // VIIRS didn't exist before 2012: leave a gap instead of drawing zeros.
            data: months.map((m) => [iso(m), m.year < 2012 ? null : m.raw_viirs]),
            lineStyle: { color: VIIRS_COLOR, width: 1.5 },
            itemStyle: { color: VIIRS_COLOR },
            symbol: "none",
          },
        ];

    return {
      animation: !prefersReducedMotion(),
      textStyle: { fontFamily: sans },
      grid: { top: 36, right: 16, bottom: 64, left: 56 },
      legend: harmonized
        ? { show: false }
        : {
            top: 0,
            right: 8,
            icon: "roundRect",
            itemWidth: 14,
            itemHeight: 3,
            textStyle: { color: C.muted, fontFamily: sans, fontSize: 12 },
          },
      xAxis: { type: "time", ...axisCommon, splitLine: { show: false } },
      yAxis: {
        type: "value",
        ...axisCommon,
        axisLine: { show: false },
        axisLabel: { ...axisCommon.axisLabel, formatter: (v: number) => fmtInt(v) },
      },
      dataZoom: [
        { type: "inside", filterMode: "none" },
        {
          type: "slider",
          height: 20,
          bottom: 8,
          filterMode: "none",
          borderColor: C.border,
          backgroundColor: "transparent",
          fillerColor: C.band,
          handleStyle: { color: C.accent, borderColor: C.accent },
          moveHandleStyle: { color: C.accent },
          dataBackground: { lineStyle: { color: C.faint }, areaStyle: { color: C.band } },
          selectedDataBackground: { lineStyle: { color: C.accent }, areaStyle: { color: C.band } },
          textStyle: { color: C.muted, fontFamily: mono, fontSize: 10 },
          labelFormatter: (v: number) => new Date(v).getUTCFullYear().toString(),
        },
      ],
      tooltip: {
        ...tooltipBase(mono, C),
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: C.faint } },
        formatter: (ps) => {
          const list = ps as unknown as { value: [string, number | null] }[];
          const date = list[0]?.value[0];
          const m = date ? byDate.get(date) : undefined;
          if (!m) return "";
          const head = `<div style="font-weight:500;margin-bottom:4px">${MONTHS_LONG[m.month - 1]} ${m.year}</div>`;
          const line = (k: string, v: string, color?: string) =>
            `<div style="display:flex;justify-content:space-between;gap:16px"><span style="color:${C.muted}">${
              color ? `<span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${color};margin-right:6px"></span>` : ""
            }${k}</span><span>${v}</span></div>`;
          return harmonized
            ? head +
                line("Hotspots", fmtInt(m.count), C.accentText) +
                line("Likely range", `${fmtInt(m.lo)} – ${fmtInt(m.hi)}`) +
                `<div style="color:${C.faint};font-size:11px;margin-top:2px">${unitText}</div>`
            : head +
                line("Raw MODIS", fmtInt(m.raw_modis), MODIS_COLOR) +
                line("Raw VIIRS", m.year < 2012 ? "—" : fmtInt(m.raw_viirs), VIIRS_COLOR);
        },
      },
      series,
    };
  }, [months, harmonized, unitText, theme]);

  return (
    <ReactECharts
      ref={chartRef}
      option={option}
      notMerge
      style={{ height: 300, width: "100%" }}
      opts={{ renderer: "canvas" }}
    />
  );
}
