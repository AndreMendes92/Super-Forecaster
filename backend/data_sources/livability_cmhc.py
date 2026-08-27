"""
livability_cmhc.py
--------------------
Housing cost for the "Best Places to Live" tab: CMHC's Rental Market
Survey average rent (2-bedroom, primary market) by Metro Vancouver
zone/municipality. Rent, not resale price — deliberately, and labeled
as such in the UI: no free, structured, no-signup source of resale/MLS
prices by municipality exists (this repo's README already documents
the same gap for the housing-price-tracker tab; see Repliers there for
the paid alternative).

This is, by a wide margin, the shakiest integration in this tab.
CMHC's interactive Housing Market Information Portal
(www03.cmhc-schl.gc.ca/hmip-pimh) has no documented JSON/CSV API — the
only third-party tooling that exists for it (e.g. the R package
mountainMath/cmhc) works by scraping that interactive portal's pages,
which is exactly the kind of "unreliable to call from a server" trap
this repo has already hit once with StatCan's metadata endpoints (see
statcan_cache.py). Rather than build the same trap twice, this module
targets CMHC's separately-published *static* Rental Market Report data
table (an .xlsx workbook, not the interactive portal) — but the exact
current download URL and sheet layout could not be confirmed here (see
the module docstrings in livability_osm.py / livability_statcan.py for
why: this session had no outbound access to cmhc-schl.gc.ca at all).

CMHC_RENT_XLSX_URL is therefore a Render environment variable, not a
hardcoded constant — so if/when the guessed default URL turns out to
be wrong or CMHC moves the file, it's a one-line env var fix on Render
(no redeploy needed), the same escape hatch REPLIERS_API_KEY already
gives this app for its own uncertain dependency.

If this module fails outright (bad URL, unexpected workbook layout,
etc.), livability_cache.py records it as a failed source and every
municipality's housing-cost criterion shows "not available" rather
than the whole refresh failing — see refresh_all() there.
"""

import io
import os

import pandas as pd

# Best documented guess at CMHC's current Metro Vancouver primary
# rental market data table, per the "Rental Market Report Data Tables"
# listing linked from
# https://www.cmhc-schl.gc.ca/professionals/housing-markets-data-and-research/housing-data/data-tables/rental-market
# Override with the real URL via Render's environment variables if
# this is stale.
DEFAULT_CMHC_RENT_XLSX_URL = (
    "https://www.cmhc-schl.gc.ca/professionals/housing-markets-data-and-research/"
    "housing-data/data-tables/rental-market/rental-market-report-data-tables"
)


def _get_xlsx_url() -> str:
    return os.environ.get("CMHC_RENT_XLSX_URL", DEFAULT_CMHC_RENT_XLSX_URL)


def fetch_average_rent_by_municipality(municipality_names: list[str]) -> dict[str, float]:
    """
    Downloads the CMHC rent workbook and returns
    {municipality name (as passed in): average 2-bedroom rent ($)} for
    every name it can find a match for. Municipalities not found in
    the workbook are simply absent from the returned dict — callers
    treat a missing key as "not available", not an error.

    Raises requests.exceptions.RequestException / ValueError if the
    file can't be fetched or parsed at all (a total failure, logged as
    one failed source rather than 22 individual ones).
    """
    import requests  # local import: this is the one non-StatCan HTTP call in this module

    resp = requests.get(_get_xlsx_url(), timeout=60)
    resp.raise_for_status()

    sheets = pd.read_excel(io.BytesIO(resp.content), sheet_name=None, header=None)

    result: dict[str, float] = {}
    for _sheet_name, sheet_df in sheets.items():
        for muni in municipality_names:
            if muni in result:
                continue
            # Find a cell that starts with the municipality name (zone
            # rows in CMHC's tables are typically labeled this way),
            # then take the first plausible rent value ($400-$10,000)
            # in that same row as "the" average rent.
            mask = sheet_df.apply(
                lambda col: col.astype(str).str.strip().str.lower() == muni.strip().lower()
            )
            hits = mask.any(axis=1)
            if not hits.any():
                continue
            row = sheet_df[hits].iloc[0]
            numeric_values = pd.to_numeric(row, errors="coerce").dropna()
            plausible = numeric_values[(numeric_values >= 400) & (numeric_values <= 10000)]
            if not plausible.empty:
                result[muni] = float(plausible.iloc[0])

    return result
