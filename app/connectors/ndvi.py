"""NDVI-connector (vegetasjonshelse).

Standard kilde: NASA MODIS via ORNL DAAC sin gratis REST-tjeneste (ingen nøkkel).
Produkt MOD13Q1 = 250 m oppløsning, ny verdi hver 16. dag.

BYTTE KILDE SENERE:
  Vil du bruke Agromonitoring eller NASA Earthdata i stedet, lag en ny klasse
  som arver fra `NdviConnector`, implementer `fetch(...)`, og registrer den i
  `NDVI_CONNECTORS` nederst. Sett så `sources.ndvi` i region-YAMLen til navnet.
  Resten av appen trenger ingen endring.
"""
from __future__ import annotations

import time
from datetime import date, datetime

import httpx


def _get_with_retry(client: httpx.Client, url: str, params: dict, attempts: int = 4):
    """GET med noen gjenforsøk – MODIS-tjenesten har av og til korte blipp."""
    last_error: Exception | None = None
    for i in range(attempts):
        try:
            r = client.get(url, params=params, headers={"Accept": "application/json"})
            r.raise_for_status()
            return r
        except (httpx.HTTPError, httpx.TransportError) as e:
            last_error = e
            time.sleep(2 * (i + 1))  # vent litt lenger for hvert forsøk
    raise last_error

from .base import NdviObservation


class NdviConnector:
    """Grensesnitt som alle NDVI-kilder må følge."""

    name: str = "base"
    # Typisk hvor ofte kilden gir en ny verdi (brukes av scheduleren).
    cadence_days: int = 16

    def fetch(self, lat: float, lon: float, start: date, end: date) -> list[NdviObservation]:
        raise NotImplementedError


class NasaModisNdvi(NdviConnector):
    name = "nasa_modis"
    cadence_days = 16

    BASE_URL = "https://modis.ornl.gov/rst/api/v1"
    PRODUCT = "MOD13Q1"
    BAND = "250m_16_days_NDVI"
    SCALE = 0.0001
    # MODIS bruker fyll-verdien -3000 der data mangler (skyer o.l.).
    FILL_BELOW = -2000
    # Tjenesten tillater maks 10 datoer per forespørsel.
    MAX_DATES_PER_REQUEST = 10

    def __init__(self, timeout_s: float = 60.0, polite_delay_s: float = 0.4):
        self._timeout = timeout_s
        self._delay = polite_delay_s

    def _available_dates(self, client: httpx.Client, lat: float, lon: float) -> list[dict]:
        """Henter alle tilgjengelige MODIS-datoer for punktet."""
        r = _get_with_retry(
            client,
            f"{self.BASE_URL}/{self.PRODUCT}/dates",
            {"latitude": lat, "longitude": lon},
        )
        return r.json().get("dates", [])

    def fetch(self, lat: float, lon: float, start: date, end: date) -> list[NdviObservation]:
        observations: list[NdviObservation] = []
        with httpx.Client(timeout=self._timeout) as client:
            dates = self._available_dates(client, lat, lon)

            # Behold kun datoer innenfor det forespurte tidsrommet.
            wanted = [
                d for d in dates
                if start <= datetime.strptime(d["calendar_date"], "%Y-%m-%d").date() <= end
            ]
            modis_dates = [d["modis_date"] for d in wanted]

            # Del opp i biter på maks 10 (tjenestens grense) og hent hver bit.
            for i in range(0, len(modis_dates), self.MAX_DATES_PER_REQUEST):
                chunk = modis_dates[i:i + self.MAX_DATES_PER_REQUEST]
                observations.extend(self._fetch_chunk(client, lat, lon, chunk[0], chunk[-1]))
                if self._delay:
                    time.sleep(self._delay)

        observations.sort(key=lambda o: o.date)
        return observations

    def _fetch_chunk(
        self, client: httpx.Client, lat: float, lon: float, start_modis: str, end_modis: str
    ) -> list[NdviObservation]:
        r = _get_with_retry(
            client,
            f"{self.BASE_URL}/{self.PRODUCT}/subset",
            {
                "latitude": lat,
                "longitude": lon,
                "band": self.BAND,
                "startDate": start_modis,
                "endDate": end_modis,
                "kmAboveBelow": 0,
                "kmLeftRight": 0,
            },
        )
        payload = r.json()

        out: list[NdviObservation] = []
        for row in payload.get("subset", []):
            raw = row["data"][0]
            if raw is None or raw < self.FILL_BELOW:
                continue  # mangler ekte data (skyer e.l.) – hopp over
            obs_date = datetime.strptime(row["calendar_date"], "%Y-%m-%d").date()
            out.append(NdviObservation(date=obs_date, value=round(raw * self.SCALE, 4)))
        return out


# Registret som kobler navn (fra YAML) til en faktisk connector.
NDVI_CONNECTORS: dict[str, NdviConnector] = {
    "nasa_modis": NasaModisNdvi(),
}


def get_ndvi_connector(name: str) -> NdviConnector:
    if name not in NDVI_CONNECTORS:
        raise KeyError(f"Ukjent NDVI-kilde '{name}'. Finnes: {list(NDVI_CONNECTORS)}")
    return NDVI_CONNECTORS[name]
