"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/lib/theme";

export default function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const light = theme === "light";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={light}
      aria-label="Light mode"
      title={light ? "Switch to dark mode" : "Switch to light mode"}
      className="grid size-9 place-items-center rounded-full border border-fl-border text-fl-muted transition-colors hover:bg-fl-panel hover:text-fl-text"
    >
      {light ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
    </button>
  );
}
