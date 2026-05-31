"""Felles byggeklosser for alle connectorer.

En "connector" henter data for ett punkt over et tidsrom og returnerer det
i et STANDARDISERT format. Da kan resten av appen være likegyldig til hvilken
kilde dataene faktisk kom fra – og kilden kan byttes ut uten at noe annet endres.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class NdviObservation:
    """Én NDVI-måling for ett punkt på én dato. NDVI går fra -1 til 1."""
    date: date
    value: float


@dataclass(frozen=True)
class WeatherObservation:
    """Værmåling for ett punkt på én dato."""
    date: date
    precip_mm: float          # nedbør (mm)
    temp_max_c: float | None  # maks-temperatur (°C)
    temp_min_c: float | None  # min-temperatur (°C)
