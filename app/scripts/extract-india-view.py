"""Cut Natural Earth's India point-of-view country boundaries to the
Jammu & Kashmir / Ladakh box and write them as a small GeoJSON of lines.

Source file (public domain):
  https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries_ind.geojson

Usage (from app/):
  python -I scripts/extract-india-view.py <ne_10m_admin_0_countries_ind.geojson> public/geo/india-view-kashmir.geojson

BOX must match KASHMIR_BOX in components/GlobeMap.tsx.
"""
import json
import sys

BOX = (72.4, 32.2, 80.6, 37.2)  # west, south, east, north


def clip_segment(p, q, box):
    """Liang-Barsky: part of segment p->q inside box, or None."""
    x0, y0 = p
    x1, y1 = q
    w, s, e, n = box
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for pk, qk in ((-dx, x0 - w), (dx, e - x0), (-dy, y0 - s), (dy, n - y0)):
        if pk == 0:
            if qk < 0:
                return None
            continue
        r = qk / pk
        if pk < 0:
            if r > t1:
                return None
            t0 = max(t0, r)
        else:
            if r < t0:
                return None
            t1 = min(t1, r)
    return (x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy)


def rings(geom):
    if geom["type"] == "Polygon":
        yield from geom["coordinates"]
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            yield from poly


def main(src, out):
    with open(src, encoding="utf-8") as f:
        fc = json.load(f)

    lines = []
    for feat in fc["features"]:
        props = feat["properties"]
        code = props.get("ADM0_A3_IN") or props.get("ADM0_A3")
        if code == "IND":
            xs = [x for r in rings(feat["geometry"]) for x, _ in r]
            ys = [y for r in rings(feat["geometry"]) for _, y in r]
            print(f"India (India's view) extent: lon {min(xs):.2f}..{max(xs):.2f}, lat {min(ys):.2f}..{max(ys):.2f}")
        for ring in rings(feat["geometry"]):
            current = []
            for p, q in zip(ring, ring[1:]):
                seg = clip_segment(tuple(p), tuple(q), BOX)
                if seg is None:
                    if len(current) > 1:
                        lines.append(current)
                    current = []
                    continue
                a, b = seg
                a = [round(a[0], 4), round(a[1], 4)]
                b = [round(b[0], 4), round(b[1], 4)]
                if not current:
                    current = [a, b]
                elif current[-1] == a:
                    if b != a:
                        current.append(b)
                else:
                    if len(current) > 1:
                        lines.append(current)
                    current = [a, b]
            if len(current) > 1:
                lines.append(current)

    out_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "source": "Natural Earth 1:10m admin-0 countries, India point of view (public domain)",
                    "box": BOX,
                },
                "geometry": {"type": "MultiLineString", "coordinates": lines},
            }
        ],
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(out_fc, f, separators=(",", ":"))
    pts = sum(len(l) for l in lines)
    print(f"Wrote {len(lines)} lines, {pts} points to {out}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
