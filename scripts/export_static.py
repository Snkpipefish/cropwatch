"""Lager en statisk versjon av CropWatch som kan publiseres på GitHub Pages.

Hva skriptet gjør:
  1. Henter ferske data for alle regioner (NDVI + vær).
  2. Skriver nøyaktig de samme JSON-svarene som det levende API-et gir, men som
     vanlige filer under docs/data/.
  3. Kopierer dashbordet (frontend) inn i docs/.

GitHub Pages serverer så docs/-mappen som en helt vanlig nettside – ingen server,
ingen database, ingenting å drifte.

Kjør lokalt med:   python scripts/export_static.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import text                   # noqa: E402

from app import service                      # noqa: E402
from app.config_loader import load_regions   # noqa: E402
from app.storage.db import _engine           # noqa: E402

DOCS = ROOT / "docs"
DATA = DOCS / "data"
FRONTEND = ROOT / "frontend"


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  skrev {path.relative_to(ROOT)}")


# Hvor gamle data kan være før vi roper varsku. NDVI kommer ~hver 8. dag
# (Terra+Aqua flettet) pluss noen dagers prosessering hos NASA; vær kommer daglig.
STALE_NDVI_DAYS = 20
STALE_WEATHER_DAYS = 4


def _report_freshness() -> None:
    """Skriv siste dato per område, og flagg det som henger etter.

    `::warning::`-linjene dukker opp som gule annoteringer på kjøringen i
    GitHub Actions – da synes det at en kilde har stoppet, selv om jobben
    ellers er grønn.
    """
    print("Ferskhet (siste dato per område):")
    today = date.today()
    from sqlalchemy import text as _text
    for region_id in load_regions():
        with _engine(region_id).connect() as c:
            for table, limit in (("ndvi", STALE_NDVI_DAYS), ("weather", STALE_WEATHER_DAYS)):
                rows = c.execute(_text(
                    f"SELECT area_id, MAX(date) FROM {table} GROUP BY area_id")).fetchall()
                for area_id, last in rows:
                    age = (today - date.fromisoformat(last)).days if last else None
                    mark = ""
                    if age is None or age > limit:
                        mark = "  <-- HENGER ETTER"
                        if os.environ.get("GITHUB_ACTIONS"):
                            print(f"::warning::{region_id}/{area_id}: siste "
                                  f"{table}-dato er {last} ({age} dager gammel)")
                    print(f"  {region_id:16s} {area_id:16s} {table:7s} {last}  "
                          f"({age} dager){mark}")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    regions = load_regions()
    print(f"Fant {len(regions)} region(er): {', '.join(regions)}")

    # 1) Hent ferske data for hver region. Databasen følger med i repoet, så
    #    dette henter bare det nye siden sist – ikke hele historikken på nytt.
    #    En region som svikter (f.eks. nettverksblipp) stopper ikke de andre –
    #    da brukes den eksisterende historikken, og siden oppdateres likevel.
    for region_id in regions:
        print(f"Henter data for {region_id} ...")
        try:
            counts = service.refresh_region(region_id)
            print(f"  hentet {counts}")
        except Exception as e:
            print(f"  ADVARSEL: henting feilet for {region_id}: {e}")
        # Skriv WAL inn i hovedfila så den committede databasen er komplett.
        with _engine(region_id).connect() as c:
            c.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            c.commit()

    # 2) Skriv regionliste + status per region som statiske JSON-filer.
    print("Skriver JSON ...")
    _write(DATA / "regions.json", service.list_regions())
    for region_id in regions:
        _write(DATA / f"{region_id}.json", service.region_status(region_id))

    # 3) Kopier dashbordet inn i docs/. (sugar11-dataene bygges av det lokale
    #    scripts/export_sugar11.py – her kopieres bare frontend-filene.)
    print("Kopierer frontend ...")
    for name in ("index.html", "app.js", "sugar11.html", "sugar11.js"):
        if (FRONTEND / name).exists():
            shutil.copy2(FRONTEND / name, DOCS / name)
            print(f"  kopierte {name}")

    # Liten fil som forteller når siden sist ble bygget.
    _write(DATA / "built.json", {"built_utc": date.today().isoformat()})

    _report_freshness()
    print("Ferdig. docs/ er klar for GitHub Pages.")


if __name__ == "__main__":
    main()
