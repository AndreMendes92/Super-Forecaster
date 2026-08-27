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
each of the four whole-table pulls (crime, population/density, income,
rent) and each municipality's Overpass lookup is wrapped individually,
so a single failure just means that criterion/municipality shows "not
available" in the UI instead of a stale or half-written cache. The
returned summary dict names exactly what succeeded and failed — check
it (or the backend logs, which print it) after the first real deploy,
the same way statcan_cache.py's refresh summary was originally used to
verify its own curated vector IDs.
"""

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from db import LivabilityCache
from .livability_geography import MUNICIPALITIES
from .livability_statcan import fetch_crime_severity_index, fetch_population_density, fetch_median_household_income
from .livability_osm import fetch_osm_counts
from .livability_cmhc import fetch_average_rent_by_municipality


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


def refresh_all(db: Session) -> dict:
    summary = {"sources_ok": [], "sources_failed": [], "municipalities_osm_failed": []}

    _set(db, "municipalities", static_municipalities())

    crime_by_geo, crime_year = {}, None
    try:
        crime_by_geo, crime_year = fetch_crime_severity_index()
        summary["sources_ok"].append("crime_severity_index")
    except Exception as e:
        summary["sources_failed"].append({"source": "crime_severity_index", "error": str(e)})

    pop_by_geo = {}
    try:
        pop_by_geo = fetch_population_density()
        summary["sources_ok"].append("population_density")
    except Exception as e:
        summary["sources_failed"].append({"source": "population_density", "error": str(e)})

    income_by_geo = {}
    try:
        income_by_geo = fetch_median_household_income()
        summary["sources_ok"].append("median_household_income")
    except Exception as e:
        summary["sources_failed"].append({"source": "median_household_income", "error": str(e)})

    rent_by_name = {}
    try:
        rent_by_name = fetch_average_rent_by_municipality([m.name for m in MUNICIPALITIES])
        summary["sources_ok"].append("average_rent")
    except Exception as e:
        summary["sources_failed"].append({"source": "average_rent", "error": str(e)})

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

        osm_counts = None
        try:
            osm_counts = fetch_osm_counts(m.osm_area_name)
        except Exception as e:
            summary["municipalities_osm_failed"].append({"municipality": m.id, "error": str(e)})

        for criterion_key, unit_label in [
            ("walkability", "amenities per km² (grocery/restaurant/cafe/pharmacy density — a proxy, not a real Walk Score)"),
            ("transit", "transit stops per km²"),
            ("green_space", "park/green-space features per km²"),
        ]:
            count = osm_counts.get(criterion_key) if osm_counts else None
            per_km2 = (count / land_area) if (count is not None and land_area) else None
            place["criteria"][criterion_key] = {
                "value": per_km2,
                "raw_count": count,
                "unit": unit_label,
                "as_of": "current OpenStreetMap data",
                "source": "OpenStreetMap (Overpass API)",
                "note": None,
            }

        places[m.id] = place

    _set(db, "places", places)
    _set(db, "meta", {"computed_at": datetime.now(timezone.utc).isoformat()})

    return summary
