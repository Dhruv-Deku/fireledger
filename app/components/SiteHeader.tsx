import Link from "next/link";
import ThemeToggle from "./ThemeToggle";

type Page = "home" | "app" | "accuracy";

const NAV: { href: string; label: string; page: Page }[] = [
  { href: "/app", label: "Map", page: "app" },
  { href: "/accuracy", label: "Accuracy and trust", page: "accuracy" },
];

/**
 * one header for every page. on the home page it floats over the globe
 * (overlay), elsewhere it's a solid bar above the content.
 */
export default function SiteHeader({ page, overlay = false }: { page: Page; overlay?: boolean }) {
  return (
    <header
      className={`z-30 flex h-14 shrink-0 items-center justify-between gap-4 px-4 sm:px-6 ${
        overlay ? "absolute inset-x-0 top-0" : "border-b border-fl-border bg-fl-bg"
      }`}
    >
      <Link href="/" className="wide text-[19px] font-semibold tracking-[-0.01em] text-fl-text" aria-label="FireLedger home">
        FireLedger
      </Link>
      <nav className="flex items-center gap-1 sm:gap-2" aria-label="Pages">
        {NAV.map((n) => (
          <Link
            key={n.href}
            href={n.href}
            aria-current={page === n.page ? "page" : undefined}
            className={`rounded-full px-3 py-1.5 text-sm transition-colors ${
              page === n.page ? "bg-fl-subtle text-fl-text" : "text-fl-muted hover:text-fl-text"
            } ${n.page === "app" ? "hidden sm:inline-block" : ""}`}
          >
            {n.label}
          </Link>
        ))}
        <ThemeToggle />
      </nav>
    </header>
  );
}
