"""Indikatorer: generiske beregninger som fungerer for enhver region.

Alt her sammenligner "nå" mot et historisk normalnivå, så brukeren ser om
forholdene er bedre eller verre enn vanlig for årstiden.

Fargekoding:
  grønn = normalt eller bedre, gul = noe svakere enn normalt, rød = klart svakere.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date


def _doy(d: date) -> int:
    """Dag-i-året (1–366). Lar oss sammenligne 'samme årstid' på tvers av år."""
    return d.timetuple().tm_yday


def _status_from_anomaly(anomaly: float | None, yellow: float, red: float) -> str:
    """anomaly = nå minus normalt. yellow/red er negative terskler."""
    if anomaly is None:
        return "unknown"
    if anomaly <= red:
        return "red"
    if anomaly <= yellow:
        return "yellow"
    return "green"


# ---- NDVI (vegetasjonshelse) ----------------------------------------------

def ndvi_with_baseline(observations, yellow: float = -0.05, red: float = -0.12) -> dict:
    """Lager NDVI-tidsserie med historisk normal-linje og avvik per punkt.

    Normalen for en gitt dato = snittet av NDVI på samme årstid (±8 dager)
    i ANDRE år. Vinduet trengs fordi Terra og Aqua leverer på ulike faste
    dager i året – uten det ville en Aqua-dato aldri funnet Terra-historikk
    å sammenligne med, og avviket ble stående tomt.
    """
    window = 8
    points = [(_doy(o.date), o.date.year, o.value) for o in observations]

    series = []
    for o in observations:
        doy, year = _doy(o.date), o.date.year
        others = [v for (d, yr, v) in points
                  if yr != year and min(abs(d - doy), 365 - abs(d - doy)) <= window]
        baseline = round(sum(others) / len(others), 4) if others else None
        anomaly = round(o.value - baseline, 4) if baseline is not None else None
        series.append({
            "date": o.date.isoformat(),
            "value": o.value,
            "baseline": baseline,
            "anomaly": anomaly,
        })

    latest = series[-1] if series else None
    status = _status_from_anomaly(latest["anomaly"], yellow, red) if latest else "unknown"
    return {
        "series": series,
        "latest": latest,
        "status": status,
    }


# ---- Nedbør (akkumulert mot normalt) --------------------------------------

def _cumulative_by_year(weather, value_fn) -> dict[int, list[tuple[int, float]]]:
    """For hvert år: (dag-i-året, akkumulert verdi hittil i året)."""
    per_year: dict[int, list] = defaultdict(list)
    for w in sorted(weather, key=lambda x: x.date):
        per_year[w.date.year].append(w)
    out: dict[int, list[tuple[int, float]]] = {}
    for year, days in per_year.items():
        total = 0.0
        seq = []
        for w in days:
            total += value_fn(w) or 0.0
            seq.append((_doy(w.date), round(total, 2)))
        out[year] = seq
    return out


def rainfall_vs_normal(weather, yellow: float = 0.85, red: float = 0.6) -> dict:
    """Sammenligner årets akkumulerte nedbør mot historisk snitt-akkumulering.

    Tørrere enn normalt = mulig tørkestress. yellow/red er forhold (nå/normalt).
    """
    cum = _cumulative_by_year(weather, lambda w: w.precip_mm)
    if not cum:
        return {"series": [], "latest": None, "status": "unknown"}

    current_year = max(cum)
    # Normal-akkumulering per dag-i-året (snitt over tidligere år).
    baseline_at_doy: dict[int, list[float]] = defaultdict(list)
    for year, seq in cum.items():
        if year == current_year:
            continue
        for doy, val in seq:
            baseline_at_doy[doy].append(val)

    series = []
    for doy, val in cum[current_year]:
        others = baseline_at_doy.get(doy, [])
        baseline = round(sum(others) / len(others), 2) if others else None
        series.append({"doy": doy, "cumulative_mm": val, "baseline_mm": baseline})

    latest = series[-1] if series else None
    status = "unknown"
    ratio = None
    if latest and latest["baseline_mm"]:
        ratio = round(latest["cumulative_mm"] / latest["baseline_mm"], 2)
        status = "red" if ratio < red else "yellow" if ratio < yellow else "green"
    return {"series": series, "latest": latest, "ratio_to_normal": ratio, "status": status}


# ---- Growing degree days ---------------------------------------------------

def growing_degree_days(weather, base_temp_c: float, year: int | None = None) -> dict:
    """Akkumulert varmesum for vekst i et år: sum av maks(0, snitt-temp - basis)."""
    if year is None:
        year = max((w.date.year for w in weather), default=date.today().year)
    total = 0.0
    series = []
    for w in sorted(weather, key=lambda x: x.date):
        if w.date.year != year or w.temp_max_c is None or w.temp_min_c is None:
            continue
        mean_t = (w.temp_max_c + w.temp_min_c) / 2
        total += max(0.0, mean_t - base_temp_c)
        series.append({"date": w.date.isoformat(), "gdd_cumulative": round(total, 1)})
    return {"year": year, "total_gdd": round(total, 1), "series": series}


# ---- Stress-indekser (siste 30 dager) -------------------------------------

def heat_stress(weather, heat_temp_c: float, window_days: int = 30) -> dict:
    """Antall dager med maks-temp over varmestress-grensen, siste 30 dager."""
    recent = sorted(weather, key=lambda x: x.date)[-window_days:]
    hot = [w for w in recent if w.temp_max_c is not None and w.temp_max_c >= heat_temp_c]
    days = len(hot)
    status = "red" if days > 7 else "yellow" if days >= 3 else "green"
    return {"hot_days": days, "window_days": len(recent),
            "threshold_c": heat_temp_c, "status": status}


def cycle_position(phases, today: date | None = None) -> dict:
    """Finner hvor i vekstsyklusen vi er nå.

    `phases` er en liste av objekter med .name, .months (liste av 1-12) og .color.
    Returnerer en 12-måneders tidslinje + hvilken fase i dag faller i.
    """
    today = today or date.today()
    # Måned → fase-oppslag.
    month_phase: dict[int, object] = {}
    for ph in phases:
        for m in ph.months:
            month_phase[m] = ph

    timeline = []
    for m in range(1, 13):
        ph = month_phase.get(m)
        timeline.append({
            "month": m,
            "phase": ph.name if ph else "—",
            "color": ph.color if ph else "#334155",
        })

    current_phase = month_phase.get(today.month)
    current = None
    if current_phase is not None:
        idx = current_phase.months.index(today.month)
        current = {
            "phase": current_phase.name,
            "color": current_phase.color,
            "month_in_phase": idx + 1,
            "phase_length": len(current_phase.months),
        }

    from calendar import monthrange
    days_in_month = monthrange(today.year, today.month)[1]
    today_fraction = ((today.month - 1) + (today.day - 1) / days_in_month) / 12

    return {
        "timeline": timeline,
        "current": current,
        "today_month": today.month,
        "today_fraction": round(today_fraction, 4),
        "phases": [{"name": p.name, "color": p.color} for p in phases],
    }


def drought_stress(weather, window_days: int = 30, dry_threshold_mm: float = 1.0) -> dict:
    """Lengste rekke med tørre dager (nedbør under terskel), siste 30 dager."""
    recent = sorted(weather, key=lambda x: x.date)[-window_days:]
    longest = current = 0
    for w in recent:
        if (w.precip_mm or 0.0) < dry_threshold_mm:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    status = "red" if longest > 20 else "yellow" if longest > 10 else "green"
    return {"longest_dry_streak": longest, "window_days": len(recent), "status": status}
