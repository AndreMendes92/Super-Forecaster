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
avoids whatever is blocking those specifically. Confirmed reachable
from Render (see below) — the live download itself is no longer the
open question it once was.

The parsing/matching/point-in-polygon logic (osmium + shapely,
described below) was verified end-to-end in this session against a
hand-built OSM XML fixture before ever deploying. What deploying it
revealed instead: three consecutive live refreshes all left the
cache's `meta` timestamp completely frozen — not updated even once,
which livability_cache.py's refresh_all() does after *every single
municipality* in its main loop. The OSM fetch runs before that loop
starts, so the only way to get zero writes across three separate runs
is the whole backend process dying outright — a segfault or an
OOM-kill (Render's free tier caps memory at 512MB; a full metro-area
.osm.pbf plus osmium/shapely parsing is a real risk of hitting that),
neither of which a Python try/except can catch. Compounding suspect:
this session's own osmium/shapely testing was done under Python
3.11.15, while Render's build log has shown Python 3.14.3 — a very new
release those C-extension wheels may not fully support.

Fixed by moving the actual download+parse into its own OS process
(_osm_extract_worker.py, launched via subprocess.run() below) instead
of running it in the same process as the web server. A crash in there
now only takes down that child process — fetch_all_osm_counts() treats
a non-zero exit, a timeout, or unparseable output the same as any other
_fetch_with_fallback failure (this source is unavailable for this run,
falls back to last-known-good), so the rest of refresh_all() — crime,
population, income, rent, and the per-municipality loop — keeps running
regardless of what happens to OSM parsing. This doesn't yet prove
*why* the crash happens (still unconfirmed whether it's OOM, a Python
3.14 incompatibility, or something else — Render's own process logs
around the crash would confirm which), but it stops that one source
from being able to take the whole app down with it either way.

Uses pyosmium (Python bindings for the C++ libosmium library) to
stream the extract rather than loading a parsed structure into memory,
and shapely (new dependency, but a lightweight one — no GDAL, no
geopandas) for point-in-polygon tests against each municipality's
boundary (see livability_boundaries.py) using shapely.prepared for
fast repeated contains() checks across every node in the extract.
"""

import json
import os
import subprocess
import sys

import osmium
import shapely.geometry as sgeom

BBBIKE_EXTRACT_URL = "https://download.bbbike.org/osm/bbbike/Vancouver/Vancouver.osm.pbf"

_WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_osm_extract_worker.py")
# Generous, but still well inside what would let a fully-hung child
# run out the clock on the rest of refresh_all() — the download alone
# is given 120s (see _osm_extract_worker.py), so this leaves real
# headroom for the osmium parse pass on top of that.
_WORKER_TIMEOUT_SECONDS = 240

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
    from Render's network entirely. The actual download+parse runs in
    a separate OS process (_osm_extract_worker.py) — see this module's
    docstring for why: it crashed the whole backend process outright
    on Render, in a way no in-process try/except could catch.

    Returns {municipality_id: {"walkability": int, "transit": int,
    "green_space": int}} for every municipality with a usable cached
    boundary polygon. A municipality genuinely outside the extract's
    coverage area (unverified exactly how far BBBike's per-city extract
    reaches — it may not cover every outlying Metro Vancouver
    municipality) will just come back with all-zero counts,
    indistinguishable here from "really has none nearby" — a known
    limitation, not a crash.

    Raises a plain Python exception — caught by
    livability_cache._fetch_with_fallback() same as any other source —
    if the worker process times out, exits non-zero (including from a
    segfault or an OOM-kill, which produce an abnormal/negative
    returncode rather than output), or produces output that isn't the
    JSON it's supposed to print on success.
    """
    if not municipality_boundaries:
        raise ValueError("No usable municipality boundary polygons to count OSM features against (none cached yet?).")

    try:
        proc = subprocess.run(
            [sys.executable, _WORKER_SCRIPT],
            input=json.dumps(municipality_boundaries),
            capture_output=True,
            text=True,
            timeout=_WORKER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"OSM extract worker timed out after {_WORKER_TIMEOUT_SECONDS}s "
            "(download+parse taking too long, or hung) — see livability_osm_extract.py"
        ) from e

    if proc.returncode != 0:
        raise RuntimeError(
            f"OSM extract worker exited with code {proc.returncode} — likely a native-level "
            "crash or out-of-memory kill in osmium/shapely, isolated to this subprocess rather "
            f"than the main backend process. stderr: {(proc.stderr or '')[-2000:]}"
        )

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"OSM extract worker produced unparseable output: {proc.stdout[:500]!r} "
            f"/ stderr: {(proc.stderr or '')[-1000:]}"
        ) from e
