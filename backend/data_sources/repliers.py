"""
repliers.py
-----------
Fetches real (or sandbox-sample, on a free key) MLS sold-price
statistics from the Repliers API, grouped by month and filtered by
property type and city.

Docs: https://docs.repliers.io/reference/getting-started-with-your-api
Auth: REPLIERS-API-KEY header. Read from the REPLIERS_API_KEY
environment variable — never hardcode a key in this file or commit
one to git.
"""

import os
import requests
import pandas as pd

REPLIERS_BASE_URL = "https://api.repliers.io/listings"

# Common Canadian MLS property type values (varies slightly by board).
# Exposed to the frontend so users pick a valid value rather than guess.
COMMON_PROPERTY_TYPES = [
    "Detached",
    "Semi-Detached",
    "Att/Row/Twnhouse",
    "Condo Apt",
    "Condo Townhouse",
]


def _get_api_key() -> str:
    key = os.environ.get("REPLIERS_API_KEY")
    if not key:
        raise ValueError(
            "REPLIERS_API_KEY is not set. Add it as an environment "
            "variable on Render (Settings \u2192 Environment) \u2014 never hardcode it."
        )
    return key


def _parse_statistics_response(data: dict, stat_key: str) -> pd.DataFrame:
    """
    Parses Repliers' {"statistics": {"soldPrice": {"mth": {...}}}}
    shape into a clean DataFrame. Split out from the network call so
    this can be tested against a fabricated response without hitting
    the live API.

    stat_key: "avg" or "med"
    """
    try:
        monthly = data["statistics"]["soldPrice"]["mth"]
    except KeyError:
        raise ValueError(
            "Unexpected response shape \u2014 no statistics.soldPrice.mth found. "
            f"Response keys were: {list(data.keys())}"
        )

    if not monthly:
        raise ValueError(
            "No monthly sold-price data returned for this city/property "
            "type/date range combination. Try a broader city or a longer "
            "history window."
        )

    rows = []
    for month_str, values in monthly.items():
        if stat_key not in values:
            continue
        rows.append({
            "period_start": pd.to_datetime(month_str + "-01"),
            "value": values[stat_key],
            "count": values.get("count"),
        })

    if not rows:
        raise ValueError(f"No '{stat_key}' values found in the monthly statistics.")

    df = pd.DataFrame(rows).sort_values("period_start").reset_index(drop=True)
    return df


def fetch_repliers_price_history(
    city: str,
    property_type: str = None,
    months_back: int = 24,
    stat: str = "avg",
) -> pd.DataFrame:
    """
    Fetches monthly sold-price statistics for a city (and optionally a
    single property type) over the last `months_back` months.

    stat: "avg" or "med" (average vs median sold price)

    Returns a DataFrame with columns: period_start, value, count
    """
    if stat not in ("avg", "med"):
        raise ValueError("stat must be 'avg' or 'med'")

    api_key = _get_api_key()
    min_sold_date = (pd.Timestamp.today() - pd.DateOffset(months=months_back)).strftime("%Y-%m-%d")

    params = {
        "city": city,
        "status": "U",
        "lastStatus": "Sld",
        "minSoldDate": min_sold_date,
        "statistics": f"{stat}-soldPrice,grp-mth",
        "listings": "false",
    }
    if property_type:
        params["propertyType"] = property_type

    headers = {"REPLIERS-API-KEY": api_key}
    resp = requests.get(REPLIERS_BASE_URL, params=params, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    return _parse_statistics_response(data, stat_key=stat)
