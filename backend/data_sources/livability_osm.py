"""
livability_osm.py
-------------------
Three of the seven "Best Places to Live" criteria — walkability,
transit access, and green space — are all sourced the same way: a
single free, no-signup, no-API-key host (the OpenStreetMap Overpass
API) that can resolve a municipality's administrative boundary by name
and count features inside it server-side, in one query. That removes
the need for this app to fetch or store any boundary polygons itself,
or for a geometry library (shapely/geopandas) — Overpass's own
`area["name"="X"]["boundary"="administrative"]` clause does the
point/way-in-polygon work.

First real deploy caught two things that couldn't be tested for in
advance:

1. Render's outbound networking doesn't reliably support IPv6, and
   plain `requests` calls were failing with "Network is unreachable"
   (curl trying an IPv6 route with none available) — see ipv4_http.py,
   which fixes this the same way statcan_http.py already had to for
   StatCan.
2. Even over IPv4, overpass-api.de itself refused the connection
   outright ("Failed to connect... Could not connect to server") —
   that public instance is known to block/rate-limit traffic from
   cloud-hosting IP ranges (AWS, Render, etc.) as anti-abuse
   protection, independent of anything on this app's side. Fixed by
   trying a short list of alternate public Overpass mirrors in order
   and using whichever one actually accepts the connection, rather
   than hardcoding the one host most likely to block a Render IP.

The exact `osm_area_name` per municipality (see livability_geography.py)
is this module's remaining real risk — if a name doesn't resolve to an
area on whichever mirror answers, that municipality is recorded as
failed for these three criteria rather than guessed at, and shows up
in /livability/refresh-cache's summary so it's easy to spot and fix.
"""

from . import ipv4_http

# Tried in order — the first one that accepts the connection is used.
# overpass-api.de is the "official" instance but is known to reject
# cloud/datacenter IPs (which is exactly what bit this on Render); the
# other two are independently-run public mirrors that generally don't.
OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

# Tag groups per criterion. Kept as Overpass QL fragments (not Python
# data) so the query text is easy to compare directly against
# Overpass's own docs/examples when debugging a bad result.
_WALKABILITY_TAGS = """
  node["shop"~"^(supermarket|convenience|greengrocer)$"](area.a);
  node["amenity"~"^(restaurant|cafe|fast_food|pharmacy)$"](area.a);
"""
_TRANSIT_TAGS = """
  node["highway"="bus_stop"](area.a);
  node["public_transport"="platform"](area.a);
  node["railway"~"^(station|halt|tram_stop)$"](area.a);
"""
_GREEN_SPACE_TAGS = """
  way["leisure"~"^(park|nature_reserve|garden)$"](area.a);
  relation["leisure"~"^(park|nature_reserve|garden)$"](area.a);
"""

_CRITERION_TAGS = {
    "walkability": _WALKABILITY_TAGS,
    "transit": _TRANSIT_TAGS,
    "green_space": _GREEN_SPACE_TAGS,
}


def _run_count_query(osm_area_name: str, tag_fragment: str) -> int:
    """
    Runs one Overpass QL query scoped to a municipality's admin
    boundary and returns a feature count. Tries each URL in
    OVERPASS_URLS in turn, moving on only on a connection-level
    failure (a mirror refusing/unreachable) — an actual query result,
    including a "no such area" ValueError, is trusted and returned
    immediately rather than retried against a different mirror.
    """
    query = f"""
    [out:json][timeout:60];
    area["name"="{osm_area_name}"]["boundary"="administrative"]->.a;
    (
      {tag_fragment}
    );
    out count;
    """

    last_network_error = None
    for url in OVERPASS_URLS:
        try:
            resp = ipv4_http.post(url, data={"data": query}, timeout=75)
            resp.raise_for_status()
        except Exception as e:
            last_network_error = e
            continue

        data = resp.json()
        elements = data.get("elements", [])
        if not elements:
            raise ValueError(
                f"Overpass returned no result for area \"{osm_area_name}\" — "
                "the OSM admin boundary name may not match (see livability_geography.py)."
            )

        # `out count;` returns one element of type "count" with a "tags"
        # dict like {"total": "137", "nodes": "90", "ways": "47", ...}.
        count_tags = elements[0].get("tags", {})
        if "total" not in count_tags:
            raise ValueError(f"Unexpected Overpass response shape for \"{osm_area_name}\": {data}")

        return int(count_tags["total"])

    raise last_network_error


def fetch_osm_counts(osm_area_name: str) -> dict[str, int]:
    """
    Runs all three OSM-sourced criteria for one municipality. Returns
    {"walkability": count, "transit": count, "green_space": count}.
    Raises on the first failing criterion — callers (livability_cache.py)
    catch per-municipality, not per-criterion, so one bad area name
    fails that municipality's OSM criteria as a group rather than
    silently mixing real and missing data.
    """
    return {
        criterion: _run_count_query(osm_area_name, tags)
        for criterion, tags in _CRITERION_TAGS.items()
    }
