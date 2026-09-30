"""Tjeneste-laget: limet mellom config, connectorer, lagring og indikatorer.

Både API-et og scheduleren bruker funksjonene her, så logikken finnes ett sted.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .config_loader import Region, get_region, load_regions
from .connectors import enso
from .connectors.ndvi import get_ndvi_connector
from .connectors.weather import get_weather_connector
from .indicators import compute
from .storage import db

log = logging.getLogger("cropwatch.service")

# ENSO er global og endrer seg sakte – hentes én gang per prosess.
_enso_cache: dict | None = None


def _get_enso() -> dict:
    global _enso_cache
    if _enso_cache is None:
        try:
            _enso_cache = enso.fetch_oni(months_back=36)
        except Exception:
            _enso_cache = {"series": [], "latest_oni": None, "state": "Ukjent",
                           "strength": "", "trend": "stabil"}
    return _enso_cache

# Hvor mange år historikk vi henter første gang (for et solid normalgrunnlag).
HISTORY_YEARS = 8
# Når vi oppdaterer, henter vi litt på nytt bakover for å fange sene rettelser.
REFRESH_OVERLAP_DAYS = 40


def _start_date(region_id: str, area_id: str, existing_dates: list[date]) -> date:
    if existing_dates:
        return max(existing_dates) - timedelta(days=REFRESH_OVERLAP_DAYS)
    return date.today() - timedelta(days=365 * HISTORY_YEARS)


# Den eneste NDVI-kilden som fantes før meta-tabellen kom. Databaser uten
# "ndvi_source" har derfor MODIS-historikk.
_LEGACY_NDVI_SOURCE = "nasa_modis@1"


def _fetch_ndvi_points(region: Region, connector, points, start: date):
    """Henter NDVI for alle punktene – og tåler at kilden jobber asynkront.

    Kilder med `submit`/`collect` (AppEEARS) får oppgave-id-en lagret i
    databasen. Rakk ikke oppgaven å bli ferdig, returneres None, og neste
    kjøring fortsetter å vente på den samme oppgaven i stedet for å starte
    forfra. Andre kilder hentes rett fram med `fetch_many`.
    """
    if not (hasattr(connector, "submit") and hasattr(connector, "collect")):
        return connector.fetch_many(points, start, date.today())

    pending = db.get_meta(region.id, "ndvi_pending_task") or ""
    task_id, _, pending_key = pending.partition("|")
    if task_id and pending_key == connector.history_key:
        log.info("Fortsetter å vente på AppEEARS-oppgave %s for %s", task_id, region.id)
        try:
            results = connector.collect(task_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Ventende oppgave %s kunne ikke hentes (%s) – sender inn på nytt",
                        task_id, e)
            results = None
            task_id = ""
        else:
            if results is None:
                return None  # fortsatt i kø – behold id-en
            db.set_meta(region.id, "ndvi_pending_task", "")
            return results

    task_id = connector.submit(points, start, date.today())
    db.set_meta(region.id, "ndvi_pending_task", f"{task_id}|{connector.history_key}")
    results = connector.collect(task_id)
    if results is None:
        return None
    db.set_meta(region.id, "ndvi_pending_task", "")
    return results


def refresh_ndvi(region: Region) -> int:
    connector = get_ndvi_connector(region.sources["ndvi"])
    problem = connector.ready()
    if problem:
        # Ikke registrer kjøringen – da prøver scheduleren igjen neste gang.
        log.warning("Hopper over NDVI for %s: %s", region.id, problem)
        return 0

    # Har regionen byttet satellitt siden sist? Da må hele historikken hentes
    # på nytt fra den nye kilden og erstatte den gamle (ulike sensorer gir
    # litt ulike tall, og normalen må bygges på én kilde).
    stored_source = db.get_meta(region.id, "ndvi_source") or _LEGACY_NDVI_SOURCE
    switching = stored_source != connector.history_key
    if switching:
        log.info("NDVI-kilde for %s byttes %s -> %s: henter %d års historikk på nytt",
                 region.id, stored_source, connector.history_key, HISTORY_YEARS)
        start = date.today() - timedelta(days=365 * HISTORY_YEARS)
    else:
        starts = []
        for area in region.areas:
            existing = [o.date for o in db.get_ndvi(region.id, area.id)]
            starts.append(_start_date(region.id, area.id, existing))
        start = min(starts) if starts else date.today()

    points = [(a.id, a.lat, a.lon) for a in region.areas]
    try:
        results = _fetch_ndvi_points(region, connector, points, start)
    except Exception:
        log.exception("NDVI-henting feilet for %s – beholder eksisterende data", region.id)
        return 0
    if results is None:
        return 0  # oppgaven jobber fortsatt hos NASA – vi prøver igjen neste gang

    total = 0
    complete = True
    for area in region.areas:
        obs = results.get(area.id) or []
        if not obs:
            complete = False
            log.warning("Ingen NDVI-verdier for %s/%s denne gangen", region.id, area.id)
            continue
        if switching:
            total += db.replace_ndvi(region.id, area.id, obs)
        else:
            total += db.save_ndvi(region.id, area.id, obs)
    if switching and complete:
        db.set_meta(region.id, "ndvi_source", connector.history_key)
    db.record_fetch(region.id, "ndvi")
    return total


def refresh_weather(region: Region) -> int:
    connector = get_weather_connector(region.sources["weather"])
    total = 0
    for area in region.areas:
        try:
            existing = [o.date for o in db.get_weather(region.id, area.id)]
            start = _start_date(region.id, area.id, existing)
            obs = connector.fetch(area.lat, area.lon, start, date.today())
            total += db.save_weather(region.id, area.id, obs)
        except Exception:
            log.exception("Vær-henting feilet for %s/%s – hopper over", region.id, area.id)
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
        "enso": _get_enso(),
        "cycle": compute.cycle_position(region.cycle),
        "areas": areas,
        "last_run": {
            "ndvi": _iso(db.get_last_run(region.id, "ndvi")),
            "weather": _iso(db.get_last_run(region.id, "weather")),
        },
    }


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None
