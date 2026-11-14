"""Download the context and validation datasets that need no account, into data/external/.

    python pipeline/fetch_extras.py                  # everything, small and useful first
    python pipeline/fetch_extras.py --only creams fwi
    python pipeline/fetch_extras.py --list

Every file goes to <name>.part first and is renamed when complete, so a rerun skips what is done
and resumes the rest. Each dataset folder gets a SOURCE.txt with its link and licence. The script
stops if free disk space drops below MIN_FREE_GB.

Fire weather is the slow one: the NASA GFWED server sends ~0.2 MB/s per connection and only has
daily files (4 MB each), so it is reduced on the fly to one monthly file (mean of each index, max of
FWI) and the dailies are deleted. Newest months first, so the VIIRS years land first.

Needs an account, so not here: MODIS land cover MCD12C1 and cloud fraction MOD08_M3 (Earthdata),
ERA5 / Copernicus fire danger (CDS), Sentinel-5P CO (Copernicus), VIIRS Nightfire (EOG).
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "data" / "external"
MIN_FREE_GB = 15
UA = {"User-Agent": "FireLedger/1.0 (NASA Space Apps 2026)"}

CREAMS = "https://creams.iari.res.in"
ZENODO = "https://zenodo.org/api/records/"
PSL = "https://downloads.psl.noaa.gov/Datasets/"
GFWED = "https://portal.nccs.nasa.gov/datashare/GlobalFWI/v2.0/fwiCalcs.MERRA2/Default/MERRA2/"
GOES = "https://noaa-goes16.s3.amazonaws.com/"
FWI_VARS = ["FWI", "BUI", "DC", "DMC", "FFMC", "ISI", "DSR"]
FWI_YEARS = range(2001, date.today().year + 1)


class DiskFull(Exception):
    pass


def check_disk() -> None:
    free = shutil.disk_usage(ROOT).free / 2**30
    if free < MIN_FREE_GB:
        raise DiskFull(f"only {free:.1f} GB free (limit {MIN_FREE_GB} GB)")


def get(url: str, tries: int = 6, timeout: int = 120) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or i == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if i == tries - 1:
                raise
        time.sleep(min(60, 5 * 2**i))
    raise RuntimeError("unreachable")


def download(url: str, path: Path, size: int | None = None, tries: int = 6) -> bool:
    """Fetch url to path unless it is already there (and the right size). True if fetched."""
    if path.exists() and path.stat().st_size > 0 and (size is None or path.stat().st_size == size):
        return False
    check_disk()
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r, \
                    open(part, "wb") as f:
                want = size or int(r.headers.get("Content-Length") or 0)
                shutil.copyfileobj(r, f, 1 << 20)
            got = part.stat().st_size
            if got == 0 or (want and got != want):  # servers sometimes end a 200 early
                raise OSError(f"short file {got} of {want or '?'}")
            part.replace(path)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404 or i == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if i == tries - 1:
                raise
        time.sleep(min(60, 5 * 2**i))
    return False


def many(jobs: list[tuple[str, Path, int | None]], workers: int, label: str) -> None:
    done = fetched = 0
    t0 = time.time()

    def one(job):
        return download(*job)

    with ThreadPoolExecutor(workers) as ex:
        for got in ex.map(one, jobs):
            done += 1
            fetched += got
            if done % 25 == 0 or done == len(jobs):
                print(f"  {label}: {done}/{len(jobs)} ({fetched} new) [{time.time() - t0:.0f}s]", flush=True)


def source_txt(folder: Path, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SOURCE.txt").write_text(f"Downloaded {date.today()} by pipeline/fetch_extras.py\n\n{text.strip()}\n",
                                       encoding="utf-8")


# --- datasets ---------------------------------------------------------------------------------

def creams() -> None:
    """ICAR-IARI CREAMS daily crop-residue fire bulletins (Punjab, Haryana, UP, Delhi, MP, Rajasthan)."""
    out = EXT / "creams"
    page = json.loads(get(f"{CREAMS}/index.php?rest_route=/wp/v2/pages/1123&_fields=content"))
    links = sorted(set(re.findall(r'href="(/Creams_website_bulletin/[^"]+\.pdf)"', page["content"]["rendered"])))
    jobs = [(CREAMS + l.replace(" ", "%20"), out / Path(l).relative_to("/Creams_website_bulletin"), None)
            for l in links]
    source_txt(out, f"""ICAR-IARI CREAMS crop residue burning bulletins (daily PDFs, wheat 'w' and rice 'r'
seasons), {CREAMS}/?page_id=1123. Official Indian satellite fire counts per state, for comparison
with FireLedger's Punjab and Haryana numbers. Public bulletins; cite ICAR-IARI CREAMS.""")
    many(jobs, 4, "creams")


