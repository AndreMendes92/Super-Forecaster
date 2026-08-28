"""
livability_cache.py
---------------------
Orchestrates the "Best Places to Live" tab's data refresh: pulls every
criterion for every one of the 22 curated Metro Vancouver
municipalities (livability_geography.py) and stores one JSON blob per
municipality in the LivabilityCache table (db.py) — the same
cache-then-serve pattern statcan_cache.py already uses for the housing
price tracker, for the same reason: several of the underlying sources
here (StatCan, Overpass, CMHC) are too slow or occasionally unreliable
to call live within a request. See POST /livability/refresh-cache in
main.py, meant to be called on a schedule (monthly — these datasets
update annually/quarterly at most) by
.github/workflows/refresh-livability-cache.yml.

refresh_all() never lets one bad source take down the whole refresh —
each of the five whole-region pulls (crime, population/density,
income, rent, and OSM walkability/transit/green-space counts — see
livability_osm_extract.py) is wrapped individually, so a single
failure just means that criterion shows "not available" in the UI
instead of a stale or half-written cache. The returned summary dict
names exactly what succeeded and failed — check it (or the backend
logs, which print it) after the first real deploy, the same way
statcan_cache.py's refresh summary was originally used to verify its
own curated vector IDs.

Learned the hard way on first real deploy: try/except around each
*source* isn't enough on its own if the *whole job* takes too long —
with every OSM/StatCan source failing, the old version's worst-case
runtime (dozens of municipalities x multiple mirrors x long timeouts)
could run for a very long time, and Render appears to kill/restart the
backend process before such a run ever finishes — which silently loses
the *entire* refresh, including the one write at the very end, leaving
the cache stuck showing whatever the last successful run produced
(confusingly looking like "nothing changed" rather than "this run
never finished"). Two fixes: much shorter per-call timeouts throughout
this codebase's livability_* modules, and — the more important one —
this function now saves `places` and a running summary to the cache
after *every* municipality, not just once at the end, and prints a
one-line progress log per municipality. A mid-run kill now leaves
real partial data and a clear stopping point in the logs, instead of
silently discarding everything.

Learned the hard way *again*, on a run where StatCan happened to fail
all three of its tables at once: the previous version re-fetched every
whole-table source from scratch on every refresh and rebuilt `places`
entirely from that run's results — so a single bad-luck run against a
flaky external host didn't just fail to add new data, it silently
*erased* previously-good data by overwriting it with nulls. Fixed by
caching each raw source (crime, population, income, rent, and OSM
counts) permanently under its own key, the same way boundaries already
were — a refresh only ever overwrites a cached source when a fetch
actually succeeds; on failure it falls back to whatever was last
cached (there may be nothing cached yet, in which case it's still "not
available", same as before). Each of those five fetches also gets one
quick retry before falling back, to absorb a short blip rather than
treating it the same as a real outage.
"""

import json
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from db import LivabilityCache
from .livability_geography import MUNICIPALITIES
from .livability_statcan import fetch_crime_severity_index, fetch_population_density, fetch_median_household_income
from .livability_osm_extract import fetch_all_osm_counts
from .livability_cmhc import fetch_average_rent_by_municipality
from .livability_boundaries import fetch_boundary


def _get(db: Session, key: str):
    row = db.query(LivabilityCache).filter(LivabilityCache.key == key).first()
    return json.loads(row.value_json) if row else None


def _set(db: Session, key: str, value) -> None:
    row = db.query(LivabilityCache).filter(LivabilityCache.key == key).first()
    payload = json.dumps(value)
    if row is None:
        db.add(LivabilityCache(key=key, value_json=payload))
    else:
        row.value_json = payload
        row.updated_at = datetime.now(timezone.utc)
    db.commit()


def static_municipalities() -> list[dict]:
    return [{"id": m.id, "name": m.name} for m in MUNICIPALITIES]


def get_cached_municipalities(db: Session) -> list[dict]:
    return _get(db, "municipalities") or static_municipalities()


def get_cached_places(db: Session) -> dict:
    return _get(db, "places") or {}


def get_cache_meta(db: Session) -> dict:
    return _get(db, "meta") or {}


def get_cached_boundaries(db: Session) -> dict:
    """{municipality_id: GeoJSON geometry} for the heat map — see
    livability_boundaries.py. Fetched once per municipality, ever
    (boundaries don't change), so this can be empty/partial right
    after the very first refresh and fills in as later refreshes skip
    whatever's already cached and pick up the rest."""
    return _get(db, "boundaries") or {}


def _match_geo(geo_values: dict, prefix: str | None):
    """
    Returns the value whose GEO/name key starts with `prefix`
    (case-insensitive) — used for both the StatCan GEO-name matching
    and, since it's generic on dict value type, the CMHC rent lookup.
    None in, None out (an unconfirmed police-service mapping, see
    livability_geography.py).
    """
    if not prefix:
        return None
    prefix_lower = prefix.strip().lower()
    for name, value in geo_values.items():
        if str(name).strip().lower().startswith(prefix_lower):
            return value
    return None


def _fetch_with_fallback(db: Session, summary: dict, cache_key: str, fetch_fn, retries: int = 1, retry_delay: float = 5.0):
    """
    Runs fetch_fn(), retrying up to `retries` extra times on failure
    (absorbing a short blip) before giving up and falling back to
    whatever this source's cache already holds — which may be
    yesterday's data (fine, these sources change slowly) or nothing at
    all (a source that has never once succeeded). Only overwrites the
    cache when a fetch actually succeeds, so a bad run never erases a
    good one. See the module docstring for why this exists.
    """
    cached = _get(db, cache_key)
    last_error = None

    for attempt in range(retries + 1):
        try:
            result = fetch_fn()
            _set(db, cache_key, result)
            summary["sources_ok"].append(cache_key)
            return result
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(retry_delay)

    summary["sources_failed"].append({"source": cache_key, "error": str(last_error)})
    if cached is not None:
        summary["sources_reused_from_cache"].append(cache_key)
        return cached
    return None


