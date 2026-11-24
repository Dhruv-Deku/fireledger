"""Run the FireLedger API on your own computer, with the data from the GitHub release.

    pip install -r api/requirements.txt
    python run_local.py                  # first run downloads 420 MB of data, then serves http://localhost:8000
    python run_local.py --with-points    # also the presets' single hotspots (623 MB more), for the hotspot CSV
                                         # there; that file isn't in the release yet
    python run_local.py --port 8765      # if something else already uses port 8000

Then open https://fireledger.vercel.app/app?api=http://localhost:8000 : the site remembers it
(?api=default switches back). Interactive API docs: http://localhost:8000/docs

This is the same grid-only copy as the public server: every area comes from the 0.25 deg grid and the
9 presets are exact. With the full hotspot files (pipeline/ingest.py) it serves everything instead.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELEASE = "https://github.com/Dhruv-Deku/fireledger/releases/download/data-2026-11-13/"
# name, sha256, a file that is there once it's unpacked
DATA = ("fireledger-data-2026-11-13.zip",
        "deabea0767f2f98a934dad9daa7e3e9e8280f4e4e61a89c8b53d9b8ec539537f",
        ROOT / "data" / "derived" / "grid.parquet")
POINTS = ("fireledger-preset-points-2026-11-13.zip",
          "592e492f3c85382ab543f89cc3ede06aad7532f0c4f3eea0de33ad1c8ee94f52",
          ROOT / "data" / "preset_points" / "punjab.parquet")
NEEDS = ["fastapi", "uvicorn", "duckdb", "pyarrow", "pandas", "numpy", "scipy", "shapely"]


def fetch(name: str, sha256: str, marker: Path) -> None:
    if marker.exists():
        return
    url = RELEASE + name
    part = ROOT / "data" / (name + ".part")
    part.parent.mkdir(exist_ok=True)
    print(f"Downloading {url}")
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(url) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while chunk := r.read(1 << 20):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {done / 1e6:.0f} of {total / 1e6:.0f} MB", end="", flush=True)
        print()
    except (urllib.error.URLError, OSError) as e:
        part.unlink(missing_ok=True)
        sys.exit(f"Download failed ({e}). Get {name} from {RELEASE.replace('/download/', '/tag/')} "
                 f"and unzip it in {ROOT} (it holds a data/ folder), then run this again.")
    if h.hexdigest() != sha256:
        part.unlink()
        sys.exit(f"{name} came down damaged (checksum differs). Run this again.")
    print(f"Unpacking {name}")
    with zipfile.ZipFile(part) as z:
        z.extractall(ROOT)
    part.unlink()


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the FireLedger API on this computer.")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to let other computers on your network use it")
    ap.add_argument("--with-points", action="store_true", help="also download the presets' single hotspots (623 MB)")
    args = ap.parse_args()

    missing = []
    for mod in NEEDS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        sys.exit(f"Missing Python packages: {', '.join(missing)}. Install them with:\n"
                 f"  pip install -r {Path('api') / 'requirements.txt'}")

    fetch(*DATA)
    if args.with_points:
        fetch(*POINTS)

    local = f"http://localhost:{args.port}"
    print(f"\nFireLedger API: {local}/api/health  (docs: {local}/docs)")
    print(f"Use it in the app: https://fireledger.vercel.app/app?api={local}")
    print("Keep this window open while you use it; Ctrl+C stops it.\n")

    import uvicorn
    sys.path.insert(0, str(ROOT))
    uvicorn.run("api.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
