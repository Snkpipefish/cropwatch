"""Bygger datagrunnlaget for Sukker no. 11-driverdashbordet.

Leser (alt lokalt, read-only):
  - bedrock.db        → pris, BRL, etanol (ANP), UNICA, India, COT, bedrock-score/floor
  - CropWatch data/   → NDVI-anomali (eksportvektet), frost-vakt, vekstsyklus
  - NOAA (nett)       → ENSO/ONI (gjenbruker CropWatch-connectoren)

Skriver docs/data/sugar11.json + kopierer sugar11-frontend til docs/.

MÅ kjøres lokalt på denne PC-en (GitHub Actions har ikke bedrock.db).
Hver seksjon er feiltolerant: svikter én kilde, leveres resten, og panelet
viser «ingen data» med årsak i stedet for å stoppe alt.

Kjør:  .venv/bin/python scripts/export_sugar11.py [--push]
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config_loader import get_region            # noqa: E402
from app.connectors import enso as enso_mod         # noqa: E402
from app.connectors import fred as fred_mod         # noqa: E402
from app.indicators import compute                  # noqa: E402
from app.storage import db as cw_db                 # noqa: E402

BEDROCK_DB = Path(os.environ.get("BEDROCK_DB", "/home/pc/bedrock/bedrock.db"))
FLOOR_JSON = Path(os.environ.get(
    "SUGAR_FLOOR_JSON", "/home/pc/bedrock/data/_meta/sugar_rolling_floor.json"))
AGRI_SIGNALS = Path(os.environ.get(
    "BEDROCK_AGRI_SIGNALS", "/home/pc/bedrock/data/agri_signals.json"))
DOCS = ROOT / "docs"
FRONTEND = ROOT / "frontend"

# Eksport-vekter per land (samme som bedrock sin weather_stress-driver).
COUNTRY_WEIGHTS = {"brazil_sugar": 0.55, "india_sugar": 0.30, "thailand_sugar": 0.15}

# Bedrock sin sesong-mapping for sukker (sugar.yaml monthly_scores, jan→des).
SEASON_SCORES = [1.0, 1.0, 0.9, 0.7, 0.6, 0.7, 0.8, 0.9, 1.0, 0.9, 0.8, 0.9]

FROST_THRESHOLD_C = 3.0
FROST_MONTHS = {6, 7, 8}  # frost-vinduet i Brasil Centro-Sul


def bcon() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{BEDROCK_DB}?mode=ro", uri=True)


def percentile_of_last(values: list[float]) -> float | None:
    """Hvor (0–100) ligger siste verdi i sin egen historikk?"""
    if len(values) < 10:
        return None
    last = values[-1]
    below = sum(1 for v in values[:-1] if v < last)
    return round(100.0 * below / (len(values) - 1), 1)


def section(fn):
    """Kjør én seksjon feiltolerant: returner {'error': ...} ved svikt."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        print(f"  ADVARSEL i {fn.__name__}: {e}")
        return {"error": str(e)[:160]}


# ---------------- bedrock-seksjoner -----------------------------------------

def price():
    with bcon() as c:
        rows = c.execute(
            "SELECT ts, close FROM prices WHERE instrument='Sugar' AND tf='D1' "
            "ORDER BY ts").fetchall()
    closes = [r[1] for r in rows]
    last_ts, last = rows[-1][0][:10], closes[-1]
    yr = closes[-252:]
    chg5 = round(100 * (last / closes[-6] - 1), 2) if len(closes) > 6 else None
    return {
        "asof": last_ts,
        "close": round(last, 2),
        "chg_5d_pct": chg5,
        "pct_1y": percentile_of_last(yr),
        "spark": [round(v, 2) for v in closes[-90:]],
    }


def brl():
    # Hent ferskt fra FRED (DEXBZUS). Faller tilbake til bedrocks kopi hvis
    # FRED er nede eller nøkkelen mangler.
    source = "FRED"
    try:
        rows = fred_mod.fetch_series("DEXBZUS", start="2022-01-01")
        last_date, vals = rows[-1][0], [v for _, v in rows]
    except Exception as e:  # noqa: BLE001
        print(f"  FRED utilgjengelig ({e}) – bruker bedrock-kopi for BRL.")
        source = "bedrock"
        with bcon() as c:
            rows = c.execute(
                "SELECT date, value FROM fundamentals WHERE series_id='DEXBZUS' "
                "AND value IS NOT NULL ORDER BY date").fetchall()
        last_date, vals = rows[-1][0], [r[1] for r in rows]

    last = vals[-1]
    chg5 = round(100 * (last / vals[-6] - 1), 2) if len(vals) > 6 else None
    return {
        "asof": last_date,
        "source": source,           # FRED (ferskt) eller bedrock (reserve)
        "usdbrl": round(last, 4),
        "chg_5d_pct": chg5,          # + = svakere real
        "pct_3y": percentile_of_last(vals[-756:]),
    }


