"""Sky-oppdatering av Sukker no. 11-dashbordet (kjøres av GitHub Actions).

Oppdaterer panelene som har åpne, nøkkelfrie kilder – flere ganger om dagen,
uten at PC-en må være på:

  - pris (Yahoo Finance, SB=F)     - frost-vakt (Open-Meteo, ferskt Brasil-vær)
  - USD/BRL (Yahoo Finance, BRL=X) - plantehelse/NDVI (CropWatch-databasene)
  - ENSO/ONI (NOAA)                - sesong (ren kalenderberegning)

Panelene som trenger bedrock (signal/gulv, UNICA, etanol, India, COT) beholdes
UENDRET fra forrige publiserte sugar11.json – de oppdateres av PC-jobben
(scripts/export_sugar11.py) når den kjører. Hvert panel viser sin egen dato,
så alderen er alltid synlig.

Svikter en fersk kilde, beholdes forrige verdi i stedet for å vise feil.

Kjør:  .venv/bin/python scripts/export_sugar11_cloud.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from export_sugar11 import (  # noqa: E402
    DOCS, enso, frost, ndvi, percentile_of_last, pressures, season,
)

SUGAR11_JSON = DOCS / "data" / "sugar11.json"

# Seksjoner PC-en eier (leser bedrock lokalt) – bæres videre som de er.
CARRY_SECTIONS = ("ethanol", "unica", "india", "cot", "bedrock")


def _yahoo_closes(symbol: str, range_: str) -> list[tuple[str, float]]:
    """Daglige sluttkurser [(dato, kurs), ...] fra Yahoo Finance (nøkkelfritt).
    Siste punkt er dagens kurs så langt hvis markedet er åpent."""
    r = httpx.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": range_, "interval": "1d"},
        headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    return [
        (datetime.fromtimestamp(t, timezone.utc).date().isoformat(), c)
        for t, c in zip(res["timestamp"], closes) if c is not None
    ]


def price():
    rows = _yahoo_closes("SB=F", "1y")
    closes = [v for _, v in rows]
    last = closes[-1]
    chg5 = round(100 * (last / closes[-6] - 1), 2) if len(closes) > 6 else None
    return {
        "asof": rows[-1][0],
        "close": round(last, 2),
        "chg_5d_pct": chg5,
        "pct_1y": percentile_of_last(closes[-252:]),
        "spark": [round(v, 2) for v in closes[-90:]],
    }


def brl():
    rows = _yahoo_closes("BRL=X", "3y")
    vals = [v for _, v in rows]
    last = vals[-1]
    chg5 = round(100 * (last / vals[-6] - 1), 2) if len(vals) > 6 else None
    return {
        "asof": rows[-1][0],
        "source": "Yahoo",
        "usdbrl": round(last, 4),
        "chg_5d_pct": chg5,
        "pct_3y": percentile_of_last(vals[-756:]),
    }


def main() -> None:
    prev = json.loads(SUGAR11_JSON.read_text()) if SUGAR11_JSON.exists() else {}

    # Frost-vakten trenger dagens minimumstemperatur – hent ferskt Brasil-vær.
    try:
        from app import service as cw_service
        from app.config_loader import get_region
        cw_service.refresh_weather(get_region("brazil_sugar"))
        print("Hentet ferskt Brasil-vær for frost-vakten.")
    except Exception as e:  # noqa: BLE001
        print(f"  ADVARSEL: vær-oppdatering feilet ({e}) – bruker lagrede data.")

    out = {
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "built_by": "cloud",
    }
    for name, fn in (("price", price), ("brl", brl), ("enso", enso),
                     ("ndvi", ndvi), ("frost", frost), ("season", season)):
        try:
            out[name] = fn()
            print(f"  {name}: ferskt ({out[name].get('asof')})")
        except Exception as e:  # noqa: BLE001
            out[name] = prev.get(name, {"error": str(e)[:160]})
            print(f"  {name}: kilde sviktet ({e}) – beholder forrige verdi")

    for name in CARRY_SECTIONS:
        out[name] = prev.get(name, {"error": "venter på PC-oppdatering"})
        print(f"  {name}: beholdt fra forrige bygg ({out[name].get('asof')})")

    out["pressure"] = pressures(out)

    SUGAR11_JSON.parent.mkdir(parents=True, exist_ok=True)
    SUGAR11_JSON.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Skrev docs/data/sugar11.json")


if __name__ == "__main__":
    main()
