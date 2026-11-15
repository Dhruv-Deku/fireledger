// sen's slope and a two-sided mann-kendall test, the same pair the api uses for
// `trend`. the app needs them for the 2012-on trend until the api sends trend_viirs.

/** median of the slopes between every pair of years */
export function senSlope(points: { x: number; y: number }[]): number {
  const slopes: number[] = [];
  for (let i = 0; i < points.length; i++) {
    for (let j = i + 1; j < points.length; j++) {
      const dx = points[j].x - points[i].x;
      if (dx !== 0) slopes.push((points[j].y - points[i].y) / dx);
    }
  }
  slopes.sort((a, b) => a - b);
  const h = slopes.length >> 1;
  return slopes.length % 2 ? slopes[h] : (slopes[h - 1] + slopes[h]) / 2;
}

/** two-sided p-value, normal approximation with tie correction and continuity correction */
export function mannKendallP(values: number[]): number {
  const n = values.length;
  let s = 0;
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) s += Math.sign(values[j] - values[i]);
  const ties = new Map<number, number>();
  for (const v of values) ties.set(v, (ties.get(v) ?? 0) + 1);
  let tieTerm = 0;
  for (const t of ties.values()) if (t > 1) tieTerm += t * (t - 1) * (2 * t + 5);
  const variance = (n * (n - 1) * (2 * n + 5) - tieTerm) / 18;
  if (variance <= 0) return 1;
  const z = s > 0 ? (s - 1) / Math.sqrt(variance) : s < 0 ? (s + 1) / Math.sqrt(variance) : 0;
  return 2 * (1 - normalCdf(Math.abs(z)));
}

// abramowitz and stegun 7.1.26, good to about 1e-7
function normalCdf(z: number) {
  const t = 1 / (1 + 0.3275911 * (z / Math.SQRT2));
  const erf =
    1 - t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))) *
      Math.exp(-(z * z) / 2);
  return 0.5 * (1 + erf);
}
