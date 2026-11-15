// Area-of-interest helpers. The AOI is always a GeoJSON Polygon, as in the
// API contract (docs/API_FOR_APP.md): { type: "Polygon", coordinates: [[[lon, lat], ...]] }.

export type Polygon = {
  type: "Polygon";
  coordinates: number[][][];
};

export type AreaOfInterest = {
  geometry: Polygon;
  /** Short human label shown in the panel, e.g. "Punjab, India" or "Drawn polygon". */
  label: string;
};

const KM_PER_DEG_LAT = 111.32;

/**
 * Circle around (lat, lon) with the given radius in km, as a closed polygon.
 * A degree of longitude shrinks with cos(latitude), so the east-west offset is
 * divided by cos(lat); without this, circles turn into ovals away from the equator.
 */
export function circlePolygon(
  lat: number,
  lon: number,
  radiusKm: number,
  steps = 64,
): Polygon {
  const dLat = radiusKm / KM_PER_DEG_LAT;
  // Clamp so the poles don't divide by ~0.
  const cosLat = Math.max(Math.cos((lat * Math.PI) / 180), 0.01);
  const dLon = radiusKm / (KM_PER_DEG_LAT * cosLat);

  const ring: number[][] = [];
  for (let i = 0; i < steps; i++) {
    const t = (i / steps) * 2 * Math.PI;
    ring.push([round6(lon + dLon * Math.cos(t)), round6(lat + dLat * Math.sin(t))]);
  }
  ring.push(ring[0]); // close the ring
  return { type: "Polygon", coordinates: [ring] };
}

/** [[west, south], [east, north]] of a polygon's outer ring. */
export function polygonBounds(p: Polygon): [[number, number], [number, number]] {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const [x, y] of p.coordinates[0]) {
    if (x < w) w = x;
    if (x > e) e = x;
    if (y < s) s = y;
    if (y > n) n = y;
  }
  return [[w, s], [e, n]];
}

/** area of a polygon's outer ring on a sphere, km² (good enough to tell small areas from big ones) */
export function areaKm2(p: Polygon): number {
  const R = 6371;
  const rad = Math.PI / 180;
  const ring = p.coordinates[0];
  let sum = 0;
  for (let i = 0; i < ring.length - 1; i++) {
    const [x1, y1] = ring[i];
    const [x2, y2] = ring[i + 1];
    sum += (x2 - x1) * rad * (2 + Math.sin(y1 * rad) + Math.sin(y2 * rad));
  }
  return Math.abs((sum * R * R) / 2);
}

function round6(v: number) {
  return Math.round(v * 1e6) / 1e6;
}
