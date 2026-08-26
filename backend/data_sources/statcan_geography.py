"""
statcan_geography.py
---------------------
Lets the app work for "any location in Canada" against Statistics
Canada's New Housing Price Index (table 18-10-0205-01) WITHOUT
hardcoding a list of vector IDs per city (which would be fragile and
easy to get wrong).

Instead, this reads StatCan's own metadata for the table at request
time:

1. getCubeMetadata       -> the table's dimensions (Geography, and
                             "New housing price indexes" with values
                             like "Total (house and land)", "House
                             only", "Land only") and every member of
                             each, with their member IDs.
2. getSeriesInfoFromCubePidCoord -> given a coordinate built from the
                             chosen member IDs, returns the vector ID
                             for that exact series.
3. That vector ID is then handed to fetch_statcan_vector() in
   statcan.py, which already knows how to pull the actual numbers.

Metadata is cached in-process (it barely ever changes) so we don't
hit StatCan's API on every request.

NOTE on what this index actually measures: StatCan's NHPI tracks
prices builders would sell NEW houses for — it is NOT a resale/MLS
average price, and it does NOT break out condos specifically. It's
the best free, real, city-level series available in Canada, and it's
a solid proxy for market direction, but say so in the UI. For
condo/detached/townhouse-specific price levels, see the Repliers
integration (sample data on a free key).
"""

import time
import requests

WDS_BASE = "https://www150.statcan.gc.ca/t1/wds/rest"
NHPI_PRODUCT_ID = 1810020501  # table 18-10-0205-01, "New housing price index, monthly"

_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; HousingTracker/1.0)",
}

_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours — this metadata is effectively static
_cache = {"metadata": None, "fetched_at": 0.0}


def _fetch_cube_metadata() -> dict:
    now = time.time()
    if _cache["metadata"] is not None and (now - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _cache["metadata"]

    resp = requests.post(
        f"{WDS_BASE}/getCubeMetadata",
        json=[{"productId": NHPI_PRODUCT_ID}],
        headers=_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data or data[0].get("status") != "SUCCESS":
        raise ValueError("Could not load Statistics Canada's table metadata (getCubeMetadata failed).")

    metadata = data[0]["object"]
    _cache["metadata"] = metadata
    _cache["fetched_at"] = now
    return metadata


def _find_dimension(metadata: dict, name_contains: str) -> dict:
    for dim in metadata["dimension"]:
        if name_contains.lower() in dim["dimensionNameEn"].lower():
            return dim
    raise ValueError(f"Couldn't find a '{name_contains}' dimension in the StatCan table — its layout may have changed.")


def list_geographies() -> list[dict]:
    """Every geography (Canada, provinces, and CMAs/cities) this table has data for."""
    metadata = _fetch_cube_metadata()
    dim = _find_dimension(metadata, "Geography")
    return [{"member_id": m["memberId"], "name": m["memberNameEn"]} for m in dim["member"]]


def list_housing_types() -> list[dict]:
    """The 3 index components StatCan tracks: Total (house and land), House only, Land only."""
    metadata = _fetch_cube_metadata()
    dim = _find_dimension(metadata, "housing price indexes")
    return [{"member_id": m["memberId"], "name": m["memberNameEn"]} for m in dim["member"]]


def _match_member(members: list[dict], query: str) -> dict:
    query_lower = query.strip().lower()
    # exact match first, then "starts with", then "contains"
    for m in members:
        if m["name"].lower() == query_lower:
            return m
    for m in members:
        if m["name"].lower().startswith(query_lower):
            return m
    for m in members:
        if query_lower in m["name"].lower():
            return m
    raise ValueError(f"'{query}' not found among StatCan's available values: {[m['name'] for m in members][:15]}...")


def get_vector_id(geography: str, housing_type: str = "Total (house and land)") -> dict:
    """
    Resolves a human-typed geography + housing type (e.g. "Toronto",
    "Total (house and land)") to a StatCan vector ID, by looking up
    the real member names/IDs from the table's own metadata and
    asking StatCan for the series at that coordinate.

    Returns {"vector_id": int, "geography": str, "housing_type": str}
    matched to StatCan's exact names (useful to show the user exactly
    what was matched, in case their typed text was a partial match).
    """
    metadata = _fetch_cube_metadata()
    geo_dim = _find_dimension(metadata, "Geography")
    type_dim = _find_dimension(metadata, "housing price indexes")

    geo_member = _match_member(geo_dim["member"], geography)
    type_member = _match_member(type_dim["member"], housing_type)

    # Coordinates are up to 10 dot-separated dimension-member IDs, in
    # dimension-position order, padded with zeros for unused dims.
    positions = {geo_dim["dimensionPositionId"]: geo_member["memberId"],
                 type_dim["dimensionPositionId"]: type_member["memberId"]}
    coordinate = ".".join(str(positions.get(pos, 0)) for pos in range(1, 11))

    resp = requests.post(
        f"{WDS_BASE}/getSeriesInfoFromCubePidCoord",
        json=[{"productId": NHPI_PRODUCT_ID, "coordinate": coordinate}],
        headers=_HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data or data[0].get("status") != "SUCCESS":
        raise ValueError(
            f"StatCan doesn't have a series for '{geo_member['name']}' x "
            f"'{type_member['name']}' — try a different geography (some "
            "smaller areas only report a subset of index types)."
        )

    vector_id = data[0]["object"]["vectorId"]
    return {
        "vector_id": vector_id,
        "geography": geo_member["name"],
        "housing_type": type_member["name"],
    }