def ethanol():
    """Relativ attraktivitet: sukker (omregnet til BRL) mot hydrous-etanol.

    ratio = (sukkerpris cents/lb × USDBRL) / (etanol BRL/liter).
    Høy ratio i egen historikk = sukker godt betalt relativt → bruk lager
    velge sukker (bear). Lav = etanol frister (bull for sukkerprisen).
    """
    with bcon() as c:
        eth = c.execute(
            "SELECT date, value FROM fundamentals "
            "WHERE series_id='ANP_ETANOL_HIDR_CS_BRL_LITER' "
            "AND value IS NOT NULL ORDER BY date").fetchall()
        sug = dict(c.execute(
            "SELECT substr(ts,1,10), close FROM prices "
            "WHERE instrument='Sugar' AND tf='D1'").fetchall())
        fx_map = dict(c.execute(
            "SELECT date, value FROM fundamentals WHERE series_id='DEXBZUS' "
            "AND value IS NOT NULL ORDER BY date").fetchall())

    # Legg ferske FRED-kurser oppå bedrocks historikk, så de siste ukene ikke
    # bruker en utdatert valutakurs. (Beholder dyp historikk fra bedrock.)
    try:
        fx_map.update(dict(fred_mod.fetch_series("DEXBZUS", start="2022-01-01")))
    except Exception as e:  # noqa: BLE001
        print(f"  FRED-FX utilgjengelig for etanol-paritet ({e}) – bruker bedrock.")

    sug_dates = sorted(sug)
    fx_dates = sorted(fx_map)

    def nearest_before(dates: list[str], d: str) -> str | None:
        cand = [x for x in dates if x <= d]
        return cand[-1] if cand else None

    series = []
    for d, eth_v in eth:
        sd, fd = nearest_before(sug_dates, d), nearest_before(fx_dates, d)
        if sd and fd and eth_v:
            series.append((d, (sug[sd] * fx_map[fd]) / eth_v))
    vals = [v for _, v in series]
    return {
        "asof": eth[-1][0],
        "eth_brl_liter": round(eth[-1][1], 3),
        "ratio_pct_3y": percentile_of_last(vals[-156:]),  # ukentlig serie ≈ 3 år
    }


def unica():
    with bcon() as c:
        cols = [x[1] for x in c.execute("PRAGMA table_info(unica_reports)").fetchall()]
        row = c.execute(
            "SELECT * FROM unica_reports ORDER BY report_date DESC LIMIT 1").fetchone()
    d = dict(zip(cols, row))
    return {
        "asof": d["report_date"],
        "period": d.get("period"),
        "crop_year": d.get("crop_year"),
        "mix_sugar_pct": d.get("mix_sugar_pct"),
        "mix_sugar_pct_prev": d.get("mix_sugar_pct_prev"),
        "sugar_production_yoy_pct": d.get("sugar_production_yoy_pct"),
        "crush_yoy_pct": d.get("crush_yoy_pct"),
    }


def india():
    with bcon() as c:
        rows = c.execute(
            "SELECT date, value FROM fundamentals "
            "WHERE series_id='COMTRADE_INDIA_SUGAR_EXPORTS_KG_MONTHLY' "
            "ORDER BY date").fetchall()
    vals = [r[1] for r in rows]
    yoy = None
    if len(vals) >= 24:
        cur, prev = sum(vals[-12:]), sum(vals[-24:-12])
        if prev:
            yoy = round(100 * (cur / prev - 1), 1)
    return {
        "asof": rows[-1][0],
        "exports_12m_yoy_pct": yoy,   # - = mindre eksport = strammere marked
    }


def cot():
    with bcon() as c:
        rows = c.execute(
            "SELECT report_date, mm_long - mm_short, open_interest "
            "FROM cot_disaggregated "
            "WHERE contract='SUGAR NO. 11 - ICE FUTURES U.S.' "
            "ORDER BY report_date").fetchall()
    nets = [r[1] for r in rows]
    pct = percentile_of_last(nets[-52:])
    return {
        "asof": rows[-1][0],
        "mm_net": rows[-1][1],
        "open_interest": rows[-1][2],
        "pct_52w": pct,
        "extreme": pct is not None and (pct >= 95 or pct <= 5),
    }


