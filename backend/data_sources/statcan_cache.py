"""
statcan_cache.py
-----------------
Reads/writes the StatcanCache table (db.py) and drives the daily
background refresh.

Why this exists: StatCan's WDS API is real, free data, but calling it
live within a web request has proven unreliable (see the long comment
in statcan_http.py) — intermittent bot-scoring, IPv6 hangs, and plain
slowness that can lose a race against Render's own gateway timeout and
come back as an ugly, unhelpful 502 HTML page instead of a clean
error. This decouples "does the page load fast" from "is StatCan
cooperating right now" by refreshing a cache once a day in the
background (see /statcan/refresh-cache in main.py), where slowness and
retries don't hurt anyone, and having the live endpoints read from
that cache first.

Anything not covered by the cache (an uncurated geography, or a
non-default index type) still falls back to a live fetch — just with
a short timeout so a bad moment for StatCan fails fast and cleanly
instead of hanging.
"""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from db import StatcanCache
from . import statcan_geography
from .statcan import fetch_statcan_vector

# Canada + every province + the major CMAs — covers the large majority
# of what people will actually look up. Anything outside this list
# still works, just via a live (short-timeout) fetch instead of cache.
CURATED_GEOGRAPHIES = [
    "Canada",
    "Newfoundland and Labrador", "Prince Edward Island", "Nova Scotia",
    "New Brunswick", "Quebec", "Ontario", "Manitoba", "Saskatchewan",
    "Alberta", "British Columbia",
    "St. John's", "Halifax", "Moncton", "Saint John",
    "Québec", "Montréal", "Sherbrooke", "Trois-Rivières", "Gatineau",
    "Ottawa", "Toronto", "Hamilton", "Kitchener", "London", "Windsor", "Barrie",
    "Winnipeg", "Regina", "Saskatoon",
    "Calgary", "Edmonton",
    "Kelowna", "Vancouver", "Victoria",
]

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


def get_cached_geographies(db: Session):
    return _get(db, "geographies")


def get_cached_housing_types(db: Session):
    return _get(db, "housing_types")


def _series_key(geography: str, housing_type: str) -> str:
    return f"series:{geography}:{housing_type}"


def find_cached_series(db: Session, geography_query: str, housing_type_query: str):
    """
    Best-effort cache lookup for the common case: a curated major
    geography + the default 'Total (house and land)' index. Anything
    else returns None so the caller falls back to a live fetch.
    """
    query_lower = geography_query.strip().lower()
    matched_geo = next(
        (name for name in CURATED_GEOGRAPHIES
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
    Fetches fresh metadata + the curated geography list from StatCan
    live, and stores it all in the cache. Meant to run in the
    background, triggered daily — it's fine for this to take a few
    minutes (dozens of live StatCan calls), nothing is waiting on it.

    Returns a summary dict, logged by the caller.
    """
    summary = {"geographies": False, "housing_types": False, "series_ok": [], "series_failed": []}

    try:
        _set(db, "geographies", statcan_geography.list_geographies())
        summary["geographies"] = True
    except Exception as e:
        summary["geographies_error"] = str(e)

    try:
        _set(db, "housing_types", statcan_geography.list_housing_types())
        summary["housing_types"] = True
    except Exception as e:
        summary["housing_types_error"] = str(e)

    for geography in CURATED_GEOGRAPHIES:
        try:
            match = statcan_geography.get_vector_id(geography, DEFAULT_HOUSING_TYPE)
            df = fetch_statcan_vector(match["vector_id"], latest_n=120)
            series = {
                "vector_id": match["vector_id"],
                "geography": match["geography"],
                "housing_type": match["housing_type"],
                "week_start": [d.strftime("%Y-%m-%d") for d in df["period_start"]],
                "volume": df["value"].tolist(),
            }
            cache_series(db, match["geography"], match["housing_type"], series)
            summary["series_ok"].append(geography)
        except Exception as e:
            summary["series_failed"].append({"geography": geography, "error": str(e)})

    return summary
