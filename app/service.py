"""Tjeneste-laget: limet mellom config, connectorer, lagring og indikatorer.

Både API-et og scheduleren bruker funksjonene her, så logikken finnes ett sted.
"""
from __future__ import annotations

from datetime import date, timedelta

from .config_loader import Region, get_region, load_regions
from .connectors.ndvi import get_ndvi_connector
from .connectors.weather import get_weather_connector
from .indicators import compute
from .storage import db

# Hvor mange år historikk vi henter første gang (for et solid normalgrunnlag).
HISTORY_YEARS = 8
# Når vi oppdaterer, henter vi litt på nytt bakover for å fange sene rettelser.
REFRESH_OVERLAP_DAYS = 40


def _start_date(region_id: str, area_id: str, existing_dates: list[date]) -> date:
    if existing_dates:
        return max(existing_dates) - timedelta(days=REFRESH_OVERLAP_DAYS)
    return date.today() - timedelta(days=365 * HISTORY_YEARS)


def refresh_ndvi(region: Region) -> int:
    connector = get_ndvi_connector(region.sources["ndvi"])
    total = 0
    for area in region.areas:
        existing = [o.date for o in db.get_ndvi(region.id, area.id)]
        start = _start_date(region.id, area.id, existing)
        obs = connector.fetch(area.lat, area.lon, start, date.today())
        total += db.save_ndvi(region.id, area.id, obs)
    db.record_fetch(region.id, "ndvi")
    return total


def refresh_weather(region: Region) -> int:
    connector = get_weather_connector(region.sources["weather"])
    total = 0
    for area in region.areas:
        existing = [o.date for o in db.get_weather(region.id, area.id)]
        start = _start_date(region.id, area.id, existing)
        obs = connector.fetch(area.lat, area.lon, start, date.today())
        total += db.save_weather(region.id, area.id, obs)
    db.record_fetch(region.id, "weather")
    return total


def refresh_region(region_id: str, source: str | None = None) -> dict:
    region = get_region(region_id)
    result = {}
    if source in (None, "ndvi"):
        result["ndvi"] = refresh_ndvi(region)
    if source in (None, "weather"):
        result["weather"] = refresh_weather(region)
    return result


# ---- Lesing for dashbordet -------------------------------------------------

def list_regions() -> list[dict]:
    out = []
    for region in load_regions().values():
        out.append({
            "id": region.id,
            "name": region.name,
            "commodity": region.commodity,
            "areas": [
                {"id": a.id, "name": a.name, "lat": a.lat, "lon": a.lon}
                for a in region.areas
            ],
        })
    return out


def area_status(region: Region, area_id: str) -> dict:
    ndvi = db.get_ndvi(region.id, area_id)
    weather = db.get_weather(region.id, area_id)

    ndvi_res = compute.ndvi_with_baseline(ndvi)
    rain_res = compute.rainfall_vs_normal(weather)
    gdd_res = compute.growing_degree_days(weather, region.growing.base_temp_c)
    heat_res = compute.heat_stress(weather, region.growing.heat_stress_temp_c)
    drought_res = compute.drought_stress(weather)

    return {
        "area_id": area_id,
        "ndvi": ndvi_res,
        "rainfall": rain_res,
        "gdd": gdd_res,
        "heat_stress": heat_res,
        "drought_stress": drought_res,
    }


def region_status(region_id: str) -> dict:
    region = get_region(region_id)
    areas = {a.id: area_status(region, a.id) for a in region.areas}
    return {
        "region": {"id": region.id, "name": region.name, "commodity": region.commodity},
        "areas": areas,
        "last_run": {
            "ndvi": _iso(db.get_last_run(region.id, "ndvi")),
            "weather": _iso(db.get_last_run(region.id, "weather")),
        },
    }


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None
