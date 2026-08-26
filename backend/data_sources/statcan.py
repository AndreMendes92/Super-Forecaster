"""
statcan.py
----------
Fetch a real time series from Statistics Canada's free, public Web
Data Service (WDS) API. No API key or signup needed.

How to find a vector ID for a dataset you want (e.g. BC or Vancouver
housing prices, city population, etc.):
1. Go to the relevant StatCan table page, e.g.
   https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1810020501
   (this one is "New housing price index, monthly")
2. Click "Add/Remove data"
3. Click "Customize layout"
4. Tick "Display vector identifier and coordinate", click Apply
5. The table now shows a "Vector" column (e.g. v111955442) next to
   each row — pick the row for the geography you want (e.g. British
   Columbia, or Vancouver CMA)

A verified working example to start with: v111955442 is the Canada-
wide "Total (house and land)" New Housing Price Index, monthly,
table 18-10-0205-01.
"""

import pandas as pd

from .statcan_http import post_json

STATCAN_WDS_URL = "https://www150.statcan.gc.ca/t1/wds/rest/getDataFromVectorsAndLatestNPeriods"


def _parse_wds_response(data: list, vector_id: int) -> pd.DataFrame:
    """
    Parses the JSON structure StatCan's WDS API returns into a clean
    DataFrame. Split out from the network call so this logic can be
    tested with a saved/fabricated response, without needing to hit
    the live API.
    """
    if not data:
        raise ValueError(f"StatCan API returned an empty response for vector {vector_id}.")

    entry = data[0]
    if entry.get("status") != "SUCCESS":
        raise ValueError(
            f"StatCan API could not return vector {vector_id} "
            f"(status: {entry.get('status')}). It may not exist — double "
            "check the vector ID on the StatCan table page."
        )

    points = entry.get("object", {}).get("vectorDataPoint", [])
    if not points:
        raise ValueError(
            f"Vector {vector_id} exists but returned no data points "
            "(the series may be suppressed or discontinued)."
        )

    df = pd.DataFrame(points)
    df["period_start"] = pd.to_datetime(df["refPer"])
    df["value"] = pd.to_numeric(df["value"])
    df = df[["period_start", "value"]].sort_values("period_start").reset_index(drop=True)
    return df


def fetch_statcan_vector(vector_id: int, latest_n: int = 120) -> pd.DataFrame:
    """
    Fetch the latest N data points for a single StatCan vector.
    Returns a DataFrame with columns: period_start (datetime), value (float)
    Raises ValueError with a clear message on bad vector ID / no data,
    or requests.exceptions.RequestException on network problems.
    """
    body = [{"vectorId": int(vector_id), "latestN": int(latest_n)}]
    data = post_json(STATCAN_WDS_URL, body)
    return _parse_wds_response(data, vector_id)
