"use client";

import { useEffect, useState, type RefObject } from "react";
import type ReactECharts from "echarts-for-react";
import { Download, Loader2 } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { downloadCsv, isGridOnly, USING_MOCK } from "@/lib/api";
import { unitLabel } from "@/lib/chartTheme";
import { downloadCalendarPng, downloadSeriesCsv, fileStem } from "@/lib/exports";
import type { DataState } from "./Workspace";

type Ready = Extract<DataState, { status: "ready" }>;
type Kind = "hotspots" | "hotspots-full" | "series" | "png";

export default function DownloadMenu({
  label,
  data,
  calendarRef,
}: {
  label: string;
  data: DataState;
  calendarRef: RefObject<ReactECharts | null>;
}) {
  const [busy, setBusy] = useState<Kind | null>(null);
  const [error, setError] = useState<string | null>(null);
  // a grid-only server can't make the hotspot csv; say so instead of failing on click
  const [gridOnly, setGridOnly] = useState(false);
  useEffect(() => {
    isGridOnly()
      .then(setGridOnly)
      .catch(() => {});
  }, []);
  // the message floats over the panel, so it goes away on its own
  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 6000);
    return () => clearTimeout(t);
  }, [error]);
  const ready = data.status === "ready" ? (data as Ready) : null;

  async function run(kind: Kind, fn: (d: Ready) => Promise<void> | void) {
    if (!ready) return;
    setBusy(kind);
    setError(null);
    try {
      await fn(ready);
    } catch (e) {
      setError((e as Error).message || "Download failed.");
    } finally {
      setBusy(null);
    }
  }

  const stem = ready ? fileStem(label, ready.request) : "";
  const units = ready ? unitLabel(ready.calendar.unit, ready.request.harmonized) : "";

  return (
    <div className="relative">
      <DropdownMenu>
        <DropdownMenuTrigger
          disabled={!ready || busy !== null}
          className="flex h-8 items-center gap-1.5 rounded-md border border-fl-border px-3 text-[13px] text-fl-text transition-colors hover:border-fl-faint disabled:cursor-not-allowed disabled:text-fl-faint"
        >
          {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Download className="size-4" aria-hidden />}
          Download
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-72 p-1">
          <Item
            tag="CSV"
            title="Hotspots"
            hint={
              USING_MOCK
                ? "Sample rows only"
                : gridOnly
                  ? "Not on this server: it has monthly totals only, not single hotspots"
                  : "Every hotspot in this area and period, with its weight"
            }
            disabled={gridOnly}
            onClick={() => run("hotspots", (d) => downloadCsv(d.request, `${stem}_hotspots.csv`))}
          />
          {!USING_MOCK && !gridOnly && (
            <Item
              tag="CSV"
              title="Hotspots, every field"
              hint="Adds pixel size, brightness, the weight's 90% range and which rows count"
              onClick={() => run("hotspots-full", (d) => downloadCsv(d.request, `${stem}_hotspots_full.csv`, true))}
            />
          )}
          <Item
            tag="CSV"
            title="Monthly totals"
            hint="One row per month, every field the API sends"
            onClick={() =>
              run("series", (d) =>
                downloadSeriesCsv(d.calendar.months, d.request, d.calendar.unit, `${stem}_monthly.csv`),
              )
            }
          />
          <Item
            tag="PNG"
            title="Calendar image"
            hint="The burning calendar, ready to share"
            onClick={() =>
              run("png", async () => {
                const chart = calendarRef.current?.getEchartsInstance();
                if (!chart) throw new Error("The calendar isn't on screen yet.");
                await downloadCalendarPng(chart, {
                  title: `Burning activity calendar, ${label}`,
                  subtitle: `Hotspots per month (${units}), ${ready!.request.start} to ${ready!.request.end}`,
                  footer: [
                    ...(USING_MOCK ? ["SAMPLE DATA, NOT REAL"] : []),
                    "Data: NASA FIRMS (MODIS C6.1, VIIRS 375 m)",
                    "Built for NASA Space Apps Challenge 2026",
                  ],
                  filename: `${stem}_calendar.png`,
                });
              })
            }
          />
        </DropdownMenuContent>
      </DropdownMenu>
      {error && (
        <p role="alert" className="absolute right-0 top-full z-20 mt-2 w-56 rounded-md border border-fl-border bg-fl-surface px-3 py-2 text-xs text-fl-accent-text">
          {error}
        </p>
      )}
    </div>
  );
}

function Item({
  tag,
  title,
  hint,
  onClick,
  disabled = false,
}: {
  tag: string;
  title: string;
  hint: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <DropdownMenuItem onClick={onClick} disabled={disabled} className="items-start justify-between gap-4 px-3 py-2">
      <span className="flex flex-col">
        <span className="text-[13px] text-fl-text">{title}</span>
        <span className="text-xs text-fl-muted">{hint}</span>
      </span>
      <span className="num mt-0.5 text-[11px] text-fl-muted">{tag}</span>
    </DropdownMenuItem>
  );
}
