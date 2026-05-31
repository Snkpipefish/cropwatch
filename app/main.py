"""FastAPI-appen: leverer både JSON-API og dashbordet.

Start lokalt med:
    .venv/bin/uvicorn app.main:app --reload
Åpne så http://localhost:8000 i nettleseren.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from . import service
from .scheduler import start_scheduler

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app = FastAPI(title="CropWatch", description="Avlingsovervåking: NDVI + vær mot normalt")


@app.on_event("startup")
def _startup() -> None:
    start_scheduler()


@app.get("/api/regions")
def api_regions():
    """Alle regioner (lest fra config) – mater dropdown og kart i frontend."""
    return service.list_regions()


@app.get("/api/regions/{region_id}/status")
def api_region_status(region_id: str):
    """Indikatorer + tidsserier for alle områder i en region."""
    try:
        return service.region_status(region_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/api/regions/{region_id}/refresh")
def api_refresh(region_id: str, source: str | None = None):
    """Henter ferske data nå (NDVI og/eller vær). Mest for testing/manuell kjøring."""
    try:
        return service.refresh_region(region_id, source)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


# Serverer dashbordet (index.html, app.js) fra rot. API-rutene over matcher
# først, så dette fanger bare frontend-filene.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
