"""Leser regionkonfigurasjoner fra app/config/regions/*.yaml.

Hele poenget med denne modulen: appen oppdager nye regioner automatisk.
Legg en ny .yaml-fil i mappen, og den dukker opp i appen ved neste oppstart.
Ingen kodeendring nødvendig.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REGIONS_DIR = Path(__file__).parent / "config" / "regions"


@dataclass(frozen=True)
class Area:
    """Ett dyrkingsområde med et senterpunkt vi henter data for."""
    id: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Growing:
    base_temp_c: float
    heat_stress_temp_c: float


@dataclass(frozen=True)
class Phase:
    name: str
    months: list[int]
    color: str


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    commodity: str
    sources: dict[str, str]
    growing: Growing
    cycle: list[Phase] = field(default_factory=list)
    areas: list[Area] = field(default_factory=list)

    def area(self, area_id: str) -> Area | None:
        return next((a for a in self.areas if a.id == area_id), None)


def _parse(path: Path) -> Region:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    required = ["id", "name", "commodity", "sources", "growing", "areas"]
    missing = [k for k in required if k not in raw]
    if missing:
        raise ValueError(f"{path.name} mangler felt: {', '.join(missing)}")

    growing = Growing(
        base_temp_c=float(raw["growing"]["base_temp_c"]),
        heat_stress_temp_c=float(raw["growing"]["heat_stress_temp_c"]),
    )
    areas = [
        Area(id=a["id"], name=a["name"], lat=float(a["lat"]), lon=float(a["lon"]))
        for a in raw["areas"]
    ]
    cycle = [
        Phase(name=p["name"], months=[int(m) for m in p["months"]], color=p.get("color", "#64748b"))
        for p in raw.get("cycle", {}).get("phases", [])
    ]
    return Region(
        id=raw["id"],
        name=raw["name"],
        commodity=raw["commodity"],
        sources=dict(raw["sources"]),
        growing=growing,
        cycle=cycle,
        areas=areas,
    )


def load_regions() -> dict[str, Region]:
    """Returnerer alle regioner, nøklet på region-id. Oppdager filer automatisk."""
    regions: dict[str, Region] = {}
    for path in sorted(REGIONS_DIR.glob("*.yaml")):
        region = _parse(path)
        if region.id in regions:
            raise ValueError(f"Duplikat region-id '{region.id}' i {path.name}")
        regions[region.id] = region
    return regions


def get_region(region_id: str) -> Region:
    regions = load_regions()
    if region_id not in regions:
        raise KeyError(f"Ukjent region '{region_id}'. Finnes: {list(regions)}")
    return regions[region_id]