def zenodo(record: str, folder: str, note: str) -> None:
    out = EXT / folder
    meta = json.loads(get(ZENODO + record))
    jobs = [(f["links"]["self"], out / f["key"], f["size"]) for f in meta["files"]]
    source_txt(out, f"{meta['metadata']['title']}\nhttps://doi.org/{meta['doi']}\n"
                    f"Licence: {meta['metadata'].get('license', {}).get('id', 'see record')}\n\n{note}")
    many(jobs, 3, folder)


def gfed5() -> None:
    zenodo("7668424", "gfed5", "GFED5 burned area (0.25 deg, monthly, to 2022): a second independent "
                               "burned-area check, includes small fires MCD64 misses.")


def crop_calendar() -> None:
    zenodo("5062513", "crop_calendar", "GGCMI Phase 3 crop calendars (planting and maturity day per "
                                       "crop, 0.5 deg): explains when residue burning happens.")


def weather() -> None:
    out = EXT / "weather"
    files = {
        "gpcp_precip.mon.mean.nc": PSL + "gpcp/precip.mon.mean.nc",
        "precl_precip.mon.mean.0.5x0.5.nc": PSL + "precl/0.5deg/precip.mon.mean.0.5x0.5.nc",
        "ghcncams_air.mon.mean.nc": PSL + "ghcncams/air.mon.mean.nc",
        "cpcsoil_soilw.mon.mean.v2.nc": PSL + "cpcsoil/soilw.mon.mean.v2.nc",
    }
    source_txt(out, "NOAA PSL monthly gridded data, " + PSL + "\n" + "\n".join(f"{k}: {v}" for k, v in files.items())
               + "\n\nPrecipitation (GPCP 2.5 deg, PREC/L 0.5 deg), air temperature (GHCN_CAMS 0.5 deg) and "
                 "soil moisture (CPC 0.5 deg), for 'driest September in 10 years' notes. Public domain (NOAA).")
    many([(u, out / k, None) for k, u in files.items()], 2, "weather")


def population() -> None:
    out = EXT / "population"
    url = ("https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/2020/0_Mosaicked/"
           "global_2020_1km_UNadj_uncounstrained.tif")
    source_txt(out, f"WorldPop 2020 population, 1 km, UN-adjusted, unconstrained.\n{url}\nLicence: CC BY 4.0 "
                    "(WorldPop, www.worldpop.org). For 'people living near this season's fires'.")
    many([(url, out / "worldpop_2020_1km_UNadj.tif", None)], 1, "population")


def goes() -> None:
    """GOES-16 full-disk fire product, one scan per hour, Aug-Sep 2024 (Amazon + Cerrado fire peak)."""
    out = EXT / "goes16_fdc"
    jobs = []
    for doy in range(date(2024, 8, 1).timetuple().tm_yday, date(2024, 9, 30).timetuple().tm_yday + 1):
        xml = get(f"{GOES}?list-type=2&prefix=ABI-L2-FDCF/2024/{doy:03d}/").decode()
        keys = [(re.search(r"<Key>([^<]+)</Key>", c)[1], re.search(r"<Size>(\d+)</Size>", c)[1])
                for c in re.findall(r"<Contents>(.*?)</Contents>", xml)]
        first: dict[str, tuple[str, int]] = {}
        for key, size in sorted(keys):
            hour = key.split("/")[3]
            first.setdefault(hour, (key, int(size)))
        jobs += [(GOES + k, out / k.split("/", 1)[1], s) for k, s in first.values()]
    source_txt(out, f"NOAA GOES-16 ABI L2 Fire/Hot Spot Characterization, full disk (ABI-L2-FDCF), first scan of\n"
                    f"each hour, 2024-08-01..2024-09-30, from the NOAA Open Data bucket {GOES}\n\n"
                    "Geostationary fire detections every 10 min over the Americas: shows the daily fire cycle that\n"
                    "MODIS and VIIRS only sample at their overpass times. Public domain (NOAA).")
    many(jobs, 8, "goes16")


def wdpa() -> None:
    out = EXT / "wdpa"
    today = date.today()
    tag = f"{calendar.month_abbr[today.month]}{today.year}"
    url = f"https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_{tag}_Public_shp.zip"
    source_txt(out, f"World Database on Protected Areas, {tag}.\n{url}\n\nLicence: UNEP-WCMC terms, non-commercial "
                    "use, no redistribution of the data itself (keep it out of the repo and the public site). "
                    "Cite: UNEP-WCMC and IUCN, Protected Planet.")
    many([(url, out / f"WDPA_{tag}_Public_shp.zip", None)], 1, "wdpa")


