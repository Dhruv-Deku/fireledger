"use client";

import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import "@/lib/echartsGuard";
import type { EChartsOption } from "echarts";
import { getDaily, type DailyResponse, type DailySummary, type RequestBody } from "@/lib/api";
import { MONTHS, chartFonts, fmtInt, prefersReducedMotion, themeColors, tooltipBase } from "@/lib/chartTheme";
import { useTheme } from "@/lib/theme";

const ordinal = (n: number) => {
  const s = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${s}`;
};

const versus = (s: DailySummary) => {
  const p = Math.round(s.pct_diff);
  const words = Math.abs(p) < 5 ? "about normal" : `${Math.abs(p)}% ${p > 0 ? "above" : "below"} normal`;
  return `${words} (${ordinal(s.rank)} of ${s.of_years} years)`;
};

const dayLabel = (d: string) => `${Number(d.slice(8, 10))} ${MONTHS[Number(d.slice(5, 7)) - 1]}`;

/**
 * last 30 days with data, each day against the same calendar day in earlier
 * years (POST /api/daily). hidden if the call fails or comes back empty.
 */
export default function DailyCard({ request }: { request: RequestBody }) {
  const [data, setData] = useState<DailyResponse | null>(null);
  const { theme } = useTheme();

  useEffect(() => {
    const ctrl = new AbortController();
    getDaily(request, ctrl.signal)
      .then(setData)
      .catch(() => setData(null));
    return () => ctrl.abort();
  }, [request]);

  const option = useMemo<EChartsOption | null>(() => {
    if (!data?.days.length) return null;
    void theme;
    const C = themeColors();
    const { mono, sans } = chartFonts();
    const days = data.days;
    const x = days.map((d) => d.date);
    return {
      animation: !prefersReducedMotion(),
      textStyle: { fontFamily: sans },
      grid: { top: 12, right: 8, bottom: 28, left: 44 },
      xAxis: {
        type: "category",
        data: x,
        axisLine: { lineStyle: { color: C.border } },
        axisTick: { show: false },
        axisLabel: { color: C.muted, fontFamily: mono, fontSize: 10, interval: 6, formatter: (v: string) => dayLabel(v) },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: C.muted, fontFamily: mono, fontSize: 10, formatter: (v: number) => fmtInt(v) },
        splitLine: { lineStyle: { color: C.border } },
      },
      tooltip: {
        trigger: "axis",
        ...tooltipBase(mono, C),
        formatter: (ps) => {
          const i = (Array.isArray(ps) ? ps[0] : ps).dataIndex as number;
          const d = days[i];
          const line = (k: string, v: string) =>
            `<div style="display:flex;justify-content:space-between;gap:16px"><span style="color:${C.muted}">${k}</span><span>${v}</span></div>`;
          return (
            `<div style="font-weight:500;margin-bottom:4px">${dayLabel(d.date)} ${d.date.slice(0, 4)}</div>` +
            line("Hotspots", d.count == null ? "no satellite pass" : fmtInt(d.count)) +
            line("Normal (median)", fmtInt(d.median)) +
            line("Middle half of years", `${fmtInt(d.p25)}–${fmtInt(d.p75)}`) +
            (d.pct_diff != null ? line("Versus normal", `${d.pct_diff > 0 ? "+" : ""}${Math.round(d.pct_diff)}%`) : "") +
            line("Raw MODIS / VIIRS", `${fmtInt(d.raw_modis)} / ${fmtInt(d.raw_viirs)}`) +
            (d.raw_modis > d.raw_viirs
              ? `<div style="color:${C.accentText};font-size:11px;margin-top:4px;max-width:240px;white-space:normal">MODIS (4 pm pass) saw more: burning may have moved after VIIRS's 1:45 pm pass</div>`
              : "")
          );
        },
      },
      series: [
        // grey band p25 to p75: an invisible base plus the band on top of it
        { type: "line", data: days.map((d) => d.p25), stack: "band", symbol: "none", lineStyle: { opacity: 0 }, silent: true },
        {
          type: "line",
          data: days.map((d) => d.p75 - d.p25),
          stack: "band",
          symbol: "none",
          lineStyle: { opacity: 0 },
          areaStyle: { color: C.band },
          silent: true,
        },
        {
          type: "bar",
          data: days.map((d) => d.count),
          barWidth: "60%",
          itemStyle: { color: C.inferno[3], borderRadius: [2, 2, 0, 0] },
        },
        { type: "line", data: days.map((d) => d.median), symbol: "none", lineStyle: { color: C.text, width: 1.5, type: "dashed" } },
      ],
    };
  }, [data, theme]);

  if (!data?.days.length || !option) return null;
  return (
    <div className="flex flex-col gap-3 border-t border-fl-border pt-6">
      <div className="flex flex-col gap-0.5">
        <h3 className="text-[17px] font-medium tracking-tight text-fl-text">Last 30 days</h3>
        <p className="text-[15px] text-fl-text">Last 7 days: {versus(data.last_7_days)}</p>
        <p className="text-sm text-fl-muted">Last 30 days: {versus(data.last_30_days)}</p>
      </div>
      <ReactECharts option={option} style={{ height: 200 }} notMerge opts={{ renderer: "canvas" }} />
      <p className="text-xs leading-snug text-fl-muted">
        Bars: hotspots each day, {dayLabel(data.window.start)} to {dayLabel(data.window.end)}. Dashed line: normal
        (median). Grey band: the middle half of years. Each day compared with the same day in 2000–2025.
        Near-real-time data.
      </p>
    </div>
  );
}
