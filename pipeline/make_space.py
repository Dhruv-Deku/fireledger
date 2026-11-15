"""Assemble (and optionally upload) the grid-only public copy of the API for Hugging Face Spaces.

    python pipeline/make_space.py                         # -> data/hf_space/ (test: uvicorn from there)
    python pipeline/make_space.py --upload <user>/<space> # needs `huggingface_hub` login with a write token

The full API reads 548M hotspot rows (11 GB); this copy has no hotspot files, so the API answers
every area from the 0.25 deg grid (HAVE_POINTS is false) and the CSV endpoint says 503. The 9 presets
come from the full API's saved answers (data/snapshot), so they match the laptop exactly.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hf_space"
DER = ROOT / "data" / "derived"
DATA = ["grid.parquet", "grid_recent.parquet", "grid_layer.parquet", "mtd_grid.parquet", "daily_grid.parquet", "avail.parquet",
        "calib.parquet", "regions.parquet", "persistent.parquet", "typed_static.parquet", "proof.json",
        "bands.json", "burned_area.json", "creams_check.json", "nrt_from.json"]


def build() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(ROOT / "deploy" / "hf_space", OUT)
    shutil.copytree(ROOT / "api", OUT / "api", ignore=shutil.ignore_patterns("__pycache__"))
    (OUT / "data" / "derived").mkdir(parents=True)
    for f in DATA:
        if (DER / f).exists():
            shutil.copy2(DER / f, OUT / "data" / "derived" / f)
    snap = ROOT / "data" / "snapshot"  # presets' exact answers from the full API (pipeline/snapshot.py)
    if (snap / "manifest.json").exists():
        for part in ["calendar", "summary", "daily"]:
            shutil.copytree(snap / part, OUT / "data" / "snapshot" / part)
        shutil.copy2(snap / "manifest.json", OUT / "data" / "snapshot" / "manifest.json")
    size = sum(p.stat().st_size for p in OUT.rglob("*") if p.is_file())
    print(f"built {OUT} ({size / 1e6:.0f} MB)")


def upload(repo_id: str) -> None:
    from huggingface_hub import HfApi
    api = HfApi()
    api.create_repo(repo_id, repo_type="space", space_sdk="docker", exist_ok=True)
    api.upload_folder(folder_path=str(OUT), repo_id=repo_id, repo_type="space",
                      commit_message="FireLedger API, grid-only copy")
    print(f"uploaded: https://huggingface.co/spaces/{repo_id}  (API: https://{repo_id.replace('/', '-').lower()}.hf.space)")


if __name__ == "__main__":
    build()
    if "--upload" in sys.argv:
        upload(sys.argv[sys.argv.index("--upload") + 1])
