"""Lagring: én SQLite-database per region (data/<region_id>.db).

Hver region får sin egen fil, så regionene er helt adskilte – å legge til en
ny region rører aldri en annens data.

Lagringen er "idempotent": henter du samme dato to ganger, oppdateres raden i
stedet for å lage en duplikat (takket være sammensatt nøkkel område+dato).
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Date, DateTime, Float, String, create_engine, event, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

DATA_DIR = Path(__file__).parent.parent.parent / "data"


class Base(DeclarativeBase):
    pass


class NdviObs(Base):
    __tablename__ = "ndvi"
    area_id: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    value: Mapped[float] = mapped_column(Float)


class WeatherObs(Base):
    __tablename__ = "weather"
    area_id: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    precip_mm: Mapped[float] = mapped_column(Float)
    temp_max_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_min_c: Mapped[float | None] = mapped_column(Float, nullable=True)


class FetchLog(Base):
    """Når ble hver kilde sist hentet? Brukes av scheduleren."""
    __tablename__ = "fetch_log"
    source: Mapped[str] = mapped_column(String, primary_key=True)  # "ndvi" / "weather"
    last_run_at: Mapped[datetime] = mapped_column(DateTime)


_engines: dict[str, object] = {}


def _engine(region_id: str):
    if region_id not in _engines:
        DATA_DIR.mkdir(exist_ok=True)
        eng = create_engine(f"sqlite:///{DATA_DIR / f'{region_id}.db'}")

        # Vent (i stedet for å feile umiddelbart) hvis databasen er låst av en
        # annen skriver, og bruk WAL-modus som tåler lesing under skriving.
        @event.listens_for(eng, "connect")
        def _set_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        Base.metadata.create_all(eng)
        _engines[region_id] = eng
    return _engines[region_id]


def session(region_id: str) -> Session:
    return Session(_engine(region_id))


# ---- Skrive ----------------------------------------------------------------

def save_ndvi(region_id: str, area_id: str, observations) -> int:
    # Dedupliser på dato (siste vinner) før vi skriver.
    rows = {
        o.date: {"area_id": area_id, "date": o.date, "value": o.value}
        for o in observations
    }
    if not rows:
        return 0
    stmt = sqlite_insert(NdviObs).values(list(rows.values()))
    stmt = stmt.on_conflict_do_update(
        index_elements=["area_id", "date"],
        set_={"value": stmt.excluded.value},
    )
    with session(region_id) as s:
        s.execute(stmt)
        s.commit()
    return len(rows)


def save_weather(region_id: str, area_id: str, observations) -> int:
    rows = {
        o.date: {
            "area_id": area_id, "date": o.date, "precip_mm": o.precip_mm,
            "temp_max_c": o.temp_max_c, "temp_min_c": o.temp_min_c,
        }
        for o in observations
    }
    if not rows:
        return 0
    stmt = sqlite_insert(WeatherObs).values(list(rows.values()))
    stmt = stmt.on_conflict_do_update(
        index_elements=["area_id", "date"],
        set_={
            "precip_mm": stmt.excluded.precip_mm,
            "temp_max_c": stmt.excluded.temp_max_c,
            "temp_min_c": stmt.excluded.temp_min_c,
        },
    )
    with session(region_id) as s:
        s.execute(stmt)
        s.commit()
    return len(rows)


def record_fetch(region_id: str, source: str) -> None:
    with session(region_id) as s:
        s.merge(FetchLog(source=source, last_run_at=datetime.utcnow()))
        s.commit()


# ---- Lese ------------------------------------------------------------------

def get_ndvi(region_id: str, area_id: str) -> list[NdviObs]:
    with session(region_id) as s:
        return list(s.scalars(
            select(NdviObs).where(NdviObs.area_id == area_id).order_by(NdviObs.date)
        ))


def get_weather(region_id: str, area_id: str) -> list[WeatherObs]:
    with session(region_id) as s:
        return list(s.scalars(
            select(WeatherObs).where(WeatherObs.area_id == area_id).order_by(WeatherObs.date)
        ))


def get_last_run(region_id: str, source: str) -> datetime | None:
    with session(region_id) as s:
        row = s.get(FetchLog, source)
        return row.last_run_at if row else None
