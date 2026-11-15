import { Suspense } from "react";
import type { Metadata } from "next";
import ClientWorkspace from "@/components/ClientWorkspace";
import SiteHeader from "@/components/SiteHeader";

export const metadata: Metadata = { title: "Map | FireLedger" };

export default function MapApp() {
  return (
    <>
      <SiteHeader page="app" />

      {/* reads today's date, so it renders per visit, not at build time */}
      <Suspense fallback={<main className="grid flex-1 place-items-center text-sm text-fl-muted">Loading the map</main>}>
        <ClientWorkspace />
      </Suspense>

      {/* data credit, plain text only: no NASA logos, meatball, worm or seal */}
      <footer className="flex h-8 shrink-0 items-center justify-center border-t border-fl-border bg-fl-bg px-4 text-center text-xs text-fl-muted">
        Data: NASA FIRMS (MODIS C6.1, VIIRS 375 m) · Built for NASA Space Apps Challenge 2026
      </footer>
    </>
  );
}
