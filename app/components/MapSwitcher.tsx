"use client";

import { ChevronDown } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Base, MapView, Projection } from "@/lib/mapPrefs";

const PROJECTIONS: { value: Projection; label: string; hint: string }[] = [
  { value: "auto", label: "Auto", hint: "Globe when zoomed out, flat map up close" },
  { value: "globe", label: "3D globe", hint: "Always a sphere" },
  { value: "flat", label: "Flat map", hint: "2D, easiest for comparing regions" },
];

/**
 * Each map with a small swatch in its typical colours, so the list can be
 * scanned by eye. The swatches are hints, not data.
 */
const BASES: { value: Base; label: string; hint: string; swatch: string }[] = [
  {
    value: "physical",
    label: "Physical",
    hint: "NASA Blue Marble: land in true colour, relief and ocean floor",
    swatch: "linear-gradient(135deg, #1d3b5c 0 30%, #3f6a3c 45%, #9a8a5e 75%, #e9e4da)",
  },
  {
    value: "satellite",
    label: "Satellite",
    hint: "Sentinel-2 cloudless, 10 m: fields and burn scars up close",
    swatch: "linear-gradient(135deg, #2f3b27 0 35%, #6f6b4b 60%, #a69a7a 80%, #33465a)",
  },
  {
    value: "terrain",
    label: "Terrain",
    hint: "Height and ocean depth in atlas colours, with hill shading",
    swatch: "linear-gradient(90deg, #1f5f9c 0 25%, #5e8f52 40%, #d6bd86 65%, #8e6b4b 85%, #f4f4f2)",
  },
  {
    value: "political",
    label: "Political",
    hint: "Countries in colour, with India's official borders",
    swatch: "conic-gradient(from 45deg, #e9dcc4 0 25%, #d5e3c8 0 50%, #dad3ea 0 75%, #f1d8cc 0)",
  },
  {
    value: "street",
    label: "Street",
    hint: "Roads, towns, rivers and borders",
    swatch: "linear-gradient(135deg, #14161b 0 40%, #5a4632 40% 46%, #14161b 46% 70%, #0c1d2e 70%)",
  },
  {
    value: "night",
    label: "Night lights",
    hint: "NASA Black Marble: Earth at night from VIIRS",
    swatch: "radial-gradient(circle at 35% 40%, #f9cb35 0 2px, transparent 3px), radial-gradient(circle at 70% 65%, #f98e09 0 2px, transparent 3px), #06080d",
  },
];

const short = { auto: "Auto", globe: "Globe", flat: "Flat" } as const;

/** onChange takes an updater so two quick changes never overwrite each other. */
export default function MapSwitcher({
  view,
  onChange,
}: {
  view: MapView;
  onChange: (update: (v: MapView) => MapView) => void;
}) {
  const current = BASES.find((b) => b.value === view.base) ?? BASES[0];
  const summary = `${current.label} map, ${short[view.projection]}${view.terrain ? ", 3D terrain" : ""}`;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="glass flex h-9 shrink-0 items-center gap-2 rounded-[10px]! pl-1.5 pr-2.5 text-[13px] text-fl-text transition-colors hover:border-fl-faint"
        aria-label={`Map: ${summary}. Change map`}
        title={summary}
      >
        <Swatch css={current.swatch} />
        <span className="hidden sm:inline">{current.label}</span>
        <ChevronDown className="size-3.5 text-fl-muted" aria-hidden />
      </DropdownMenuTrigger>
      <DropdownMenuContent side="left" align="start" className="w-80">
        <DropdownMenuGroup>
          <DropdownMenuLabel>Map</DropdownMenuLabel>
          <DropdownMenuRadioGroup value={view.base} onValueChange={(b) => onChange((v) => ({ ...v, base: b as Base }))}>
            {BASES.map((b) => (
              <DropdownMenuRadioItem key={b.value} value={b.value} closeOnClick className="items-center gap-3 py-1.5">
                <Swatch css={b.swatch} large />
                <Option label={b.label} hint={b.hint} />
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuLabel>View</DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={view.projection}
            onValueChange={(p) => onChange((v) => ({ ...v, projection: p as Projection }))}
          >
            {PROJECTIONS.map((p) => (
              <DropdownMenuRadioItem key={p.value} value={p.value} closeOnClick className="items-start py-1.5">
                <Option label={p.label} hint={p.hint} />
              </DropdownMenuRadioItem>
            ))}
          </DropdownMenuRadioGroup>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuCheckboxItem
          checked={view.terrain}
          onCheckedChange={(c) => onChange((v) => ({ ...v, terrain: !!c }))}
          className="items-start py-1.5"
        >
          <Option label="3D terrain" hint="Real elevation, tilted view. Heavier on slow laptops." />
        </DropdownMenuCheckboxItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Swatch({ css, large = false }: { css: string; large?: boolean }) {
  return (
    <span
      aria-hidden
      className={`shrink-0 rounded-[6px] border border-fl-border ${large ? "h-8 w-11" : "h-6 w-8"}`}
      style={{ background: css }}
    />
  );
}

function Option({ label, hint }: { label: string; hint: string }) {
  return (
    <span className="flex flex-col">
      <span className="text-sm text-fl-text">{label}</span>
      <span className="text-xs text-fl-muted">{hint}</span>
    </span>
  );
}
