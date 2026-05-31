"""El Niño / La Niña-connector (ENSO-tilstand).

Kilde: NOAA Climate Prediction Center sin ONI-indeks (Oceanic Niño Index) –
gratis, ingen nøkkel. ONI er temperaturavviket i Stillehavet (Niño 3.4) som
avgjør om vi er i El Niño, La Niña eller nøytralt. Det påvirker nedbøren i
mange dyrkingsregioner sterkt.

Tommelfingerregel:
  ONI ≥ +0.5  → El Niño
  ONI ≤ -0.5  → La Niña
  ellers      → nøytral
"""
from __future__ import annotations

from datetime import date

import httpx

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"

# Hver ONI-rad gjelder en 3-måneders sesong; her er midtmåneden.
_SEASON_MONTH = {
    "DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4, "AMJ": 5, "MJJ": 6,
    "JJA": 7, "JAS": 8, "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12,
}


def classify(oni: float) -> tuple[str, str]:
    """Returnerer (tilstand, styrke) på norsk."""
    if oni >= 0.5:
        state = "El Niño"
    elif oni <= -0.5:
        state = "La Niña"
    else:
        return "Nøytral", ""
    m = abs(oni)
    strength = (
        "svak" if m < 1.0 else
        "moderat" if m < 1.5 else
        "sterk" if m < 2.0 else "svært sterk"
    )
    return state, strength


def fetch_oni(months_back: int = 36, timeout_s: float = 20.0) -> dict:
    """Henter ONI-historikk og dagens tilstand."""
    with httpx.Client(timeout=timeout_s) as client:
        r = client.get(ONI_URL)
        r.raise_for_status()
        lines = r.text.strip().splitlines()

    series: list[dict] = []
    for line in lines[1:]:  # hopp over overskriften
        parts = line.split()
        if len(parts) < 4:
            continue
        season, year, _total, anom = parts[0], parts[1], parts[2], parts[3]
        month = _SEASON_MONTH.get(season)
        if month is None:
            continue
        try:
            series.append({"date": date(int(year), month, 1).isoformat(), "oni": float(anom)})
        except ValueError:
            continue

    series = series[-months_back:]
    latest = series[-1] if series else None
    state, strength = classify(latest["oni"]) if latest else ("Ukjent", "")

    # Enkel trend: sammenlign siste mot 3 sesonger tidligere.
    trend = "stabil"
    if len(series) >= 4:
        delta = series[-1]["oni"] - series[-4]["oni"]
        trend = "stigende" if delta > 0.2 else "fallende" if delta < -0.2 else "stabil"

    return {
        "series": series,
        "latest_oni": latest["oni"] if latest else None,
        "state": state,
        "strength": strength,
        "trend": trend,
    }