def refresh_all(db: Session) -> dict:
    summary = {
        "sources_ok": [], "sources_failed": [], "sources_reused_from_cache": [],
        "boundaries_failed": [],
    }

    _set(db, "municipalities", static_municipalities())
    boundaries = get_cached_boundaries(db)

    crime_cached = _fetch_with_fallback(db, summary, "raw_crime", fetch_crime_severity_index)
    crime_by_geo, crime_year = crime_cached if crime_cached else ({}, None)

    pop_by_geo = _fetch_with_fallback(db, summary, "raw_population", fetch_population_density) or {}
    income_by_geo = _fetch_with_fallback(db, summary, "raw_income", fetch_median_household_income) or {}
    rent_by_name = _fetch_with_fallback(
        db, summary, "raw_rent",
        lambda: fetch_average_rent_by_municipality([m.name for m in MUNICIPALITIES]),
    ) or {}
    # One whole-region OSM extract download + local count (see
    # livability_osm_extract.py), not 22 individual live queries — only
    # possible once at least some boundaries are cached, and only
    # covers whichever municipalities' boundaries were already cached
    # *before* this run (any fetched further down in this same run
    # show up in next run's OSM pass instead).
    osm_by_municipality = _fetch_with_fallback(
        db, summary, "raw_osm_counts",
        lambda: fetch_all_osm_counts(boundaries),
    ) or {}

    places = {}
    for m in MUNICIPALITIES:
        place = {"id": m.id, "name": m.name, "criteria": {}}

        crime_value = _match_geo(crime_by_geo, m.police_service_match)
        place["criteria"]["crime_severity_index"] = {
            "value": crime_value,
            "unit": "index points (lower = safer)",
            "as_of": crime_year,
            "source": "Statistics Canada, table 35-10-0063-01",
            "note": m.shared_police_note if crime_value is not None else "police service not confidently mapped yet — see livability_geography.py",
        }

        matched_pop = _match_geo(pop_by_geo, m.census_geo_match)
        land_area = matched_pop.get("land_area_km2") if matched_pop else None
        place["criteria"]["population_density"] = {
            "value": matched_pop.get("density_per_km2") if matched_pop else None,
            "unit": "people per km²",
            "as_of": "2021 Census",
            "source": "Statistics Canada, table 98-10-0002-01",
            "note": "not counted in the composite score by default — density is a matter of taste, not objectively good or bad",
        }

        income_value = _match_geo(income_by_geo, m.census_geo_match)
        place["criteria"]["median_household_income"] = {
            "value": income_value,
            "unit": "CAD / year (median household)",
            "as_of": "2021 Census",
            "source": "Statistics Canada, table 98-10-0057-01",
            "note": "context only — not counted in the composite score by default",
        }

        rent_value = rent_by_name.get(m.name)
        place["criteria"]["average_rent"] = {
            "value": rent_value,
            "unit": "CAD / month (avg. 2-bedroom, primary rental market)",
            "as_of": "CMHC Rental Market Survey",
            "source": "CMHC Rental Market Survey data tables",
            "note": "rent, not resale/purchase price — see README",
        }

        osm_counts = osm_by_municipality.get(m.id)
        for criterion_key, unit_label in [
            ("walkability", "amenities per km² (grocery/restaurant/cafe/pharmacy density — a proxy, not a real Walk Score)"),
            ("transit", "transit stops per km²"),
            ("green_space", "park/green-space features per km² — undercounts large parks tagged as polygons, not points; see livability_osm_extract.py"),
        ]:
            count = osm_counts.get(criterion_key) if osm_counts else None
            per_km2 = (count / land_area) if (count is not None and land_area) else None
            place["criteria"][criterion_key] = {
                "value": per_km2,
                "raw_count": count,
                "unit": unit_label,
                "as_of": "current OpenStreetMap data (BBBike Vancouver-region extract)",
                "source": "OpenStreetMap",
                "note": None,
            }

        places[m.id] = place

        # Boundary geometry, for the heat map — fetched once ever per
        # municipality (see livability_boundaries.py's docstring on
        # why: it never changes, and Nominatim's usage policy asks
        # that repeat/bulk lookups be avoided). A 1.1s pause after an
        # actual fetch (not a skip) respects its 1-request/second cap.
        if m.id not in boundaries:
            try:
                boundaries[m.id] = fetch_boundary(m.osm_area_name)
                time.sleep(1.1)
            except Exception as e:
                summary["boundaries_failed"].append({"municipality": m.id, "error": str(e)})

        # Persist after every municipality (not just once at the end) —
        # see the module docstring for why. Cheap relative to the
        # network calls above, and means a mid-run kill still leaves
        # real, current partial data instead of silently reverting to
        # whatever the last fully-completed run produced.
        _set(db, "places", places)
        _set(db, "boundaries", boundaries)
        _set(db, "meta", {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "municipalities_done": len(places),
            "municipalities_total": len(MUNICIPALITIES),
        })
        print(f"[livability cache refresh] {len(places)}/{len(MUNICIPALITIES)} done — {m.id}: "
              f"crime={crime_value is not None} rent={rent_value is not None} osm={osm_counts is not None} "
              f"boundary={m.id in boundaries}")

    return summary
