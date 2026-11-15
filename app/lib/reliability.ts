// How well the harmonization held up across the 2012 switch, per world region,
// from /api/proof (the accuracy page). A user's own area gets the API's
// summary.reliability instead.
// The test: across the 2012 switch to VIIRS, the harmonized record should change
// as much as MODIS alone did (MODIS kept flying, so its change is the real one).
// What's left over is the part of the jump the harmonization didn't remove.

import type { ProofResponse } from "@/lib/api";

export type Verdict = "good" | "check" | "weak";

/** Leftover jump (harmonized change − MODIS-alone change), vegetation fires only. */
export const leftover = (j: ProofResponse["jumps"][number]) =>
  j.step2012_harmonized_vegetation - j.step2012_modis_vegetation;

/** Within 5%: good. 5–10%: check. Over 10%: weak. */
export function verdict(gap: number): Verdict {
  const a = Math.abs(gap);
  return a <= 0.05 ? "good" : a <= 0.1 ? "check" : "weak";
}

export const VERDICT_TEXT: Record<Verdict, { icon: string; word: string }> = {
  good: { icon: "✓", word: "Within 5%" },
  check: { icon: "~", word: "5–10% off" },
  weak: { icon: "✗", word: "Over 10% off" },
};

/** "+9%" / "−37%" */
export const pctSigned = (f: number, digits = 0) =>
  `${f > 0 ? "+" : f < 0 ? "−" : ""}${Math.abs(f * 100).toFixed(digits)}%`;
