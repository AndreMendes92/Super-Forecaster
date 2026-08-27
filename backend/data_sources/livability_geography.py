"""
livability_geography.py
-------------------------
The curated list of Metro Vancouver municipalities scored by the
"Best Places to Live" tab, plus the shared Overpass (OpenStreetMap)
helper every OSM-based criterion (walkability, transit, green space —
see livability_osm.py) uses to count features inside one municipality.

Why municipality-level, and why these 22 rows: Metro Vancouver is a
federation of 21 municipalities + 1 electoral area (Metro Vancouver's
own description of itself). This is the coarsest granularity, but it's
the only one where every criterion in this tab has real, free,
consistently-available data across the *whole* region — Vancouver
itself publishes richer neighbourhood-level open data, but Burnaby,
Surrey, Richmond, etc. don't publish anything comparable, so scoring at
neighbourhood level would mean treating most of the region as one giant
blob. See the plan's "Going further" notes for finer-grained Vancouver-
only follow-up.

Two fields carry real uncertainty, flagged rather than guessed away:

- `police_service_match`: the prefix StatCan's Crime Severity Index
  table uses for this municipality's police service (e.g. Maple Ridge
  -> "Ridge Meadows RCMP", since Maple Ridge and Pitt Meadows share one
  RCMP detachment). First real deploy already proved one guess wrong
  and taught a general lesson: "Vancouver Police" (this table's actual
  Vancouver row apparently isn't named that way) failed to match while
  a plain "Burnaby" succeeded — so every entry here now uses the bare
  municipality name unless it's one of the genuinely shared detachments
  (Ridge Meadows, Langley, North Vancouver), where a bare name
  wouldn't work at all. Set to None for the small remainder whose
  policing arrangement wasn't confident enough to guess even that far
  (e.g. Port Coquitlam, White Rock) — those simply show "not available"
  for crime rather than risking a wrong number.
- `osm_area_name`: the `name` tag Nominatim/OpenStreetMap uses for this
  municipality's administrative boundary relation. Best documented
  guess per municipality — if Overpass returns no area for a given
  name, livability_osm.py logs that municipality as failed for the
  affected criteria rather than crashing the whole refresh.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Municipality:
    id: str                              # stable slug, used as the DB/API key
    name: str                            # display name
    osm_area_name: str                   # OSM admin boundary `name` tag to query
    police_service_match: str | None     # StatCan CSI police-service name prefix, or None if unconfirmed
    census_geo_match: str                # StatCan census-table GEO name prefix (municipality name itself — StatCan's census tables name CSDs after the municipality, so this one's confident for all 22)
    shared_police_note: str | None = field(default=None)  # e.g. "shares Ridge Meadows RCMP with Pitt Meadows"


MUNICIPALITIES: list[Municipality] = [
    Municipality("vancouver", "Vancouver", "Vancouver", "Vancouver", "Vancouver"),
    Municipality("burnaby", "Burnaby", "Burnaby", "Burnaby", "Burnaby"),
    Municipality("surrey", "Surrey", "Surrey", "Surrey", "Surrey"),
    Municipality("richmond", "Richmond", "Richmond", "Richmond", "Richmond"),
    Municipality("coquitlam", "Coquitlam", "Coquitlam", "Coquitlam", "Coquitlam"),
    Municipality("delta", "Delta", "Delta", "Delta", "Delta"),
    Municipality("langley_township", "Langley Township", "Township of Langley", "Langley RCMP",
                  "Langley (T)", shared_police_note="shares Langley RCMP with Langley City"),
    Municipality("langley_city", "Langley City", "City of Langley", "Langley RCMP",
                  "Langley (C)", shared_police_note="shares Langley RCMP with Langley Township"),
    Municipality("new_westminster", "New Westminster", "New Westminster", "New Westminster", "New Westminster"),
    Municipality("north_van_district", "North Vancouver (District)", "District of North Vancouver",
                  "North Vancouver RCMP", "North Vancouver (DM)",
                  shared_police_note="shares North Vancouver RCMP with the City of North Vancouver"),
    Municipality("north_van_city", "North Vancouver (City)", "City of North Vancouver",
                  "North Vancouver RCMP", "North Vancouver (CY)",
                  shared_police_note="shares North Vancouver RCMP with the District of North Vancouver"),
    Municipality("west_vancouver", "West Vancouver", "West Vancouver", "West Vancouver", "West Vancouver"),
    Municipality("port_coquitlam", "Port Coquitlam", "Port Coquitlam", None, "Port Coquitlam"),
    Municipality("port_moody", "Port Moody", "Port Moody", "Port Moody", "Port Moody"),
    Municipality("maple_ridge", "Maple Ridge", "Maple Ridge", "Ridge Meadows RCMP", "Maple Ridge",
                  shared_police_note="shares Ridge Meadows RCMP with Pitt Meadows"),
    Municipality("pitt_meadows", "Pitt Meadows", "Pitt Meadows", "Ridge Meadows RCMP", "Pitt Meadows",
                  shared_police_note="shares Ridge Meadows RCMP with Maple Ridge"),
    Municipality("white_rock", "White Rock", "White Rock", None, "White Rock"),
    Municipality("anmore", "Anmore", "Anmore", None, "Anmore"),
    Municipality("belcarra", "Belcarra", "Belcarra", None, "Belcarra"),
    Municipality("bowen_island", "Bowen Island", "Bowen Island", None, "Bowen Island"),
    Municipality("lions_bay", "Lions Bay", "Lions Bay", None, "Lions Bay"),
    Municipality("electoral_area_a", "Electoral Area A", "Electoral Area A", None, "Greater Vancouver (E.A.)"),
]

MUNICIPALITIES_BY_ID = {m.id: m for m in MUNICIPALITIES}
