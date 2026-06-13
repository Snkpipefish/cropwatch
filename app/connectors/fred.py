"""FRED-connector (St. Louis Fed økonomiske tidsserier).

Henter f.eks. BRL/USD (DEXBZUS) og Brent (DCOILBRENTEU) direkte fra FRED, slik
at vi får ferske tall i stedet for å vente på at en annen kopi blir oppdatert.

NØKKEL-HÅNDTERING (viktig):
  - Nøkkelen leses ved kjøretid fra env-var FRED_API_KEY, ellers fra
    ~/.bedrock/secrets.env (samme sted som bedrock). Den hardkodes ALDRI.
  - FRED legger nøkkelen i URL-en. httpx-feil inneholder hele URL-en, så en
    rå feil ville lekket nøkkelen til logg/JSON. Derfor fanges ALLE nett-feil
    og kastes på nytt som en renset melding UTEN URL (via `from None`).
  - Verdiene vi henter (valutakurser, oljepris) er offentlige data og trygge
    å publisere. Det er kun selve nøkkelen som må holdes lokal.
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx

BASE = "https://api.stlouisfed.org/fred/series/observations"
_SECRETS = Path("~/.bedrock/secrets.env").expanduser()


def get_key() -> str:
    key = os.environ.get("FRED_API_KEY")
    if key:
        return key.strip()
    if _SECRETS.exists():
        for line in _SECRETS.read_text(encoding="utf-8").splitlines():
            if line.startswith("FRED_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError(
        "FRED_API_KEY ikke funnet (env-var eller ~/.bedrock/secrets.env)")


def fetch_series(series_id: str, start: str | None = None,
                 timeout_s: float = 20.0) -> list[tuple[str, float]]:
    """Returnerer [(dato, verdi), ...] sortert stigende. Hopper over hull ('.')."""
    params = {
        "series_id": series_id,
        "api_key": get_key(),
        "file_type": "json",
        "sort_order": "asc",
    }
    if start:
        params["observation_start"] = start
    try:
        with httpx.Client(timeout=timeout_s) as client:
            r = client.get(BASE, params=params)
            r.raise_for_status()
            obs = r.json().get("observations", [])
    except httpx.HTTPError as e:
        # Rens bort URL-en (som inneholder nøkkelen) – kun serie + feiltype.
        raise RuntimeError(f"FRED-henting feilet for {series_id}: {type(e).__name__}") from None

    out: list[tuple[str, float]] = []
    for o in obs:
        v = o.get("value", ".")
        if v not in (".", "", None):
            try:
                out.append((o["date"], float(v)))
            except ValueError:
                continue
    return out
