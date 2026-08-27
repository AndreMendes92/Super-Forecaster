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

None of this has been run against a live response — the environment
this was built in has no outbound network access to overpass-api.de
at all (every external host this app touches was unreachable from
that sandbox; see the long comment in statcan_http.py for the same
situation with StatCan, which *has* been live-verified in the past,
unlike this module). The Overpass query shapes below follow Overpass
QL's documented syntax, but the exact `osm_area_name` per municipality
(see livability_geography.py) is this module's biggest real risk —
if a name doesn't resolve to an area, that municipality is recorded as
failed for these three criteria rather than guessed at, and shows up
in /livability/refresh-cache's summary so it's easy to spot and fix
after the first real deploy.
"""

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

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
    boundary and returns a feature count. Raises ValueError if
    Overpass has no area for that name (distinct from a genuine zero
    count, which is a valid — if surprising — result), or
    requests.exceptions.RequestException on network/HTTP failure.
    """
    query = f"""
    [out:json][timeout:60];
    area["name"="{osm_area_name}"]["boundary"="administrative"]->.a;
    (
      {tag_fragment}
    );
    out count;
    """
    resp = requests.post(OVERPASS_URL, data={"data": query}, timeout=75)
    resp.raise_for_status()
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
