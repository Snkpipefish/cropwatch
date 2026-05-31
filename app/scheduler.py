"""Automatisk oppdatering med riktig frekvens per kilde.

NDVI oppdateres bare hver ~16. dag (det er så ofte satellitten gir ny verdi),
mens vær hentes daglig. I stedet for å gjette tidspunkter kjører vi en lett
sjekk hver dag: for hver region og kilde ser vi når den sist ble hentet, og
henter på nytt bare hvis det har gått lenger enn kildens egen kadens.

Fordelen: vi belaster ikke API-ene unødvendig, og det tåler at appen
startes og stoppes (vi husker sist-hentet i databasen).
"""
from __future__ import annotations

import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from . import service
from .config_loader import load_regions
from .connectors.ndvi import get_ndvi_connector
from .connectors.weather import get_weather_connector
from .storage import db

log = logging.getLogger("cropwatch.scheduler")

_scheduler: BackgroundScheduler | None = None


def _cadence_days(region, source: str) -> int:
    if source == "ndvi":
        return get_ndvi_connector(region.sources["ndvi"]).cadence_days
    return get_weather_connector(region.sources["weather"]).cadence_days


def _has_empty_area(region, source: str) -> bool:
    """Sant hvis et område mangler data – f.eks. et nytt område lagt til i YAMLen."""
    read = db.get_ndvi if source == "ndvi" else db.get_weather
    return any(not read(region.id, a.id) for a in region.areas)


def _is_due(region, source: str) -> bool:
    last = db.get_last_run(region.id, source)
    if last is None:
        return True  # aldri hentet → hent nå
    if _has_empty_area(region, source):
        return True  # nytt område uten data → hent nå, uavhengig av kadens
    return (datetime.utcnow() - last).days >= _cadence_days(region, source)


def run_due_refreshes() -> None:
    """Henter alle kilder som er "forfalt" ut fra sin egen frekvens."""
    for region in load_regions().values():
        for source in ("ndvi", "weather"):
            if not _is_due(region, source):
                continue
            try:
                count = service.refresh_region(region.id, source)
                log.info("Oppdaterte %s/%s: %s", region.id, source, count)
            except Exception:
                log.exception("Feil ved oppdatering av %s/%s", region.id, source)


def start_scheduler() -> BackgroundScheduler:
    """Starter den daglige sjekken. Kjører også én gang med en gang ved oppstart."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(daemon=True)
    # Daglig sjekk; selve frekvens-logikken ligger i run_due_refreshes.
    _scheduler.add_job(
        run_due_refreshes, "interval", hours=24,
        next_run_time=datetime.now(), id="daily_refresh", max_instances=1,
    )
    _scheduler.start()
    log.info("Scheduler startet (daglig sjekk).")
    return _scheduler
