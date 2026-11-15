import Link from "next/link";
import AccuracyReport from "@/components/AccuracyReport";
import SiteHeader from "@/components/SiteHeader";

export const metadata = { title: "Accuracy and trust | FireLedger" };

export default function AccuracyPage() {
  return (
    <>
      <SiteHeader page="accuracy" />

      <main className="scrollbar-dark flex-1 overflow-y-auto bg-fl-bg">
        <div className="mx-auto flex max-w-5xl flex-col gap-10 px-5 pb-16 pt-12 sm:px-8">
          <div className="flex max-w-[44rem] flex-col gap-4">
            <h1 className="wide text-[clamp(2rem,4.6vw,3.4rem)] font-semibold leading-[1.02] tracking-[-0.025em] text-fl-text">
              How far can you trust these numbers?
            </h1>
            <p className="text-[17px] leading-relaxed text-fl-muted">
              Everything below is measured, not claimed: the conversion is tested on years where both satellites flew,
              against burned-area maps, and against India&apos;s official crop-fire counts. Where it falls short, it
              says so.
            </p>
            <Link href="/app" className="w-fit text-sm font-medium text-fl-accent-text hover:underline">
              Open the map
            </Link>
          </div>
          <AccuracyReport />
        </div>
        <footer className="border-t border-fl-border px-4 py-4 text-center text-xs text-fl-muted">
          Data: NASA FIRMS (MODIS C6.1, VIIRS 375 m) · Built for NASA Space Apps Challenge 2026
        </footer>
      </main>
    </>
  );
}
