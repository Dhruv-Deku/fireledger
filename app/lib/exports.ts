// Browser-side exports: monthly series CSV and calendar PNG.

import type { ECharts } from "echarts";
import { saveBlob, type MonthRow, type RequestBody } from "@/lib/api";
import { chartFonts, themeColors } from "@/lib/chartTheme";

/** "Punjab, India" -> "punjab-india" */
export function slug(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "area";
}

/** File name stem shared by all downloads, e.g. "fireledger_punjab-india_2000-11-01_2026-11-14". */
export function fileStem(label: string, req: RequestBody) {
  return `fireledger_${slug(label)}_${req.start}_${req.end}`;
}

const csvCell = (v: unknown) => {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

/** Contract columns first; the API's extra fields follow in the order it sends them. */
const SERIES_FIRST = ["year", "month", "count", "lo", "hi", "raw_modis", "raw_viirs", "flags"];

/**
 * Monthly series as CSV, built from the calendar rows already on screen. Keeps
 * every field the API sent (e.g. raw_snpp, raw_noaa20, industrial, source,
 * partial, preliminary), so nothing is lost between the API and the file.
 */
export function downloadSeriesCsv(months: MonthRow[], req: RequestBody, unit: string, filename: string) {
  const cols = [...SERIES_FIRST];
  for (const m of months) for (const k of Object.keys(m)) if (!cols.includes(k)) cols.push(k);
  const header = [...cols, "unit", "harmonized", "daynight", "hide_industrial"];
  const rows = months.map((m) => {
    const r = m as Record<string, unknown>;
    return [...cols.map((k) => r[k]), unit, req.harmonized, req.daynight, req.hide_industrial].map(csvCell).join(",");
  });
  saveBlob(new Blob([[header.join(","), ...rows].join("\n") + "\n"], { type: "text/csv" }), filename);
}

/**
 * Calendar PNG: the chart image (ECharts getDataURL) with a title, units and
 * the data credit drawn above/below it, so the file makes sense on its own.
 */
export async function downloadCalendarPng(
  chart: ECharts,
  opts: { title: string; subtitle: string; footer: string[]; filename: string },
) {
  const scale = 2;
  const C = themeColors(); // exported in the theme currently on screen
  const url = chart.getDataURL({ type: "png", pixelRatio: scale, backgroundColor: C.bg });
  const img = new Image();
  img.src = url;
  await img.decode();

  const pad = 24 * scale;
  const head = 64 * scale;
  const lineH = 16 * scale;
  const foot = (opts.footer.length * 16 + 16) * scale;
  const canvas = document.createElement("canvas");
  canvas.width = img.width + pad * 2;
  canvas.height = img.height + head + foot + pad;
  const ctx = canvas.getContext("2d")!;
  const { sans, mono } = chartFonts();

  ctx.fillStyle = C.bg;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.textBaseline = "top";
  ctx.fillStyle = C.text;
  ctx.font = `500 ${20 * scale}px ${sans}`;
  ctx.fillText(opts.title, pad, pad);
  ctx.fillStyle = C.muted;
  ctx.font = `400 ${13 * scale}px ${sans}`;
  ctx.fillText(opts.subtitle, pad, pad + 28 * scale);
  ctx.drawImage(img, pad, head + pad / 2);
  ctx.font = `400 ${11 * scale}px ${mono}`;
  opts.footer.forEach((line, i) => ctx.fillText(line, pad, canvas.height - foot + i * lineH));

  const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/png"));
  if (blob) saveBlob(blob, opts.filename);
}
