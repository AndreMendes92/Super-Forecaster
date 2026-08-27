"""
livability_statcan.py
-----------------------
Three of the "Best Places to Live" criteria — crime & safety,
population density, and household income — come from StatCan tables
where the value we need is picked out by matching a GEO name column
(police service, or census subdivision), not one hand-picked vector ID
per municipality the way statcan_cache.py works for the housing price
tracker. A single vector covers exactly one series; these tables need
one row per municipality *out of dozens*, so this module downloads
each table's full CSV once (StatCan's "full table download" mechanism)
and filters it, rather than hunting down 20+ individual vector IDs by
hand.

Tables used (StatCan product IDs — the "pid" in a table's URL,
e.g. https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=3510006301):
  35-10-0063-01 (pid 3510006301) — Crime severity index and weighted
    clearance rates, police services in British Columbia
  98-10-0002-01 (pid 9810000201) — Population and dwelling counts,
    Canada and census subdivisions (municipalities), 2021 Census
  98-10-0057-01 (pid 9810005701) — Household income statistics by
    household type, census divisions and census subdivisions, 2021
    Census

First real deploy already caught one design mistake: this originally
resolved each table's CSV zip URL via StatCan's WDS
`getFullTableDownloadCSV` endpoint, which timed out every time on
Render — turns out that's exactly the kind of unreliable WDS
metadata-style call statcan_cache.py already warned about (only the
plain vector-data endpoint, `getDataFromVectorsAndLatestNPeriods`, has
ever been reliable from Render; every other WDS endpoint this app has
tried — getCubeMetadata, getSeriesInfoFromCubePidCoord, and now
getFullTableDownloadCSV — has failed). Fix: skip the WDS lookup
entirely and hit StatCan's static bulk-download URL directly
(`n1/tbl/csv/{8-digit-pid}-eng.zip`), a plain file GET rather than a
dynamic query.

The column-name matching below (_find_col) is deliberately tolerant of
StatCan's two common table shapes (long-format with a
"Statistics"/characteristic + VALUE column, or wide-format with one
column per characteristic) since which shape these three tables
actually use hadn't been confirmed as of this module's last edit — a
genuinely unexpected layout still raises a clear ValueError rather
than silently returning wrong numbers, surfaced per-table in
/livability/refresh-cache's summary.
"""

import io
import zipfile

import pandas as pd

from .statcan_http import get_bytes

CRIME_PID = 3510006301
POPULATION_PID = 9810000201
INCOME_PID = 9810005701


def _find_col(df: pd.DataFrame, must_contain: list[str]) -> str | None:
    """First column whose (lowercased) name contains every given substring."""
    for col in df.columns:
        low = str(col).lower()
        if all(k in low for k in must_contain):
            return col
    return None


def _require_col(df: pd.DataFrame, must_contain: list[str], table_label: str) -> str:
    col = _find_col(df, must_contain)
    if col is None:
        raise ValueError(
            f"Couldn't find a column matching {must_contain} in {table_label} "
            f"(columns were: {list(df.columns)}) — StatCan's layout may have "
            "changed; update the matching logic in livability_statcan.py."
        )
    return col


def _static_zip_url(product_id: int) -> str:
    """
    StatCan's product IDs are 10 digits (8-digit table id + 2-digit
    cube/version suffix, e.g. 3510006301 = table 35-10-0063-01). The
    static bulk-CSV download drops that last 2-digit suffix, e.g.
    https://www150.statcan.gc.ca/n1/tbl/csv/35100063-eng.zip
    """
    return f"https://www150.statcan.gc.ca/n1/tbl/csv/{str(product_id)[:8]}-eng.zip"


def fetch_full_table_csv(product_id: int) -> pd.DataFrame:
    """
    Downloads a StatCan table's full data from its static bulk-CSV zip
    URL (a plain file GET, not a WDS query — see the module docstring
    for why) and returns it as a DataFrame.
    """
    zip_bytes = get_bytes(_static_zip_url(product_id), timeout=90)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv") and "metadata" not in n.lower()]
        if not csv_names:
            raise ValueError(f"No data CSV found in the downloaded zip for product {product_id} (files: {zf.namelist()})")
        with zf.open(csv_names[0]) as f:
            return pd.read_csv(f, low_memory=False)


