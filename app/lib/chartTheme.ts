// Shared colours, labels and helpers for the ECharts charts.

import type { Unit } from "@/lib/api";

/**
 * Dark-theme values, kept for backwards compatibility. These do NOT follow the
 * theme: chart code should use themeColors() (read at render time) instead.
 */
export const C = {
  bg: "#070b14",
  text: "#ece7dc",
  muted: "#9aa3b5",
  faint: "#687287",
  accent: "#ffb547",
  cool: "#7fb2ff",
  border: "rgba(236,231,220,0.1)",
  grid: "rgba(236,231,220,0.06)",
  /** Zero-fire cells: the panel colour, so they read as "nothing". */
  zero: "#141c2c",
  /** Base under the hatch for months with no data. */
  nodata: "#0a101b",
  tooltipBg: "rgba(12,18,32,0.96)",
} as const;

/** Inferno, darkest stops skipped so low counts stay visible on black. (Dark theme.) */
export const INFERNO = ["#57106e", "#8a226a", "#bc3754", "#e45a31", "#f98e09", "#f9cb35", "#fcffa4"];

/** Light theme: runs pale to dark, palest stop trimmed, so every value shows on a light page. */
export const INFERNO_LIGHT = ["#f9cb35", "#f98e09", "#e45a31", "#bc3754", "#8a226a", "#57106e"];

export type ThemeColors = {
  theme: "dark" | "light";
  bg: string;
  surface: string;
  text: string;
  muted: string;
  faint: string;
  accent: string;
  accentText: string;
  cool: string;
  border: string;
  zero: string;
  nodata: string;
  inferno: string[];
  /** Hatch stripe colour for no-data cells. */
  hatch: string;
  /** Fill for the likely-range band. */
  band: string;
  /** Raw VIIRS line colour (orange, readable in each theme). */
  viirs: string;
};

/**
 * Current theme's colours, read from the CSS tokens on <html> at call time
 * (canvas cannot use CSS variables). Call it inside the chart's useMemo and
 * list useTheme().theme in the deps so charts redraw when the theme flips.
 */
export function themeColors(): ThemeColors {
  if (typeof document === "undefined") {
    return {
      theme: "dark", bg: C.bg, surface: "#0c1220", text: C.text, muted: C.muted, faint: C.faint, accent: C.accent,
      accentText: C.accent, cool: C.cool, border: C.border, zero: C.zero, nodata: C.nodata, inferno: INFERNO,
      hatch: "rgba(255,255,255,0.14)", band: "rgba(255,255,255,0.10)", viirs: "#f98e09",
    };
  }
  const root = document.documentElement;
  const theme = root.getAttribute("data-theme") === "light" ? "light" : "dark";
  const cs = getComputedStyle(root);
  const v = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback;
  const light = theme === "light";
  return {
    theme,
    bg: v("--fl-bg", C.bg),
    surface: v("--fl-surface", "#0c1220"),
    text: v("--fl-text", C.text),
    muted: v("--fl-muted", C.muted),
    faint: v("--fl-faint", C.faint),
    accent: v("--fl-accent", C.accent),
    accentText: v("--fl-accent-text", C.accent),
    cool: v("--fl-cool", C.cool),
    border: v("--fl-border", C.border),
    zero: v("--fl-zero", C.zero),
    nodata: v("--fl-nodata", C.nodata),
    inferno: light ? INFERNO_LIGHT : INFERNO,
    hatch: light ? "rgba(14,22,38,0.18)" : "rgba(236,231,220,0.14)",
    band: light ? "rgba(14,22,38,0.08)" : "rgba(236,231,220,0.10)",
    viirs: light ? "#c2410c" : "#f98e09",
  };
}

export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const MONTHS_LONG = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** Colour-blind-safe anomaly ramp (dark values): blue = quieter, grey = normal, orange = busier. */
export const ANOMALY = ["#2563eb", "#60a5fa", "#27272a", "#f98e09", "#ff4d00"];

/**
 * Colour + words for a robust z-score (anomaly). Bands follow the Technical
 * Blueprint: |z| < 1.5 normal, 1.5–3 unusual, > 3 extreme.
 */
export function anomalyStyle(z: number) {
  // CSS variable strings: valid in inline styles, and they follow the theme.
  const step = z <= -1.5 ? 1 : z <= -0.5 ? 2 : z < 0.5 ? 3 : z < 1.5 ? 4 : 5;
  const fill = `var(--fl-anom-${step})`;
  // Text needs its own tokens: the bright ramp steps and the grey step are not
  // readable as text in both themes.
  const text = step <= 2 ? "var(--fl-cool)" : step === 3 ? "var(--fl-muted)" : "var(--fl-accent-text)";
  const a = Math.abs(z);
  const badge = a >= 3 ? "Extreme" : a >= 1.5 ? "Unusual" : "Within normal range";
  return { fill, text, badge, arrow: z >= 0.5 ? "▲" : z <= -0.5 ? "▼" : "●" };
}

/** "42% above normal" / "18% below normal" / "about normal". */
export function pctWords(pct: number) {
  const r = Math.round(Math.abs(pct));
  if (r < 5) return "about normal";
  return `${r}% ${pct > 0 ? "above" : "below"} normal`;
}

export const FLAG_TEXT: Record<string, string> = {
  M: "MODIS-only years",
  C: "cross-calibrated",
  D: "orbit drift",
  B: "blind spot",
};

export function unitLabel(unit: Unit, harmonized: boolean) {
  if (!harmonized) return "raw, not harmonized";
  return unit === "viirs" ? "VIIRS-equivalent" : "MODIS-equivalent";
}

export const fmtInt = (v: number) => Math.round(v).toLocaleString("en-US");

/** Font families as next/font registered them (canvas can't read CSS variables). */
export function chartFonts() {
  if (typeof window === "undefined") return { sans: "sans-serif", mono: "monospace" };
  const cs = getComputedStyle(document.documentElement);
  return {
    sans: cs.getPropertyValue("--font-archivo").trim() || "sans-serif",
    // no monospace anymore: numbers use archivo too (canvas can't set tabular-nums; archivo's figures are close)
    mono: cs.getPropertyValue("--font-archivo").trim() || "sans-serif",
  };
}

export const prefersReducedMotion = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Diagonal hatch (ECharts decal) for "no data" cells (dark colour; charts override `color` per theme). */
export const HATCH = {
  symbol: "rect",
  color: "rgba(255,255,255,0.14)",
  dashArrayX: [1, 0],
  dashArrayY: [1.5, 3.5],
  rotation: -Math.PI / 4,
};

/** Common tooltip styling. */
export function tooltipBase(mono: string, t: ThemeColors = themeColors()) {
  return {
    backgroundColor: t.surface,
    borderColor: t.border,
    borderWidth: 1,
    padding: [8, 10],
    textStyle: { color: t.text, fontFamily: mono, fontSize: 12 },
    extraCssText: "border-radius:10px;box-shadow:none;backdrop-filter:blur(8px);",
  };
}