def fwi() -> None:
    """NASA GFWED fire weather (MERRA-2), daily -> monthly mean of each index and max FWI."""
    out = EXT / "fwi"
    tmp = out / "daily"
    source_txt(out, f"NASA Global Fire WEather Database (GFWED) v2.0, MERRA-2 forcing, 0.5 x 0.625 deg.\n{GFWED}\n\n"
                    "Reduced here to monthly files FWI_YYYY_MM.npz: mean of " + ", ".join(FWI_VARS) +
               ", max of FWI, number of days. Fire danger next to fire activity, for early warning.\n"
               "Field et al. (2015), Geosci. Model Dev. 8, 1185. NASA data, no restrictions.")
    months: dict[tuple[int, int], list[str]] = {}
    for y in FWI_YEARS:
        try:
            html = get(f"{GFWED}{y}/").decode()
        except urllib.error.HTTPError:
            continue
        for name in sorted(set(re.findall(r'href="(FWI\.MERRA2\.Daily\.Default\.(\d{8})\.nc)"', html))):
            d = name[1]
            months.setdefault((int(d[:4]), int(d[4:6])), []).append(name[0])
    todo = []
    for (y, m), names in sorted(months.items(), reverse=True):
        f = out / f"FWI_{y}_{m:02d}.npz"
        if f.exists():
            with np.load(f) as z:
                if int(z["n_days"]) >= len(names):
                    continue
        todo.append((y, m, names))
    print(f"  fwi: {len(todo)} of {len(months)} months to do", flush=True)
    from scipy.io import netcdf_file
    warnings.filterwarnings("ignore", r"(Mean of empty|All-NaN) slice", RuntimeWarning)  # ocean cells
    t0 = time.time()
    def day(name: str, y: int):
        """One day's indices; fetched again once if unreadable, None (day skipped) if still bad."""
        p, err = tmp / name, None
        for _ in range(2):
            try:
                download(f"{GFWED}{y}/{name}", p)
                with netcdf_file(p, "r", mmap=False) as nc:
                    grids = {v: np.asarray(nc.variables[f"MERRA2_{v}"][0], dtype=np.float32) for v in FWI_VARS}
                    return grids, np.array(nc.variables["lat"][:]), np.array(nc.variables["lon"][:])
            except DiskFull:
                raise
            except Exception as e:
                err = e
            finally:
                p.unlink(missing_ok=True)
        print(f"  fwi: skipped {name}: {type(err).__name__}: {err}", flush=True)
        return None

    for i, (y, m, names) in enumerate(todo, 1):
        with ThreadPoolExecutor(6) as ex:
            got = [d for d in ex.map(lambda n: day(n, y), names) if d]
        if not got:
            print(f"  fwi: {y}-{m:02d} no readable days, skipped", flush=True)
            continue
        _, lat, lon = got[0]
        res = {f"{v}_mean": np.nanmean(np.stack([g[v] for g, _, _ in got]), axis=0).astype(np.float32)
               for v in FWI_VARS}
        res["FWI_max"] = np.nanmax(np.stack([g["FWI"] for g, _, _ in got]), axis=0).astype(np.float32)
        # n_days < days on the server means a skipped day; the next run redoes that month
        np.savez_compressed(out / f"FWI_{y}_{m:02d}.npz", lat=lat, lon=lon, n_days=len(got), **res)
        print(f"  fwi: {y}-{m:02d} done ({i}/{len(todo)}) [{time.time() - t0:.0f}s]", flush=True)
    shutil.rmtree(tmp, ignore_errors=True)


DATASETS = {
    "creams": creams,
    "gfed5": gfed5,
    "weather": weather,
    "crop_calendar": crop_calendar,
    "population": population,
    "goes": goes,
    "wdpa": wdpa,
    "fwi": fwi,
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", nargs="+", choices=list(DATASETS), help="just these datasets")
    ap.add_argument("--list", action="store_true", help="show the dataset names and exit")
    a = ap.parse_args()
    if a.list:
        print(" ".join(DATASETS))
        return
    failed = []
    for name in a.only or DATASETS:
        print(f"[{name}]", flush=True)
        t0 = time.time()
        try:
            DATASETS[name]()
            print(f"[{name}] done in {time.time() - t0:.0f}s", flush=True)
        except DiskFull as e:
            sys.exit(f"[{name}] stopped: {e}")
        except Exception as e:  # one dead server shouldn't stop the rest
            failed.append(name)
            print(f"[{name}] FAILED: {type(e).__name__}: {e}", flush=True)
    print("all done" + (f", failed: {' '.join(failed)} (rerun to retry)" if failed else ""), flush=True)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
