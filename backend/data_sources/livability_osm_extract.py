"""
livability_osm_extract.py
---------------------------
Walkability, transit, and green space — take two. livability_osm.py's
live Overpass queries turned out to be blocked from Render's network
at the connection level across five independent public instances (see
that module's docstring for the full story) — TLS impersonation,
timeouts, and mirror lists didn't help because the failures are mostly
outright TCP connection refusals, not an HTTP-level bot check.

This module sidesteps the live query API entirely: it downloads a
static OpenStreetMap data extract for the Vancouver region as a plain
HTTPS file (BBBike.org's per-city extracts, no signup, no live query
endpoint) and counts matching features locally — the same kind of fix
that worked for StatCan (a bulk file download instead of a live API
call). BBBike is a completely different host/hosting setup than every
Overpass instance already tried, so there's real reason to expect this
avoids whatever is blocking those specifically.

UNVERIFIED as of this module's first version — this session had no
outbound network access to confirm download.bbbike.org is actually
reachable from Render. What *is* independently confirmed: the exact
extract URL (found via search, cross-referenced against a real
third-party OSM-download library that uses the same URL pattern) and
the parsing/matching/point-in-polygon logic (tested end-to-end in this
session against a hand-built OSM XML fixture — osmium correctly parsed
tagged nodes, matched them against the criteria below, and correctly
included/excluded points based on which prepared shapely polygon
contains them). The one thing genuinely untested is the live download
itself — check /livability/refresh-cache's summary after deploying.

Uses pyosmium (Python bindings for the C++ libosmium library) to
stream the extract rather than loading a parsed structure into memory,
and shapely (new dependency, but a lightweight one — no GDAL, no
geopandas) for point-in-polygon tests against each municipality's
boundary (see livability_boundaries.py) using shapely.prepared for
fast repeated contains() checks across every node in the extract.
"""

import tempfile

import osmium
import shapely.geometry as sgeom
from shapely.prepared import prep

from . import ipv4_http

BBBIKE_EXTRACT_URL = "https://download.bbbike.org/osm/bbbike/Vancouver/Vancouver.osm.pbf"

_WALKABILITY_TAG_PAIRS = {
    ("shop", "supermarket"), ("shop", "convenience"), ("shop", "greengrocer"),
    ("amenity", "restaurant"), ("amenity", "cafe"), ("amenity", "fast_food"), ("amenity", "pharmacy"),
}
_TRANSIT_RAILWAY_VALUES = {"station", "halt", "tram_stop"}
_GREEN_SPACE_LEISURE_VALUES = {"park", "nature_reserve", "garden"}


def _matches_walkability(tags: dict) -> bool:
    return ("shop", tags.get("shop")) in _WALKABILITY_TAG_PAIRS or ("amenity", tags.get("amenity")) in _WALKABILITY_TAG_PAIRS


def _matches_transit(tags: dict) -> bool:
    return (
        tags.get("highway") == "bus_stop"
        or "public_transport" in tags
        or tags.get("railway") in _TRANSIT_RAILWAY_VALUES
    )


def _matches_green_space(tags: dict) -> bool:
    return tags.get("leisure") in _GREEN_SPACE_LEISURE_VALUES


class _CountingHandler(osmium.SimpleHandler):
    """
    Streams every node in the extract once, testing each tagged node
    against the three criteria and against every municipality's
    (pre-prepared, for fast repeated contains() checks) boundary
    polygon.

    Node-only, deliberately: OSM tags parks/nature reserves as
    polygons (ways/relations) more often than as point nodes, so this
    undercounts green space specifically — resolving way/relation
    geometry needs osmium's two-pass area-assembly machinery, real
    extra complexity this first version skips in favour of shipping
    something correct-if-incomplete rather than nothing. Walkability
    and transit features are overwhelmingly point nodes in practice
    (shops, restaurants, bus stops), so those two criteria aren't
    meaningfully affected by this simplification — flagged in the UI
    either way via the "current OpenStreetMap data" source label.
    """

    def __init__(self, polygons: dict):
        super().__init__()
        self.polygons = polygons  # {municipality_id: prepared shapely polygon}
        self.counts = {mid: {"walkability": 0, "transit": 0, "green_space": 0} for mid in polygons}

    def node(self, n):
        if not n.location.valid():
            return
        tags = {t.k: t.v for t in n.tags}
        if not tags:
            return

        walk = _matches_walkability(tags)
        transit = _matches_transit(tags)
        green = _matches_green_space(tags)
        if not (walk or transit or green):
            return

        point = sgeom.Point(n.location.lon, n.location.lat)
        for municipality_id, polygon in self.polygons.items():
            if polygon.contains(point):
                if walk:
                    self.counts[municipality_id]["walkability"] += 1
                if transit:
                    self.counts[municipality_id]["transit"] += 1
                if green:
                    self.counts[municipality_id]["green_space"] += 1
                break


def fetch_all_osm_counts(municipality_boundaries: dict) -> dict:
    """
    municipality_boundaries: {municipality_id: GeoJSON geometry dict}
    (from the "boundaries" cache — see livability_boundaries.py).

    Downloads the BBBike Vancouver-region extract once and counts
    every criterion for every municipality in a single streaming pass
    — far cheaper than the old per-municipality live Overpass queries,
    and doesn't depend on a query API that's turned out to be blocked
    from Render's network entirely.

    Returns {municipality_id: {"walkability": int, "transit": int,
    "green_space": int}} for every municipality with a usable cached
    boundary polygon. A municipality genuinely outside the extract's
    coverage area (unverified exactly how far BBBike's per-city extract
    reaches — it may not cover every outlying Metro Vancouver
    municipality) will just come back with all-zero counts,
    indistinguishable here from "really has none nearby" — a known
    limitation, not a crash.
    """
    resp = ipv4_http.get(BBBIKE_EXTRACT_URL, timeout=120)
    resp.raise_for_status()
    pbf_bytes = resp.content

    polygons = {}
    for municipality_id, geometry in municipality_boundaries.items():
        try:
            polygons[municipality_id] = prep(sgeom.shape(geometry))
        except Exception:
            continue
    if not polygons:
        raise ValueError("No usable municipality boundary polygons to count OSM features against (none cached yet?).")

    with tempfile.NamedTemporaryFile(suffix=".osm.pbf") as f:
        f.write(pbf_bytes)
        f.flush()
        handler = _CountingHandler(polygons)
        osmium.apply(f.name, handler)

    return handler.counts
