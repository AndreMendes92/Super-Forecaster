"""
livability_boundaries.py
--------------------------
Fetches each municipality's administrative boundary as GeoJSON
geometry, for the heat map on the "Best Places to Live" tab (see the
Choroplethmapbox in frontend/app.py). Uses OpenStreetMap's Nominatim
geocoder with `polygon_geojson=1`, which hands back ready-to-use
GeoJSON directly — simpler and more robust than asking Overpass for
raw way/relation geometry and assembling multipolygon rings by hand.

Boundaries essentially never change, so livability_cache.py only ever
fetches a municipality's boundary once and keeps it cached
indefinitely — later refreshes skip any municipality already present
in the "boundaries" cache key. This isn't just an optimization:
Nominatim's usage policy caps public requests at 1/second and asks
that bulk/repeated lookups be avoided, which a monthly re-fetch of all
22 would otherwise brush up against for no real reason.

Falls back to Photon (komoot's geocoder, also GeoJSON-based) if
Nominatim doesn't resolve a name or the request fails outright — the
same "a free public geocoder might reject a cloud IP" risk this app
already hit once with Overpass (see livability_osm.py). Unverified as
of this module's last edit — check /livability/refresh-cache's summary
(the "boundaries_failed" list) after deploying.
"""

from . import ipv4_http

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_URL = "https://photon.komoot.io/api"

# Nominatim's usage policy requires a real, identifying User-Agent —
# requests without one are liable to be blocked outright.
_HEADERS = {
    "User-Agent": "SuperForecaster-BestPlacesToLive/1.0 (https://github.com/AndreMendes92/Super-Forecaster)",
}


def _fetch_from_nominatim(query: str) -> dict | None:
    resp = ipv4_http.get(
        NOMINATIM_URL,
        params={"q": query, "format": "jsonv2", "polygon_geojson": 1, "limit": 1, "countrycodes": "ca"},
        headers=_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results or "geojson" not in results[0]:
        return None
    return results[0]["geojson"]


def _fetch_from_photon(query: str) -> dict | None:
    resp = ipv4_http.get(
        PHOTON_URL,
        params={"q": query, "limit": 1, "osm_tag": "boundary:administrative"},
        headers=_HEADERS, timeout=15,
    )
    resp.raise_for_status()
    features = resp.json().get("features", [])
    if not features:
        return None
    return features[0].get("geometry")


def fetch_boundary(osm_area_name: str) -> dict:
    """
    Returns GeoJSON geometry (a Polygon or MultiPolygon dict) for one
    municipality's administrative boundary. Tries Nominatim first,
    then Photon. Raises ValueError if neither can resolve it.
    """
    query = f"{osm_area_name}, Metro Vancouver, British Columbia, Canada"

    try:
        geometry = _fetch_from_nominatim(query)
        if geometry:
            return geometry
    except Exception:
        pass

    geometry = _fetch_from_photon(query)
    if geometry:
        return geometry

    raise ValueError(f'Neither Nominatim nor Photon could resolve a boundary for "{osm_area_name}".')
