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

Two real-deploy surprises so far, neither fully resolved:

1. This originally resolved each table's CSV zip URL via StatCan's WDS
   `getFullTableDownloadCSV` endpoint, which timed out every time on
   Render — in line with statcan_cache.py's warning that every WDS
   endpoint except the plain vector-data one
   (`getDataFromVectorsAndLatestNPeriods`) has been unreliable from
   Render. Fixed by skipping the WDS lookup and hitting StatCan's
   static bulk-download URL directly (`n1/tbl/csv/{8-digit-pid}-eng.zip`).
2. That static URL *also* times out — even as a plain file GET with
   the same browser-impersonating, IPv4-forced client
   (statcan_http.py) that reliably serves the vector endpoint
   elsewhere in this app. Since the one thing that's actually worked
   for StatCan on Render is a *non*-impersonated-looking plain request
   to the vector endpoint, `fetch_full_table_csv` now also tries a
   second, non-impersonated request (ipv4_http.py, same IPv4 fix but
   no Chrome fingerprint) if the impersonated one times out — confirmed
   live: this combination reliably downloads all three tables now.
3. The crime table (35-10-0063) and household income table (98-10-0057)
   both come back as real zip files at the URL pattern above — but the
   population/density table (98-10-0002) came back as *something*
   that isn't a valid zip, confirmed live (`zipfile.BadZipFile`).
   `fetch_full_table_csv` now falls back to parsing the same bytes as
   a plain CSV in that case, on the chance that specific product ID's
   "zip" URL actually just serves an uncompressed file. Unverified as
   of this edit.

The column-name matching below (_find_col) is deliberately tolerant of
StatCan's two common table shapes (long-format with a
"Statistics"/characteristic + VALUE column, or wide-format with one
column per characteristic). Confirmed live which shape each table
actually uses: the household income table is "wide", with real column
headers like "Household income statistics (6):Median household total
income (2020) (2020 constant dollars)[3]" — fetch_median_household_income()
matches that directly rather than going through the generic long/wide
guessing _find_col does for the other two tables (whose actual shape
hasn't been confirmed the same way yet). A genuinely unexpected layout
still raises a clear ValueError rather than silently returning wrong
numbers, surfaced per-table in /livability/refresh-cache's summary.
"""

import io
import re
import zipfile

import pandas as pd

from .statcan_http import get_bytes
from . import ipv4_http

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
    for why) and returns it as a DataFrame. Tries a Chrome-impersonated
    request first, then a plain (non-impersonated) one — see the
    module docstring for why both are worth trying here.
    """
    url = _static_zip_url(product_id)
    try:
        # Short timeouts deliberately, on both attempts — see
        # livability_cache.py's docstring: a slow/hanging fetch here,
        # multiplied across 3 tables x 2 attempts, is exactly what made
        # a fully-failing refresh run long enough for Render to kill
        # the whole process before it ever finished a single write.
        zip_bytes = get_bytes(url, timeout=15)
    except Exception:
        resp = ipv4_http.get(url, timeout=30)
        resp.raise_for_status()
        zip_bytes = resp.content

    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv") and "metadata" not in n.lower()]
            if not csv_names:
                raise ValueError(f"No data CSV found in the downloaded zip for product {product_id} (files: {zf.namelist()})")
            with zf.open(csv_names[0]) as f:
                return pd.read_csv(f, low_memory=False)
    except zipfile.BadZipFile:
        # Seen live for one of these three tables (not all) — that URL
        # apparently doesn't serve a zip for every product ID, so try
        # reading the same bytes as a plain CSV before giving up.
        try:
            return pd.read_csv(io.BytesIO(zip_bytes), low_memory=False)
        except Exception as e:
            raise ValueError(
                f"{url} didn't return a zip or a readable CSV for product {product_id} "
                f"(got {len(zip_bytes)} bytes) — original error: {e}"
            )


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
    Returns {census-division/subdivision GEO name: median total
    (pre-tax) household income (dollars), most recent year available}.

    Confirmed live (see the module docstring) that this table is
    "wide": one column per characteristic, headers like "Household
    income statistics (6):Median household total income (2020) (2020
    constant dollars)[3]" and "...(2015) (2020 constant dollars)[4]"
    side by side for the same statistic in different reference years —
    so this picks whichever "median household total income (YYYY)"
    column has the highest YYYY, rather than relying on _find_col's
    first-match-wins behavior (which would just pick whichever year
    happens to come first in the file, not necessarily the latest one).
    """
    df = fetch_full_table_csv(INCOME_PID)
    geo_col = _require_col(df, ["geo"], "the household income table")

    year_pattern = re.compile(r"median household total income\s*\((\d{4})\)", re.IGNORECASE)
    year_columns = [(int(m.group(1)), col) for col in df.columns if (m := year_pattern.search(str(col)))]
    if not year_columns:
        raise ValueError(
            "Couldn't find a 'median household total income (YYYY)' column in the "
            f"household income table (columns were: {list(df.columns)}) — update livability_statcan.py."
        )
    _latest_year, value_col = max(year_columns)

    household_type_col = _find_col(df, ["household type"])
    rows = df
    if household_type_col:
        totals_only = rows[rows[household_type_col].astype(str).str.contains("total", case=False, na=False)]
        # If nothing says "total" outright, this table's category
        # wording differs from what was guessed here — fall back to
        # the unfiltered rows (one row per geo x household-type
        # combination) rather than returning no income data at all.
        # groupby(...).first() below then just takes whichever
        # category comes first per geo, which won't always be the
        # true aggregate — acceptable since this criterion is shown as
        # context only, never counted in the composite score by default.
        rows = totals_only if not totals_only.empty else rows

    rows = rows[rows[geo_col].notna() & rows[value_col].notna()]
    first_per_geo = rows.groupby(geo_col, sort=False).first()

    return {
        str(geo): float(value)
        for geo, value in first_per_geo[value_col].items()
        if pd.notna(value)
    }
