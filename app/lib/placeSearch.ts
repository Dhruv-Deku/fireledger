// Place search with OpenStreetMap's Nominatim (free, no key).
// Usage policy: at most 1 request per second and no search-as-you-type, so the
// app searches only when the user presses Enter. https://operations.osmfoundation.org/policies/nominatim/

import { circlePolygon, type Polygon } from "@/lib/geo";

export type PlaceResult = {
  name: string;
  /** What kind of place, e.g. "district", "town". */
  kind: string;
  /** The real boundary when OSM has one; otherwise a circle around the point. */
  geometry: Polygon;
  /** True when `geometry` is a circle, not the place's own boundary. */
  approx: boolean;
};

/** Radius used for places without a boundary (towns mapped as a point). */
export const POINT_RADIUS_KM = 25;

type NominatimHit = {
  display_name: string;
  name?: string;
  type: string;
  addresstype?: string;
  lat: string;
  lon: string;
  geojson?: { type: string; coordinates: unknown };
};

let lastCall = 0;

export async function searchPlaces(query: string, signal?: AbortSignal): Promise<PlaceResult[]> {
  // Keep to 1 request per second.
  const wait = lastCall + 1000 - Date.now();
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  lastCall = Date.now();

  const params = new URLSearchParams({
    q: query,
    format: "jsonv2",
    limit: "5",
    polygon_geojson: "1",
    // Simplify outlines (degrees) so a state isn't tens of thousands of points.
    polygon_threshold: "0.005",
  });
  let res: Response;
  try {
    res = await fetch(`https://nominatim.openstreetmap.org/search?${params}`, { signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new Error("Can't reach OpenStreetMap search. Check the internet connection.");
  }
  if (!res.ok) throw new Error(`OpenStreetMap search answered ${res.status}. Try again in a moment.`);
  const hits = (await res.json()) as NominatimHit[];
  return hits.map(toResult);
}

function toResult(h: NominatimHit): PlaceResult {
  const name = h.display_name;
  const kind = (h.addresstype ?? h.type).replace(/_/g, " ");
  const g = h.geojson;
  if (g?.type === "Polygon") {
    return { name, kind, geometry: { type: "Polygon", coordinates: g.coordinates as number[][][] }, approx: false };
  }
  if (g?.type === "MultiPolygon") {
    // The API takes one Polygon: keep the largest part (e.g. the mainland, not the islands).
    const parts = g.coordinates as number[][][][];
    const biggest = parts.reduce((a, b) => (ringArea(b[0]) > ringArea(a[0]) ? b : a));
    return { name, kind, geometry: { type: "Polygon", coordinates: biggest }, approx: false };
  }
  return { name, kind, geometry: circlePolygon(Number(h.lat), Number(h.lon), POINT_RADIUS_KM), approx: true };
}

/** Rough ring area in square degrees (shoelace), only to compare parts. */
function ringArea(ring: number[][]) {
  let a = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    a += (ring[j][0] + ring[i][0]) * (ring[j][1] - ring[i][1]);
  }
  return Math.abs(a / 2);
}