def fetch_crime_severity_index() -> tuple[dict[str, float], str | None]:
    """
    Returns ({police service GEO name (as StatCan wrote it): CSI value
    for the most recent year available}, that year as a string).
    Deliberately keeps the *overall* Crime Severity Index (not the
    violent-only or youth-only variants also in this table).
    """
    df = fetch_full_table_csv(CRIME_PID)
    geo_col = _require_col(df, ["geo"], "the crime severity index table")
    date_col = _require_col(df, ["ref_date"], "the crime severity index table")
    stat_col = _find_col(df, ["statistics"]) or _find_col(df, ["violation"])
    value_col = _require_col(df, ["value"], "the crime severity index table")

    rows = df
    if stat_col:
        rows = rows[rows[stat_col].astype(str).str.fullmatch(r"Crime severity index", case=False, na=False)]
        if rows.empty:
            # fall back to a looser match if StatCan's exact wording differs
            rows = df[df[stat_col].astype(str).str.contains("crime severity index", case=False, na=False)
                       & ~df[stat_col].astype(str).str.contains("violent|youth|weighted", case=False, na=False)]

    if rows.empty:
        raise ValueError("No 'Crime severity index' rows found — check the Statistics column values in table 35-10-0063-01.")

    latest_year = rows[date_col].max()
    latest = rows[rows[date_col] == latest_year]

    result = {
        str(row[geo_col]): float(row[value_col])
        for _, row in latest.iterrows()
        if pd.notna(row[value_col])
    }
    return result, str(latest_year)


def fetch_population_density() -> dict[str, dict]:
    """
    Returns {census-subdivision GEO name: {"population": int,
    "land_area_km2": float, "density_per_km2": float}} for every CSD
    in the table (all of Canada — callers filter to Metro Vancouver).
    """
    df = fetch_full_table_csv(POPULATION_PID)
    geo_col = _require_col(df, ["geo"], "the population/density table")

    pop_col = _find_col(df, ["population", "2021"])
    area_col = _find_col(df, ["land area"])
    density_col = _find_col(df, ["population density"])

    if pop_col and area_col:
        # "wide" shape: one column per characteristic
        result = {}
        for _, row in df.iterrows():
            pop = row.get(pop_col)
            area = row.get(area_col)
            density = row.get(density_col) if density_col else (
                float(pop) / float(area) if pd.notna(pop) and pd.notna(area) and float(area) > 0 else None
            )
            result[str(row[geo_col])] = {
                "population": int(pop) if pd.notna(pop) else None,
                "land_area_km2": float(area) if pd.notna(area) else None,
                "density_per_km2": float(density) if density is not None and pd.notna(density) else None,
            }
        return result

    # "long" shape fallback: a characteristic dimension + VALUE column
    stat_col = _find_col(df, ["characteristic"]) or _find_col(df, ["statistics"])
    value_col = _find_col(df, ["value"])
    if not stat_col or not value_col:
        raise ValueError(
            "Unexpected column layout in the population/density table "
            f"(columns were: {list(df.columns)}) — update livability_statcan.py."
        )

    result: dict[str, dict] = {}
    for geo, group in df.groupby(geo_col):
        pop_row = group[group[stat_col].astype(str).str.contains("population, 2021", case=False, na=False)]
        area_row = group[group[stat_col].astype(str).str.contains("land area", case=False, na=False)]
        density_row = group[group[stat_col].astype(str).str.contains("population density", case=False, na=False)]
        pop = pop_row[value_col].iloc[0] if not pop_row.empty else None
        area = area_row[value_col].iloc[0] if not area_row.empty else None
        density = density_row[value_col].iloc[0] if not density_row.empty else (
            float(pop) / float(area) if pop and area else None
        )
        result[str(geo)] = {
            "population": int(pop) if pop is not None and pd.notna(pop) else None,
            "land_area_km2": float(area) if area is not None and pd.notna(area) else None,
            "density_per_km2": float(density) if density is not None and pd.notna(density) else None,
        }
    return result


def fetch_median_household_income() -> dict[str, float]:
    """
    Returns {census-division/subdivision GEO name: median total income
    of households (dollars)} from the most recent year in the table.
    """
    df = fetch_full_table_csv(INCOME_PID)
    geo_col = _require_col(df, ["geo"], "the household income table")
    date_col = _find_col(df, ["ref_date"])
    value_col = _require_col(df, ["value"], "the household income table")
    stat_col = _find_col(df, ["statistics"]) or _find_col(df, ["household income statistics"]) or _find_col(df, ["characteristic"])
    household_type_col = _find_col(df, ["household type"])

    rows = df
    if stat_col:
        rows = rows[rows[stat_col].astype(str).str.contains("median total income of household", case=False, na=False)]
    if household_type_col:
        rows = rows[rows[household_type_col].astype(str).str.contains("total.*household|all household", case=False, na=False, regex=True)]

    if rows.empty:
        raise ValueError("No 'median total income of household' rows found — check table 98-10-0057-01's column values.")

    if date_col:
        latest_year = rows[date_col].max()
        rows = rows[rows[date_col] == latest_year]

    return {
        str(row[geo_col]): float(row[value_col])
        for _, row in rows.iterrows()
        if pd.notna(row[value_col])
    }
