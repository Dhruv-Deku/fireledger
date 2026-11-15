"""FireLedger API on Modal: the public, grid-only copy (same code as the laptop, no hotspot files).

    python pipeline/make_space.py                    # assemble data/hf_space/ (grid, factors, snapshot)
    python deploy/modal_app.py upload                # copy its data/ to the Modal volume (~400 MB, once per data update)
    python -m modal deploy deploy/modal_app.py       # -> https://<workspace>--fireledger-web.modal.run

Every area is answered from the 0.25 deg grid; the 9 presets come from the full API's saved answers
(data/snapshot), so they match the laptop exactly. CSV downloads need the hotspot files: 503 here.
The container sleeps after 20 idle minutes; the first request after that takes ~10-20 s.
"""
import sys
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
VOLUME = "fireledger-data"

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_requirements(str(ROOT / "deploy" / "hf_space" / "requirements.txt"))
    .env({"FIRELEDGER_DUCKDB_MEMORY": "1GB"})
    .add_local_dir(str(ROOT / "api"), "/root/app/api", ignore=["__pycache__"])
)
vol = modal.Volume.from_name(VOLUME, create_if_missing=True)
app = modal.App("fireledger", image=image)


@app.function(volumes={"/vol": vol}, cpu=1.0, memory=2048, scaledown_window=1200, timeout=300)
@modal.concurrent(max_inputs=16)
@modal.asgi_app()
def web():
    import shutil
    # the volume is network storage; the API reads parquet many times, so work from a local copy
    if not Path("/root/app/data").exists():
        shutil.copytree("/vol", "/root/app/data")
    sys.path.insert(0, "/root/app")
    from api.main import app as fastapi_app
    return fastapi_app


def upload() -> None:
    src = ROOT / "data" / "hf_space" / "data"
    with vol.batch_upload(force=True) as batch:
        for part in ["derived", "snapshot"]:
            if (src / part).exists():
                batch.put_directory(str(src / part), f"/{part}")
    print(f"uploaded {src} to volume {VOLUME}")


if __name__ == "__main__" and sys.argv[1:2] == ["upload"]:
    upload()
