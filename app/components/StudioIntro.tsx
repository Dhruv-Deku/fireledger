"use client";

import { useEffect, useState } from "react";
import { Montserrat } from "next/font/google";
import { INTRO_KEY } from "@/lib/intro";

// the studio's own type, used only in this intro; the site stays on Archivo
const montserrat = Montserrat({ subsets: ["latin"], weight: "700" });

const TOTAL_MS = 4900;

/*
 * the mark >=/⅃ traced from the hand-drawn original, same proportions:
 * ">" is the tallest, "=" sits low beside it, "/" is a little shorter,
 * "⅃" is about two thirds of its height and shares its baseline.
 */
const STROKES = [
  { d: "M25 15 L108 62 L20 107", len: 190, delay: 2.65 },
  { d: "M143 68 H203", len: 60, delay: 2.95 },
  { d: "M146 90 H200", len: 54, delay: 3.05 },
  { d: "M262 112 L304 30", len: 92, delay: 3.2 },
  { d: "M388 46 V107 H356", len: 93, delay: 3.4 },
];

const glow = (b: number, r: number, a: number) =>
  `brightness(${b}) drop-shadow(0 0 ${r}px rgba(255,255,255,${a}))`;

/*
 * word: a diagonal wipe in from top left to bottom right (0.1-1.0s), then the
 * glow flares to ~180%, dips to 75%, settles at 125% (1.0-2.4s), then it
 * collapses (2.4-2.9s) while the mark draws in and gives one last pulse.
 */
const css = `
@keyframes wd-wipe { from { -webkit-mask-position: 100% 100%; mask-position: 100% 100%; } to { -webkit-mask-position: 0 0; mask-position: 0 0; } }
@keyframes wd-glow {
  0%   { filter: ${glow(1, 4, 0.35)}; }
  35%  { filter: ${glow(1.8, 22, 0.95)}; }
  65%  { filter: ${glow(0.75, 3, 0.2)}; }
  100% { filter: ${glow(1.25, 12, 0.6)}; }
}
@keyframes wd-out {
  from { opacity: 1; transform: scaleX(1); filter: ${glow(1.25, 12, 0.6)}; }
  to   { opacity: 0; transform: scaleX(0.2); filter: ${glow(2, 30, 0)} blur(8px); }
}
@keyframes wd-draw { to { stroke-dashoffset: 0; } }
@keyframes wd-mark {
  0%, 100% { filter: ${glow(1, 5, 0.55)}; }
  50% { filter: ${glow(1.5, 16, 0.9)}; }
}
@keyframes wd-fade { to { opacity: 0; visibility: hidden; } }
.wd-overlay { animation: wd-fade 0.55s ease-in 4.35s forwards; }
/* glow on the outer span, wipe mask on the inner one, so the mask never clips the glow */
.wd-word {
  animation:
    wd-glow 1.4s ease-in-out 1s forwards,
    wd-out 0.5s ease-in 2.4s forwards;
}
.wd-wipe {
  display: block;
  padding: 0.6em 0.9em;
  -webkit-mask-image: linear-gradient(135deg, #000 42%, transparent 58%);
  mask-image: linear-gradient(135deg, #000 42%, transparent 58%);
  -webkit-mask-size: 300% 300%; mask-size: 300% 300%;
  -webkit-mask-position: 100% 100%; mask-position: 100% 100%;
  text-shadow: 0 0 18px rgba(255,255,255,0.45);
  animation: wd-wipe 0.9s cubic-bezier(.3,.6,.3,1) 0.1s forwards;
}
.wd-mark { filter: ${glow(1, 5, 0.55)}; animation: wd-mark 0.9s ease-in-out 3.6s; }
.wd-stroke { animation: wd-draw 0.55s cubic-bezier(.6,0,.2,1) forwards; }
html[data-intro-seen] .wd-overlay { display: none; }
@media (prefers-reduced-motion: reduce) { .wd-overlay { display: none; } }
`;

/**
 * studio intro on first load of a visit: "Wolfram Devs" sweeps in, flares,
 * then closes up into the >=/⅃ mark, which draws itself in, then the site
 * fades in. click or any key skips it.
 */
export default function StudioIntro() {
  const [done, setDone] = useState(false);

  useEffect(() => {
    let seen = false;
    try {
      seen = sessionStorage.getItem(INTRO_KEY) === "1";
      sessionStorage.setItem(INTRO_KEY, "1");
    } catch {
      /* storage blocked: it just plays every time */
    }
    if (seen) return;
    const skip = () => setDone(true);
    const t = setTimeout(skip, TOTAL_MS);
    window.addEventListener("keydown", skip, { once: true });
    return () => {
      clearTimeout(t);
      window.removeEventListener("keydown", skip);
    };
  }, []);

  if (done) return null;
  return (
    <div
      className="wd-overlay fixed inset-0 z-[100] grid place-items-center bg-[#070b14]"
      onClick={() => setDone(true)}
      role="presentation"
      aria-hidden
    >
      <style>{css}</style>
      <div className="relative grid place-items-center">
        <span
          className={`wd-word ${montserrat.className} absolute whitespace-nowrap uppercase tracking-[0.08em] text-[clamp(1.6rem,4.6vw,3.2rem)] text-white`}
        >
          <span className="wd-wipe">Wolfram Devs</span>
        </span>
        <svg viewBox="0 0 410 125" className="wd-mark w-[min(70vw,420px)] overflow-visible" fill="none">
          {STROKES.map((s) => (
            <path
              key={s.d}
              d={s.d}
              className="wd-stroke"
              stroke="#fff"
              strokeWidth={8}
              strokeLinecap="round"
              strokeLinejoin="round"
              // offset past the round cap, or each stroke shows a dot before it draws
              style={{
                strokeDasharray: `${s.len} ${s.len + 20}`,
                strokeDashoffset: s.len + 10,
                animationDelay: `${s.delay}s`,
              }}
            />
          ))}
        </svg>
      </div>
    </div>
  );
}
