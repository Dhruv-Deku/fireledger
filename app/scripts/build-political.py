"""Build the political map's country shapes from Natural Earth's India point of
view (public domain), small enough to ship with the app.

Source file:
  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries_ind.geojson

Usage (from app/):
  python -I scripts/build-political.py <ne_10m_admin_0_countries_ind.geojson> public/geo/countries-india-view.geojson

Keeps per country: name, ISO-like code and MAPCOLOR7 (Natural Earth's colour
index, chosen so neighbours differ). Outlines are simplified (Douglas-Peucker,
TOLERANCE degrees) and rounded; islands smaller than MIN_AREA are dropped
unless they are all a country has.
"""
import json
import sys

TOLERANCE = 0.02  # degrees, about 2 km
MIN_AREA = 0.002  # square degrees, about 25 km2 at the equator
DIGITS = 3


def simplify(points, tol):
    """Douglas-Peucker on a closed ring, iterative (no recursion limit)."""
    if len(points) < 5:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = tol * tol
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        seg2 = dx * dx + dy * dy
        best, best_d = -1, tol2
        for i in range(a + 1, b):
            px, py = points[i]
            if seg2 == 0:
                d = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
                d = (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2
            if d > best_d:
                best, best_d = i, d
        if best >= 0:
            keep[best] = True
            stack.append((a, best))
            stack.append((best, b))
    return [p for p, k in zip(points, keep) if k]


def area(ring):
    s = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        s += x0 * y1 - x1 * y0
    return abs(s) / 2


def clean_ring(ring):
    out = []
    for x, y in simplify(ring, TOLERANCE):
        p = [round(x, DIGITS), round(y, DIGITS)]
        if not out or out[-1] != p:
            out.append(p)
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out if len(out) >= 4 else None


def polygons(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"]]
    if geom["type"] == "MultiPolygon":
        return geom["coordinates"]
    return []


def main(src, out):
    with open(src, encoding="utf-8") as f:
        fc = json.load(f)

    features = []
    points = 0
    for feat in fc["features"]:
        props = feat["properties"]
        polys = polygons(feat["geometry"])
        biggest = max(polys, key=lambda p: area(p[0])) if polys else None
        kept = []
        for poly in polys:
            if poly is not biggest and area(poly[0]) < MIN_AREA:
                continue
            outer = clean_ring(poly[0])
            if not outer:
                continue
            holes = [h for h in (clean_ring(r) for r in poly[1:] if area(r) >= MIN_AREA) if h]
            kept.append([outer, *holes])
        if not kept:
            continue
        points += sum(len(r) for p in kept for r in p)
        code = props.get("ADM0_A3_IN") or props.get("ADM0_A3")
        if code == "IND":
            xs = [x for p in kept for x, _ in p[0]]
            ys = [y for p in kept for _, y in p[0]]
            print(f"India (India's view): lon {min(xs):.2f}..{max(xs):.2f}, lat {min(ys):.2f}..{max(ys):.2f}")
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": props.get("NAME_EN") or props.get("NAME"),
                    "code": code,
                    "color": props.get("MAPCOLOR7") or 1,
                },
                "geometry": {"type": "MultiPolygon", "coordinates": kept},
            }
        )

    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "type": "FeatureCollection",
                "source": "Natural Earth 1:10m admin-0 countries, India point of view (public domain), simplified",
                "features": features,
            },
            f,
            separators=(",", ":"),
            ensure_ascii=False,
        )
    print(f"Wrote {len(features)} countries, {points} points to {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