def bedrock_view():
    # GJELDENDE setup leses fra bedrocks live-fil data/agri_signals.json (samme
    # som web-UI-et og boten bruker) — IKKE signal_setups, som er backtest-data.
    setup, asof = None, None
    try:
        entries = json.loads(AGRI_SIGNALS.read_text())
        asof = datetime.fromtimestamp(AGRI_SIGNALS.stat().st_mtime, timezone.utc).date().isoformat()
        sugar = [e for e in entries if e.get("instrument") == "Sugar"]
        published = [e for e in sugar if e.get("published")]
        # Vis den publiserte setupen (det boten faktisk handler på). Er ingen
        # publisert, vis den sterkeste under terskel.
        chosen = max(published or sugar, key=lambda e: e.get("score", 0)) if sugar else None
        if chosen:
            setup = {
                "direction": chosen["direction"],
                "score": round(chosen["score"], 2),
                "grade": chosen.get("grade"),
                "horizon": chosen.get("horizon"),
                "published": bool(chosen.get("published")),
            }
    except Exception as e:  # noqa: BLE001
        print(f"  ADVARSEL: kunne ikke lese agri_signals.json ({e})")

    floor = json.loads(FLOOR_JSON.read_text()) if FLOOR_JSON.exists() else {}
    return {
        "asof": asof,
        "latest_setup": setup,
        "floor": {
            "buy": floor.get("current_yaml_buy"),
            "sell": floor.get("current_yaml_sell"),
            "recommended_sell": floor.get("sell_floor"),
            "pending": bool(floor.get("significant")) and not floor.get("applied"),
            "asof": floor.get("as_of"),
        },
    }


# ---------------- CropWatch-seksjoner ----------------------------------------

def ndvi():
    per_country, latest_dates = {}, []
    for region_id, weight in COUNTRY_WEIGHTS.items():
        region = get_region(region_id)
        anomalies = []
        for area in region.areas:
            res = compute.ndvi_with_baseline(cw_db.get_ndvi(region_id, area.id))
            if res["latest"] and res["latest"]["anomaly"] is not None:
                anomalies.append(res["latest"]["anomaly"])
                latest_dates.append(res["latest"]["date"])
        if anomalies:
            per_country[region_id] = round(sum(anomalies) / len(anomalies), 4)
    weighted = sum(per_country[r] * COUNTRY_WEIGHTS[r] for r in per_country)
    weighted /= sum(COUNTRY_WEIGHTS[r] for r in per_country)
    return {
        "asof": max(latest_dates) if latest_dates else None,
        "weighted_anomaly": round(weighted, 4),
        "per_country": per_country,
        "weights": COUNTRY_WEIGHTS,
    }


def frost():
    region = get_region("brazil_sugar")
    min14, frost_days, coldest, asof = None, 0, None, None
    for area in region.areas:
        weather = cw_db.get_weather("brazil_sugar", area.id)[-30:]
        for w in weather:
            if w.temp_min_c is None:
                continue
            asof = max(asof or w.date.isoformat(), w.date.isoformat())
            if w.date >= date.today().replace(day=1):
                pass
            if (min14 is None or w.temp_min_c < min14) and \
               (date.today() - w.date).days <= 14:
                min14, coldest = w.temp_min_c, w.date.isoformat()
            if w.temp_min_c <= FROST_THRESHOLD_C:
                frost_days += 1
    return {
        "asof": asof,
        "window_active": date.today().month in FROST_MONTHS,
        "min_temp_14d": min14,
        "coldest_date": coldest,
        "frost_days_30d": frost_days,
        "threshold_c": FROST_THRESHOLD_C,
    }


def season():
    region = get_region("brazil_sugar")
    pos = compute.cycle_position(region.cycle)
    month = date.today().month
    return {
        "asof": date.today().isoformat(),
        "phase": pos["current"]["phase"] if pos["current"] else None,
        "score": SEASON_SCORES[month - 1],
        "month": month,
    }


def enso():
    d = enso_mod.fetch_oni(months_back=36)
    return {
        "asof": d["series"][-1]["date"] if d["series"] else None,
        "state": d["state"], "strength": d["strength"],
        "oni": d["latest_oni"], "trend": d["trend"],
        "series": d["series"][-24:],
    }


# ---------------- press-retning per driver -----------------------------------
# +1 = presser prisen OPP, -1 = NED, 0 = nøytral. Polaritet følger bedrock
# sin sukker-modell (sugar.yaml) der den er definert.

