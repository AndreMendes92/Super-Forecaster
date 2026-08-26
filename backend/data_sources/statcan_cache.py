"""
statcan_cache.py
-----------------
Reads/writes the StatcanCache table (db.py) and drives the daily
background refresh.

Why this exists, and why it's built around hardcoded vector IDs:

StatCan's New Housing Price Index (table 18-10-0205-01) is fetched via
three possible WDS API calls:
  1. getCubeMetadata                 — lists every Geography/type value
  2. getSeriesInfoFromCubePidCoord   — turns a chosen combo into a vector ID
  3. getDataFromVectorsAndLatestNPeriods — fetches the actual numbers for
     a vector ID you already know

Verified directly against the live Render deployment: calls 1 and 2
are unreliable — they've failed with a 406 or a connection timeout on
every attempt, from multiple distinct fixes (browser headers, TLS
fingerprint impersonation, forcing IPv4). Call 3, when given a vector
ID you already have, works fine and returns real data quickly.

So instead of resolving geography names to vector IDs dynamically
(which needs calls 1+2), CURATED_VECTORS below hardcodes the vector ID
for each geography we support, found by hand via StatCan's own table
page (Add/Remove data -> Customize layout -> tick "Display vector
identifier and coordinate"). Every read in this app then only ever
needs call 3.

Also worth knowing: this table's Geography dimension only goes down to
province/region level — StatCan does not publish a separate NHPI
series per city (no "Toronto" or "Vancouver" row) within this
particular table. Canada + regions is the real ceiling here, not a
limitation of this app.

A daily background job (see /statcan/refresh-cache in main.py) still
refreshes the actual index VALUES for each vector — those genuinely
change over time — it just never needs the unreliable metadata calls
to do it.
"""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from db import StatcanCache
from .statcan import fetch_statcan_vector

# Vector IDs verified by hand against StatCan's own table page — see
# the module docstring. Add more any time the same way: open
# https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1810020501,
# Add/Remove data -> Customize layout -> tick "Display vector
# identifier and coordinate", and read off the "Total (house and
# land)" row's vector number for whichever geography you want to add.
CURATED_VECTORS: dict[str, int] = {
    "Canada": 111955442,
    "Ontario": 111955490,
    "Prairie region": 111955523,
    "British Columbia": 111955550,
}

# Static — this table has exactly these 3 index components and that
# essentially never changes, so there's no reason to fetch it live.
HOUSING_TYPES = ["Total (house and land)", "House only", "Land only"]
DEFAULT_HOUSING_TYPE = "Total (house and land)"


def _get(db: Session, key: str):
    row = db.query(StatcanCache).filter(StatcanCache.key == key).first()
    return json.loads(row.value_json) if row else None


def _set(db: Session, key: str, value) -> None:
    row = db.query(StatcanCache).filter(StatcanCache.key == key).first()
    payload = json.dumps(value)
    if row is None:
        db.add(StatcanCache(key=key, value_json=payload))
    else:
        row.value_json = payload
        row.updated_at = datetime.now(timezone.utc)
    db.commit()


def static_geographies() -> list[dict]:
    """The geographies this app actually supports reliably — no live call needed."""
    return [{"member_id": vector_id, "name": name} for name, vector_id in CURATED_VECTORS.items()]


def static_housing_types() -> list[dict]:
    return [{"member_id": i, "name": name} for i, name in enumerate(HOUSING_TYPES)]


def get_cached_geographies(db: Session):
    return _get(db, "geographies") or static_geographies()


def get_cached_housing_types(db: Session):
    return _get(db, "housing_types") or static_housing_types()


def _series_key(geography: str, housing_type: str) -> str:
    return f"series:{geography}:{housing_type}"


def find_cached_series(db: Session, geography_query: str, housing_type_query: str):
    """
    Matches a typed geography + housing type against the curated
    (hardcoded-vector) set and returns the cached series if found.
    Returns None for anything outside that set, so the caller falls
    back to a (best-effort, less reliable) live metadata-based lookup.
    """
    query_lower = geography_query.strip().lower()
    matched_geo = next(
        (name for name in CURATED_VECTORS
         if name.lower() == query_lower or name.lower().startswith(query_lower)),
        None,
    )
    if matched_geo is None:
        return None

    type_lower = housing_type_query.strip().lower()
    if "total" not in type_lower and type_lower not in DEFAULT_HOUSING_TYPE.lower():
        return None

    return _get(db, _series_key(matched_geo, DEFAULT_HOUSING_TYPE))


def cache_series(db: Session, geography: str, housing_type: str, series: dict) -> None:
    _set(db, _series_key(geography, housing_type), series)


def refresh_all(db: Session) -> dict:
    """
    Fetches fresh data for every curated vector and stores it in the
    cache. Meant to run in the background, triggered daily. Unlike the
    original version of this function, this never calls the
    unreliable metadata endpoints — every fetch here is the one WDS
    call (getDataFromVectorsAndLatestNPeriods) that's actually held up
    reliably.

    Returns a summary dict, logged by the caller.
    """
    # Geographies/housing types are static now — seed them straight
    # from the hardcoded lists rather than a live call.
    _set(db, "geographies", static_geographies())
    _set(db, "housing_types", static_housing_types())

    summary = {"series_ok": [], "series_failed": []}

    for geography, vector_id in CURATED_VECTORS.items():
        try:
            df = fetch_statcan_vector(vector_id, latest_n=120)
            series = {
                "vector_id": vector_id,
                "geography": geography,
                "housing_type": DEFAULT_HOUSING_TYPE,
                "week_start": [d.strftime("%Y-%m-%d") for d in df["period_start"]],
                "volume": df["value"].tolist(),
            }
            cache_series(db, geography, DEFAULT_HOUSING_TYPE, series)
            summary["series_ok"].append(geography)
        except Exception as e:
            summary["series_failed"].append({"geography": geography, "error": str(e)})

    return summary
