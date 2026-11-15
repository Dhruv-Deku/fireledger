// MapLibre v6 loads its web worker from a separate file next to the bundle.
// Turbopack doesn't emit that file, so we serve it from public/ instead and
// point MapLibre at it with setWorkerUrl() (see components/GlobeMap.tsx).
import { copyFileSync, mkdirSync } from "node:fs";

mkdirSync("public/maplibre", { recursive: true });
copyFileSync(
  "node_modules/maplibre-gl/dist/maplibre-gl-worker.mjs",
  "public/maplibre/maplibre-gl-worker.mjs",
);
console.log("Copied MapLibre worker to public/maplibre/");
