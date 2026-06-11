"""Vær-connector.

Standard kilde: Open-Meteo (gratis, ingen nøkkel). Den har to API-er:
  - arkiv (ERA5): mange år tilbake, men ~5 dagers forsinkelse på de nyeste dagene
  - forecast: de siste dagene + noen dager fram

Denne connectoren bruker arkivet for historikk og forecast for de ferske dagene,
og slår dem sammen – så du slipper å tenke på skillet.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import httpx

from .base import WeatherObservation

_DAILY_VARS = "precipitation_sum,temperature_2m_max,temperature_2m_min"
# Arkivet henger ~5 dager etter. Ferskere enn dette hentes fra forecast-API-et.
_ARCHIVE_LAG_DAYS = 5


class WeatherConnector:
    name: str = "base"
    cadence_days: int = 1  # vær kan hentes daglig

    def fetch(self, lat: float, lon: float, start: date, end: date) -> list[WeatherObservation]:
        raise NotImplementedError


class OpenMeteoWeather(WeatherConnector):
    name = "open_meteo"
    cadence_days = 1

    ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
    FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, timeout_s: float = 30.0):
        self._timeout = timeout_s

    def fetch(self, lat: float, lon: float, start: date, end: date) -> list[WeatherObservation]:
        today = date.today()
        archive_cutoff = today - timedelta(days=_ARCHIVE_LAG_DAYS)

        by_date: dict[date, WeatherObservation] = {}
        with httpx.Client(timeout=self._timeout) as client:
            # Historikk fra arkivet.
            if start <= archive_cutoff:
                for obs in self._archive(client, lat, lon, start, min(end, archive_cutoff)):
                    by_date[obs.date] = obs
            # Ferske dager fra forecast-API-et.
            if end > archive_cutoff:
                recent_start = max(start, archive_cutoff + timedelta(days=1))
                for obs in self._forecast(client, lat, lon, recent_start, end, today):
                    by_date[obs.date] = obs

        return [by_date[d] for d in sorted(by_date)]

    def _archive(self, client, lat, lon, start, end) -> list[WeatherObservation]:
        params = {
            "latitude": lat, "longitude": lon,
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "daily": _DAILY_VARS, "timezone": "auto",
        }
        return self._parse(client.get(self.ARCHIVE_URL, params=params))

    def _forecast(self, client, lat, lon, recent_start, end, today) -> list[WeatherObservation]:
        past_days = max(1, min(92, (today - recent_start).days + 1))
        forecast_days = max(1, min(16, (end - today).days + 1)) if end >= today else 1
        params = {
            "latitude": lat, "longitude": lon,
            "daily": _DAILY_VARS, "timezone": "auto",
            "past_days": past_days, "forecast_days": forecast_days,
        }
        all_obs = self._parse(client.get(self.FORECAST_URL, params=params))
        return [o for o in all_obs if recent_start <= o.date <= end]

    @staticmethod
    def _parse(response: httpx.Response) -> list[WeatherObservation]:
        # Open-Meteo struper av og til (429) – særlig når flere systemer på
        # samme maskin henter samtidig. Prøv igjen med økende ventetid.
        if response.status_code == 429:
            url = response.request.url
            with httpx.Client(timeout=30.0) as retry_client:
                for attempt in range(1, 5):
                    time.sleep(15 * attempt)
                    response = retry_client.get(url)
                    if response.status_code != 429:
                        break
        response.raise_for_status()
        daily = response.json()["daily"]
        out: list[WeatherObservation] = []
        for i, day in enumerate(daily["time"]):
            out.append(WeatherObservation(
                date=date.fromisoformat(day),
                precip_mm=daily["precipitation_sum"][i] or 0.0,
                temp_max_c=daily["temperature_2m_max"][i],
                temp_min_c=daily["temperature_2m_min"][i],
            ))
        return out


WEATHER_CONNECTORS: dict[str, WeatherConnector] = {
    "open_meteo": OpenMeteoWeather(),
}


def get_weather_connector(name: str) -> WeatherConnector:
    if name not in WEATHER_CONNECTORS:
        raise KeyError(f"Ukjent vær-kilde '{name}'. Finnes: {list(WEATHER_CONNECTORS)}")
    return WEATHER_CONNECTORS[name]
