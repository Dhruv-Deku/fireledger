// the five fire satellites on circular sun-synchronous orbits.
// real: height, tilt, period (from kepler) and each plane's local time of the
// ascending node, placed against where the sun is right now. not real: where
// each one sits along its orbit (that needs live orbital elements), and the speed.

const MU = 398600.4418; // km³/s², earth
export const EARTH_R = 6371; // km

export type Sat = {
  name: string;
  sensor: "MODIS" | "VIIRS";
  heightKm: number;
  inclinationDeg: number;
  /** local solar time when it crosses the equator going north, hours */
  ascendingLocalTime: number;
  /** starting point along the orbit, degrees from the ascending node */
  phaseDeg: number;
};

export const SATS: Sat[] = [
  // terra crosses at 10:30 going south, so 22:30 going north
  { name: "Terra", sensor: "MODIS", heightKm: 705, inclinationDeg: 98.2, ascendingLocalTime: 22.5, phaseDeg: 40 },
  { name: "Aqua", sensor: "MODIS", heightKm: 705, inclinationDeg: 98.2, ascendingLocalTime: 13.5, phaseDeg: 210 },
  { name: "Suomi NPP", sensor: "VIIRS", heightKm: 824, inclinationDeg: 98.7, ascendingLocalTime: 13.5, phaseDeg: 0 },
  // noaa-20 flies half an orbit from suomi npp, noaa-21 a quarter
  { name: "NOAA-20", sensor: "VIIRS", heightKm: 824, inclinationDeg: 98.7, ascendingLocalTime: 13.5, phaseDeg: 180 },
  { name: "NOAA-21", sensor: "VIIRS", heightKm: 824, inclinationDeg: 98.7, ascendingLocalTime: 13.5, phaseDeg: 90 },
];

/** minutes per orbit */
export const periodMin = (s: Sat) => (2 * Math.PI * Math.sqrt((EARTH_R + s.heightKm) ** 3 / MU)) / 60;

const rad = Math.PI / 180;

/** earth-fixed unit vector of the point on the orbit at argument of latitude u (deg), node at lon (deg) */
function onOrbit(s: Sat, nodeLon: number, uDeg: number): [number, number, number] {
  const u = uDeg * rad;
  const i = s.inclinationDeg * rad;
  const o = nodeLon * rad;
  return [
    Math.cos(o) * Math.cos(u) - Math.sin(o) * Math.sin(u) * Math.cos(i),
    Math.sin(o) * Math.cos(u) + Math.cos(o) * Math.sin(u) * Math.cos(i),
    Math.sin(u) * Math.sin(i),
  ];
}

/**
 * longitude of the ascending node at time t: the plane stays fixed to the sun
 * (that's what sun-synchronous means), so it follows the subsolar point round
 */
function nodeLongitude(s: Sat, tMs: number) {
  const utcHours = (tMs / 3_600_000) % 24;
  const subsolar = (12 - utcHours) * 15; // equation of time ignored, under 4 degrees
  return subsolar + (s.ascendingLocalTime - 12) * 15;
}

/** where the satellite is, and its whole orbit ring, at time t */
export function satAt(s: Sat, tMs: number, ringSteps = 120) {
  const node = nodeLongitude(s, tMs);
  const u = s.phaseDeg + ((tMs / 60000) / periodMin(s)) * 360;
  const ring: [number, number, number][] = [];
  for (let k = 0; k <= ringSteps; k++) ring.push(onOrbit(s, node, (k / ringSteps) * 360));
  return { pos: onOrbit(s, node, u), ring, scale: (EARTH_R + s.heightKm) / EARTH_R };
}

/** camera basis for an orthographic view looking at (lon, lat) */
export function viewBasis(lon: number, lat: number) {
  const l = lon * rad;
  const p = lat * rad;
  return {
    east: [-Math.sin(l), Math.cos(l), 0] as const,
    north: [-Math.sin(p) * Math.cos(l), -Math.sin(p) * Math.sin(l), Math.cos(p)] as const,
    out: [Math.cos(p) * Math.cos(l), Math.cos(p) * Math.sin(l), Math.sin(p)] as const,
  };
}
