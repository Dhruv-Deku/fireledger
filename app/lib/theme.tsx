"use client";

import { createContext, useCallback, useContext, useMemo, useSyncExternalStore, type ReactNode } from "react";

export type Theme = "dark" | "light";

export const THEME_KEY = "fl-theme";

/**
 * Runs in <head> before first paint (see layout.tsx): saved choice, else the
 * system preference. Sets data-theme and the `dark` class on <html>.
 */
export const THEME_INIT_SCRIPT = `(function(){var d=document.documentElement,t=null;try{t=localStorage.getItem("${THEME_KEY}")}catch(e){}if(t!=="light"&&t!=="dark"){try{t=window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}catch(e){t="dark"}}d.setAttribute("data-theme",t);d.classList.toggle("dark",t==="dark")})();`;

const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

function readSaved(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

function apply(theme: Theme) {
  const el = document.documentElement;
  el.setAttribute("data-theme", theme);
  el.classList.toggle("dark", theme === "dark");
  notify();
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  // Follow the system while the user has not chosen.
  const mq = window.matchMedia("(prefers-color-scheme: light)");
  const onSystem = () => {
    if (!readSaved()) apply(mq.matches ? "light" : "dark");
  };
  mq.addEventListener("change", onSystem);
  return () => {
    listeners.delete(cb);
    mq.removeEventListener("change", onSystem);
  };
}

// The DOM attribute is the source of truth (set by the inline script).
const getSnapshot = (): Theme => (document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark");
const getServerSnapshot = (): Theme => "dark";

type Ctx = { theme: Theme; setTheme: (t: Theme) => void; toggle: () => void };
const ThemeContext = createContext<Ctx>({ theme: "dark", setTheme: () => {}, toggle: () => {} });

export function ThemeProvider({ children }: { children: ReactNode }) {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const setTheme = useCallback((t: Theme) => {
    try {
      localStorage.setItem(THEME_KEY, t);
    } catch {
      /* private mode or blocked storage: the choice just lasts this visit */
    }
    apply(t);
  }, []);
  const toggle = useCallback(() => setTheme(getSnapshot() === "dark" ? "light" : "dark"), [setTheme]);

  const value = useMemo(() => ({ theme, setTheme, toggle }), [theme, setTheme, toggle]);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export const useTheme = () => useContext(ThemeContext);