def pressures(s: dict) -> dict:
    p = {}

    def has(k):
        return isinstance(s.get(k), dict) and "error" not in s[k]

    if has("brl") and s["brl"].get("chg_5d_pct") is not None:
        c = s["brl"]["chg_5d_pct"]   # + = svakere real → bull per bedrock-modellen
        p["brl"] = 1 if c > 1.0 else -1 if c < -1.0 else 0
    if has("ethanol") and s["ethanol"].get("ratio_pct_3y") is not None:
        r = s["ethanol"]["ratio_pct_3y"]  # lav ratio = etanol frister = bull
        p["ethanol"] = 1 if r <= 25 else -1 if r >= 75 else 0
    if has("unica") and s["unica"].get("sugar_production_yoy_pct") is not None:
        y = s["unica"]["sugar_production_yoy_pct"]
        p["unica"] = -1 if y > 5 else 1 if y < -5 else 0
    if has("india") and s["india"].get("exports_12m_yoy_pct") is not None:
        y = s["india"]["exports_12m_yoy_pct"]
        p["india"] = 1 if y < -10 else -1 if y > 10 else 0
    if has("cot") and s["cot"].get("pct_52w") is not None:
        p["cot"] = 0 if not s["cot"]["extreme"] else (
            1 if s["cot"]["pct_52w"] <= 5 else -1)  # ekstrem = vending-risiko
    if has("enso") and s["enso"].get("oni") is not None:
        o = s["enso"]["oni"]   # El Niño (høy ONI) = bull for sukker
        p["enso"] = 1 if o >= 0.5 else -1 if o <= -0.5 else 0
    if has("ndvi") and s["ndvi"].get("weighted_anomaly") is not None:
        a = s["ndvi"]["weighted_anomaly"]  # svakere planter = mindre sukker = bull
        p["ndvi"] = 1 if a < -0.03 else -1 if a > 0.03 else 0
    if has("frost"):
        f = s["frost"]
        p["frost"] = 1 if (f.get("window_active") and f.get("frost_days_30d", 0) > 0) else 0
    if has("season") and s["season"].get("score") is not None:
        sc = s["season"]["score"]
        p["season"] = 1 if sc >= 0.9 else -1 if sc <= 0.7 else 0
    return p


def main() -> None:
    print(f"Leser bedrock: {BEDROCK_DB}")
    if not BEDROCK_DB.exists():
        sys.exit("FEIL: bedrock.db ikke funnet – dette skriptet må kjøres lokalt.")

    # Frost-vakten trenger DAGENS minimumstemperatur – hent ferskt Brasil-vær
    # (inkrementelt, ~3 kall). Svikter det, brukes det som ligger lagret.
    try:
        from app import service as cw_service
        cw_service.refresh_weather(get_region("brazil_sugar"))
        print("Hentet ferskt Brasil-vær for frost-vakten.")
    except Exception as e:  # noqa: BLE001
        print(f"  ADVARSEL: vær-oppdatering feilet ({e}) – bruker lagrede data.")

    out = {
        "built_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "price": section(price),
        "brl": section(brl),
        "ethanol": section(ethanol),
        "unica": section(unica),
        "india": section(india),
        "cot": section(cot),
        "enso": section(enso),
        "ndvi": section(ndvi),
        "frost": section(frost),
        "season": section(season),
        "bedrock": section(bedrock_view),
    }
    out["pressure"] = pressures(out)

    (DOCS / "data").mkdir(parents=True, exist_ok=True)
    (DOCS / "data" / "sugar11.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Skrev docs/data/sugar11.json")

    for name in ("sugar11.html", "sugar11.js"):
        if (FRONTEND / name).exists():
            shutil.copy2(FRONTEND / name, DOCS / name)
            print(f"Kopierte {name}")

    summary = out["pressure"]
    print("Press-retning:", summary,
          "| opp:", sum(1 for v in summary.values() if v > 0),
          "ned:", sum(1 for v in summary.values() if v < 0))

    if "--push" in sys.argv:
        subprocess.run(["git", "-C", str(ROOT), "add", "docs/"], check=True)
        diff = subprocess.run(["git", "-C", str(ROOT), "diff", "--staged", "--quiet"])
        if diff.returncode != 0:
            subprocess.run(["git", "-C", str(ROOT), "commit", "-m",
                            f"Oppdater sugar11-dashbord {date.today()}"], check=True)
            subprocess.run(["git", "-C", str(ROOT), "pull", "--rebase",
                            "-X", "theirs", "origin", "main"], check=True)
            subprocess.run(["git", "-C", str(ROOT), "push", "origin", "main"], check=True)
            print("Pushet til GitHub.")
        else:
            print("Ingen endringer å pushe.")


if __name__ == "__main__":
    main()
