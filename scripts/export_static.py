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
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import service                      # noqa: E402
from app.config_loader import load_regions   # noqa: E402

DOCS = ROOT / "docs"
DATA = DOCS / "data"
FRONTEND = ROOT / "frontend"


def _write(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  skrev {path.relative_to(ROOT)}")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    regions = load_regions()
    print(f"Fant {len(regions)} region(er): {', '.join(regions)}")

    # 1) Hent ferske data for hver region.
    for region_id in regions:
        print(f"Henter data for {region_id} ...")
        counts = service.refresh_region(region_id)
        print(f"  hentet {counts}")

    # 2) Skriv regionliste + status per region som statiske JSON-filer.
    print("Skriver JSON ...")
    _write(DATA / "regions.json", service.list_regions())
    for region_id in regions:
        _write(DATA / f"{region_id}.json", service.region_status(region_id))

    # 3) Kopier dashbordet inn i docs/.
    print("Kopierer frontend ...")
    for name in ("index.html", "app.js"):
        shutil.copy2(FRONTEND / name, DOCS / name)
        print(f"  kopierte {name}")

    # Liten fil som forteller når siden sist ble bygget.
    _write(DATA / "built.json", {"built_utc": date.today().isoformat()})
    print("Ferdig. docs/ er klar for GitHub Pages.")


if __name__ == "__main__":
    main()
