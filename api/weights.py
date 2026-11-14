"""Per-hotspot harmonization weights, shared by the API and pipeline/build_grid.py.

A hotspot row h is joined to avail a (which satellites observed that day) and calib c
(factors for its 1 deg cell x calendar month x day/night, from pipeline/calibrate.py).

Vegetation-fire rows use the vegetation factors; industrial / persistent hot spots (STATIC_SQL)
use their own *_st factors.

viirs unit (NOAA-20 equivalent): NOAA-20 rows count 1. On days without NOAA-20
(2012-2017, outages) S-NPP rows count k_s. Before VIIRS (or if both VIIRS were down)
MODIS rows count k_ta * k_s when Terra and Aqua both observed, else k_t * k_s / k_a * k_s
for the one that did: MODIS -> S-NPP -> NOAA-20. All other rows count 0.

modis unit (Terra+Aqua equivalent): MODIS rows count 1 when both MODIS observed, else
m_t / m_a for the one that did. VIIRS rows count 0.

s is the log-spread of the factor used (chained factors add in quadrature); the 90% band
on a weight is w * exp(+-Z90 * s).
"""

Z90 = 1.6449

CELL_SQL = "(least(CAST(floor(h.lat) AS INT), 89) + 90) * 360 + (least(CAST(floor(h.lon) AS INT), 179) + 180)"

# persistent hot spots (pipeline/flags.py), with a 3x3-cell buffer for geolocation scatter
PERS_TABLE_SQL = """
    CREATE TABLE pers AS SELECT DISTINCT p.iy + dy AS iy, p.ix + dx AS ix
    FROM read_parquet('{path}') p, range(-1, 2) t1(dy), range(-1, 2) t2(dx)
"""
PERS_EMPTY_SQL = "CREATE TABLE pers (iy INTEGER, ix INTEGER)"
PERS_JOIN = "LEFT JOIN pers p ON p.iy = CAST(floor(h.lat * 100) AS INT) AND p.ix = CAST(floor(h.lon * 100) AS INT)"

# not a vegetation fire: FIRMS type 1-3 (volcano, static land source, offshore) or persistent
STATIC_SQL = "(coalesce(h.type, 0) <> 0 OR p.iy IS NOT NULL)"

JOINS = f"""
    JOIN avail a ON a.d = h.acq_date
    LEFT JOIN calib c ON c.cell = {CELL_SQL} AND c.m = month(h.acq_date) AND c.dn = h.dn
    {PERS_JOIN}
"""


def create_pers(con, derived) -> None:
    path = derived / "persistent.parquet"
    con.execute(PERS_TABLE_SQL.format(path=path.as_posix()) if path.exists() else PERS_EMPTY_SQL)


# raw VIIRS as the record shows it: NOAA-20, or S-NPP on days without NOAA-20
RAW_VIIRS_SQL = "(h.sat = 'NOAA20' OR (h.sat = 'SNPP' AND NOT a.has_noaa20))"


def k(name: str) -> str:
    """Factor column for this row: the industrial set (*_st) for static / persistent hot spots."""
    return f"(CASE WHEN {STATIC_SQL} THEN c.{name}_st ELSE c.{name} END)"


def weight_sql(unit: str) -> tuple[str, str]:
    """(weight, log-spread) SQL expressions for one hotspot row."""
    if unit == "viirs":
        w = f"""CASE h.sat
            WHEN 'NOAA20' THEN 1.0
            WHEN 'SNPP'   THEN CASE WHEN a.has_noaa20 THEN 0.0 ELSE {k('k_s')} END
            WHEN 'Terra'  THEN CASE WHEN a.has_snpp OR a.has_noaa20 THEN 0.0
                                    WHEN a.has_aqua THEN {k('k_ta')} * {k('k_s')} ELSE {k('k_t')} * {k('k_s')} END
            WHEN 'Aqua'   THEN CASE WHEN a.has_snpp OR a.has_noaa20 THEN 0.0
                                    WHEN a.has_terra THEN {k('k_ta')} * {k('k_s')} ELSE {k('k_a')} * {k('k_s')} END
            ELSE 0.0 END"""
        s = f"""CASE h.sat
            WHEN 'SNPP'   THEN {k('s_k_s')}
            WHEN 'Terra'  THEN sqrt(CASE WHEN a.has_aqua THEN {k('s_k_ta')} ^ 2 ELSE {k('s_k_t')} ^ 2 END + {k('s_k_s')} ^ 2)
            WHEN 'Aqua'   THEN sqrt(CASE WHEN a.has_terra THEN {k('s_k_ta')} ^ 2 ELSE {k('s_k_a')} ^ 2 END + {k('s_k_s')} ^ 2)
            ELSE 0.0 END"""
    elif unit == "modis":
        w = """CASE h.sat
            WHEN 'Terra' THEN CASE WHEN a.has_aqua THEN 1.0 ELSE c.m_t END
            WHEN 'Aqua'  THEN CASE WHEN a.has_terra THEN 1.0 ELSE c.m_a END
            ELSE 0.0 END"""
        s = """CASE h.sat
            WHEN 'Terra' THEN CASE WHEN a.has_aqua THEN 0.0 ELSE c.s_m_t END
            WHEN 'Aqua'  THEN CASE WHEN a.has_terra THEN 0.0 ELSE c.s_m_a END
            ELSE 0.0 END"""
    else:
        raise ValueError(unit)
    return f"coalesce({w}, 0.0)", f"coalesce({s}, 0.0)"
