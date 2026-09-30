"""NDVI-connector (vegetasjonshelse).

Standard kilde (fra 2026-09-30): NASA VIIRS på NOAA-20 via AppEEARS
(`nasa_viirs`). Produkt VJ113A1 v002, 500 m, 16-dagers komposit, løpende fra
2018. Krever en gratis Earthdata-konto: brukernavn og passord leses fra
miljøvariablene EARTHDATA_USER og EARTHDATA_PASS (GitHub-secrets i Actions).
Produktet lages hver 8. dag (to overlappende 16-dagersvinduer), så vi får en
ny verdi ~hver 8. dag – samme takt som Terra+Aqua ga.

Gammel kilde: NASA MODIS via ORNL DAAC (`nasa_modis`, ingen nøkkel).
Terra (MOD13Q1) + Aqua (MYD13Q1). Aqua sluttet å levere i august 2026 og
Terra er på slutten av levetiden, så MODIS-serien stopper 2026-08-13.
Klassen beholdes for historikk/testing, men brukes ikke lenger av regionene.

BYTTE KILDE SENERE:
  Vil du bruke Agromonitoring eller NASA Earthdata i stedet, lag en ny klasse
  som arver fra `NdviConnector`, implementer `fetch(...)`, og registrer den i
  `NDVI_CONNECTORS` nederst. Sett så `sources.ndvi` i region-YAMLen til navnet.
  Resten av appen trenger ingen endring.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import time
from datetime import date, datetime, timedelta

import httpx

log = logging.getLogger("cropwatch.ndvi")


def _get_with_retry(client: httpx.Client, url: str, params: dict, attempts: int = 4,
                    headers: dict | None = None):
    """GET med noen gjenforsøk – NASA-tjenestene har av og til korte blipp."""
    last_error: Exception | None = None
    for i in range(attempts):
        try:
            r = client.get(url, params=params,
                           headers={"Accept": "application/json", **(headers or {})})
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
    # Versjon av tolkningen; endres den, hentes historikken på nytt.
    history_version: int = 1

    @property
    def history_key(self) -> str:
        """Identifiserer kilde+tolkning som historikken i databasen er bygget på."""
        return f"{self.name}@{self.history_version}"
    # Første dato kilden har data for (brukes når historikk hentes på nytt).
    earliest: date = date(2000, 2, 18)

    def ready(self) -> str | None:
        """None hvis kilden kan brukes nå, ellers en kort forklaring (f.eks.
        manglende nøkkel). Da hopper appen over henting og beholder det den har."""
        return None

    def fetch(self, lat: float, lon: float, start: date, end: date) -> list[NdviObservation]:
        raise NotImplementedError

    def fetch_many(
        self, points: list[tuple[str, float, float]], start: date, end: date
    ) -> dict[str, list[NdviObservation]]:
        """Henter flere punkter (id, lat, lon) på én gang.

        Standard: ett kall per punkt, og ett punkt som feiler stopper ikke de
        andre. Kilder som kan hente alt i én forespørsel overstyrer denne.
        """
        out: dict[str, list[NdviObservation]] = {}
        for point_id, lat, lon in points:
            try:
                out[point_id] = self.fetch(lat, lon, start, end)
            except Exception:  # noqa: BLE001
                log.exception("NDVI-henting feilet for %s – hopper over", point_id)
        return out


class NasaModisNdvi(NdviConnector):
    name = "nasa_modis"
    # Terra og Aqua er forskjøvet 8 dager, så sammen gir de en ny verdi ~hver 8. dag.
    cadence_days = 8

    BASE_URL = "https://modis.ornl.gov/rst/api/v1"
    # Begge MODIS-satellittene: Terra (MOD) og Aqua (MYD). Terra er på slutten
    # av levetiden og leverer stadig senere/sjeldnere – Aqua fyller hullene, og
    # svikter den ene helt, fortsetter vi med den andre.
    PRODUCTS = ("MOD13Q1", "MYD13Q1")
    BAND = "250m_16_days_NDVI"
    SCALE = 0.0001
    # MODIS bruker fyll-verdien -3000 der data mangler (skyer o.l.).
    FILL_BELOW = -2000
    # Tjenesten tillater maks 10 datoer per forespørsel.
    MAX_DATES_PER_REQUEST = 10

    def __init__(self, timeout_s: float = 60.0, polite_delay_s: float = 0.4):
        self._timeout = timeout_s
        self._delay = polite_delay_s

    def _available_dates(
        self, client: httpx.Client, product: str, lat: float, lon: float
    ) -> list[dict]:
        """Henter alle tilgjengelige MODIS-datoer for punktet."""
        r = _get_with_retry(
            client,
            f"{self.BASE_URL}/{product}/dates",
            {"latitude": lat, "longitude": lon},
        )
        return r.json().get("dates", [])

    def fetch(self, lat: float, lon: float, start: date, end: date) -> list[NdviObservation]:
        by_date: dict[date, NdviObservation] = {}
        errors: list[Exception] = []
        with httpx.Client(timeout=self._timeout) as client:
            for product in self.PRODUCTS:
                try:
                    for obs in self._fetch_product(client, product, lat, lon, start, end):
                        by_date.setdefault(obs.date, obs)
                except Exception as e:  # noqa: BLE001
                    errors.append(e)
        # Feilet begge satellittene, si fra – feilet bare én, lever vi med det.
        if errors and not by_date:
            raise errors[0]
        return [by_date[d] for d in sorted(by_date)]

    def _fetch_product(
        self, client: httpx.Client, product: str, lat: float, lon: float, start: date, end: date
    ) -> list[NdviObservation]:
        observations: list[NdviObservation] = []
        dates = self._available_dates(client, product, lat, lon)

        # Behold kun datoer innenfor det forespurte tidsrommet.
        wanted = [
            d for d in dates
            if start <= datetime.strptime(d["calendar_date"], "%Y-%m-%d").date() <= end
        ]
        modis_dates = [d["modis_date"] for d in wanted]

        # Del opp i biter på maks 10 (tjenestens grense) og hent hver bit.
        for i in range(0, len(modis_dates), self.MAX_DATES_PER_REQUEST):
            chunk = modis_dates[i:i + self.MAX_DATES_PER_REQUEST]
            observations.extend(
                self._fetch_chunk(client, product, lat, lon, chunk[0], chunk[-1]))
            if self._delay:
                time.sleep(self._delay)
        return observations

    def _fetch_chunk(
        self, client: httpx.Client, product: str, lat: float, lon: float,
        start_modis: str, end_modis: str
    ) -> list[NdviObservation]:
        r = _get_with_retry(
            client,
            f"{self.BASE_URL}/{product}/subset",
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


class NasaViirsNdvi(NdviConnector):
    """NDVI fra VIIRS (NOAA-20, reserve NOAA-21) via NASA sin AppEEARS-tjeneste.

    AppEEARS jobber asynkront: vi sender inn en "oppgave" med alle punktene i
    regionen og hele tidsrommet, venter til den er ferdig (typisk 1–5 min), og
    laster ned én CSV-fil med alle verdiene. Derfor hentes en hel region i ett
    kall (`fetch_many`) i stedet for punkt for punkt.
    """

    name = "nasa_viirs"
    cadence_days = 8
    earliest = date(2018, 1, 1)  # NOAA-20 startet å levere 2018-01-01
    # Øk denne når tolkningen av dataene endres (f.eks. filtrering), så
    # historikken hentes på nytt med den nye tolkningen.
    history_version = 3

    BASE_URL = "https://appeears.earthdatacloud.nasa.gov/api"
    # Bare NOAA-20. NOAA-21 (VJ213A1.002) kan legges til som reserve, men
    # dobler behandlingstiden hos NASA (første henting tok ~30 min per region).
    PRODUCTS = ("VJ113A1.002",)
    LAYER_NDVI = "500_m_16_days_NDVI"
    LAYER_RELIABILITY = "500_m_16_days_pixel_reliability"
    SCALE = 0.0001
    # Piksel-pålitelighet (rang) i VIIRS v002 går 0–11: 0 utmerket, 1 god,
    # 2 akseptabel, 3 marginal, 4 godkjent, 5 tvilsom, 6 dårlig, 7 skyskygge,
    # 8 snø/is, 9 sky, 10 estimert, 11 langtidssnitt; negativt = mangler.
    # Vi filtrerer IKKE på rang (samme som MODIS-connectoren gjorde): i
    # monsunen er nesten alle kompositter rang 4–9, og et filter tømte hele
    # vekstsesongen for Thailand og India. Komposittet er uansett den beste
    # pikselen i vinduet. Bare manglende verdier (fyll) hoppes over.
    MAX_RELIABILITY = 11
    ENV_USER = "EARTHDATA_USER"
    ENV_PASS = "EARTHDATA_PASS"

    def __init__(self, timeout_s: float = 120.0, poll_s: float = 30.0, max_wait_s: float = 3600.0):
        self._timeout = timeout_s
        self._poll = poll_s
        self._max_wait = max_wait_s

    # -- oppsett ------------------------------------------------------------

    def _credentials(self) -> tuple[str, str] | None:
        user = os.environ.get(self.ENV_USER, "").strip()
        password = os.environ.get(self.ENV_PASS, "").strip()
        if user and password:
            return user, password
        return None

    def ready(self) -> str | None:
        if self._credentials() is None:
            return (f"mangler Earthdata-innlogging (sett miljøvariablene "
                    f"{self.ENV_USER} og {self.ENV_PASS})")
        return None

    def _login(self, client: httpx.Client) -> str:
        creds = self._credentials()
        if creds is None:
            raise RuntimeError(self.ready())
        r = client.post(f"{self.BASE_URL}/login", auth=creds)
        r.raise_for_status()
        return r.json()["token"]

    # -- henting ------------------------------------------------------------

    def fetch(self, lat: float, lon: float, start: date, end: date) -> list[NdviObservation]:
        return self.fetch_many([("p", lat, lon)], start, end).get("p", [])

    def fetch_many(
        self, points: list[tuple[str, float, float]], start: date, end: date
    ) -> dict[str, list[NdviObservation]]:
        if not points:
            return {}
        task_id = self.submit(points, start, end)
        results = self.collect(task_id)
        if results is None:
            raise TimeoutError(f"AppEEARS-oppgave {task_id} ble ikke ferdig innen "
                               f"{int(self._max_wait)} s")
        return results

    # NASA sin kø kan være treg (30–60+ min per oppgave). Derfor kan appen sende
    # inn en oppgave, huske oppgave-id-en, og hente resultatet ved en SENERE
    # kjøring hvis den ikke rakk å bli ferdig. Tjenestelaget lagrer id-en.

    def submit(self, points: list[tuple[str, float, float]], start: date, end: date) -> str:
        """Sender inn en oppgave hos NASA og returnerer oppgave-id-en."""
        start = max(start, self.earliest)
        with httpx.Client(timeout=self._timeout) as client:
            headers = {"Authorization": f"Bearer {self._login(client)}"}
            return self._submit(client, headers, points, start, end)

    def collect(self, task_id: str) -> dict[str, list[NdviObservation]] | None:
        """Venter (opptil max_wait) på oppgaven og henter resultatet.

        Returnerer None hvis oppgaven fortsatt jobber når tiden er ute – da
        lever den videre hos NASA og kan hentes ved neste kjøring. Feiler
        oppgaven, eller finnes den ikke lenger, kastes en feil (og den bør
        sendes inn på nytt).
        """
        with httpx.Client(timeout=self._timeout, follow_redirects=True) as client:
            headers = {"Authorization": f"Bearer {self._login(client)}"}
            if not self._wait(client, headers, task_id):
                log.warning("AppEEARS-oppgave %s er ikke ferdig ennå – prøver igjen neste kjøring",
                            task_id)
                return None
            try:
                csv_text = self._download_csv(client, headers, task_id)
            finally:
                self._delete(client, headers, task_id)
        return self.parse_csv(csv_text)

    def _delete(self, client, headers, task_id: str) -> None:
        # Rydd opp hos NASA – best effort, feil her er uviktig.
        try:
            client.delete(f"{self.BASE_URL}/task/{task_id}", headers=headers)
        except Exception:  # noqa: BLE001
            pass

    def _submit(self, client, headers, points, start: date, end: date) -> str:
        layers = []
        for product in self.PRODUCTS:
            layers.append({"product": product, "layer": self.LAYER_NDVI})
            layers.append({"product": product, "layer": self.LAYER_RELIABILITY})
        body = {
            "task_type": "point",
            "task_name": f"cropwatch_{datetime.utcnow():%Y%m%d_%H%M%S}",
            "params": {
                "dates": [{"startDate": start.strftime("%m-%d-%Y"),
                           "endDate": end.strftime("%m-%d-%Y")}],
                "layers": layers,
                "coordinates": [
                    {"id": pid, "category": pid, "latitude": lat, "longitude": lon}
                    for pid, lat, lon in points
                ],
            },
        }
        r = client.post(f"{self.BASE_URL}/task", json=body, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f"AppEEARS avviste oppgaven ({r.status_code}): {r.text[:300]}")
        task_id = r.json()["task_id"]
        log.info("AppEEARS-oppgave %s sendt (%d punkter, %s–%s)", task_id, len(points), start, end)
        return task_id

    def _wait(self, client, headers, task_id: str) -> bool:
        """Sant når oppgaven er ferdig, usant hvis tiden gikk ut. Feil → unntak."""
        deadline = time.monotonic() + self._max_wait
        while True:
            r = client.get(f"{self.BASE_URL}/task/{task_id}", headers=headers)
            if r.status_code == 404:
                raise RuntimeError(f"AppEEARS-oppgave {task_id} finnes ikke (lenger) hos NASA")
            if r.status_code >= 500 or r.status_code == 429:
                time.sleep(self._poll)  # kortvarig blipp hos NASA – prøv igjen
                continue
            r.raise_for_status()
            status = r.json().get("status")
            if status == "done":
                return True
            if status == "error":
                self._delete(client, headers, task_id)
                raise RuntimeError(f"AppEEARS-oppgave {task_id} feilet hos NASA")
            if time.monotonic() > deadline:
                return False
            time.sleep(self._poll)

    def _download_csv(self, client, headers, task_id: str) -> str:
        r = _get_with_retry(client, f"{self.BASE_URL}/bundle/{task_id}", {}, headers=headers)
        files = [f for f in r.json().get("files", []) if f.get("file_type") == "csv"]
        # Resultatfilen heter "<oppgavenavn>-results.csv"; hopp over granule-lista.
        files.sort(key=lambda f: ("results" not in f.get("file_name", ""), f.get("file_name", "")))
        if not files:
            raise RuntimeError(f"AppEEARS-oppgave {task_id} ga ingen CSV-fil")
        r = _get_with_retry(
            client, f"{self.BASE_URL}/bundle/{task_id}/{files[0]['file_id']}", {},
            headers=headers)
        return r.text

    # -- tolkning -----------------------------------------------------------

    def parse_csv(self, csv_text: str) -> dict[str, list[NdviObservation]]:
        """Gjør AppEEARS-CSV om til observasjoner per punkt-id.

        Kolonnene heter f.eks. "VJ113A1_002_500_m_16_days_NDVI". Verdiene er
        ferdig skalert (-1..1); fyll-verdi vises som -1.3 over land og -1.5
        over vann (rå -13000/-15000) og faller utenfor gyldig område.
        """
        reader = csv.DictReader(io.StringIO(csv_text))
        fields = reader.fieldnames or []
        if not fields:
            return {}
        log.info("AppEEARS-CSV kolonner: %s", ", ".join(fields))

        def _col(product: str, layer: str) -> str | None:
            prefix = product.replace(".", "_")
            for f in fields:
                if f.startswith(prefix) and f.endswith(layer):
                    return f
            return None

        columns = [(p, _col(p, self.LAYER_NDVI), _col(p, self.LAYER_RELIABILITY))
                   for p in self.PRODUCTS]
        columns = [c for c in columns if c[1]]
        if not columns:
            raise RuntimeError(f"Fant ingen NDVI-kolonne i AppEEARS-CSV. Kolonner: {fields}")

        # {punkt: {dato: (prioritet, verdi)}} – lavest prioritet (NOAA-20) vinner.
        best: dict[str, dict[date, tuple[int, float]]] = {}
        for row in reader:
            pid = row.get("ID") or row.get("Category") or ""
            try:
                obs_date = datetime.strptime(row["Date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                continue
            for priority, (_product, ndvi_col, rel_col) in enumerate(columns):
                value = self._number(row.get(ndvi_col))
                if value is None:
                    continue
                if abs(value) > 1.0:          # rå heltall (ikke skalert) – skaler selv
                    value = value * self.SCALE
                if value < -1.0 or value > 1.0:
                    continue                  # fyll-verdi (-1.5) eller søppel
                reliability = self._number(row.get(rel_col)) if rel_col else None
                if reliability is not None and not (0 <= reliability <= self.MAX_RELIABILITY):
                    continue                  # skyet/snø/mangler
                slot = best.setdefault(pid, {})
                if obs_date not in slot or priority < slot[obs_date][0]:
                    slot[obs_date] = (priority, round(value, 4))

        return {
            pid: [NdviObservation(date=d, value=v) for d, (_p, v) in sorted(vals.items())]
            for pid, vals in best.items()
        }

    @staticmethod
    def _number(raw) -> float | None:
        if raw is None or raw == "":
            return None
        try:
            return float(raw)
        except ValueError:
            return None


# Registret som kobler navn (fra YAML) til en faktisk connector.
NDVI_CONNECTORS: dict[str, NdviConnector] = {
    "nasa_viirs": NasaViirsNdvi(),
    "nasa_modis": NasaModisNdvi(),
}


def get_ndvi_connector(name: str) -> NdviConnector:
    if name not in NDVI_CONNECTORS:
        raise KeyError(f"Ukjent NDVI-kilde '{name}'. Finnes: {list(NDVI_CONNECTORS)}")
    return NDVI_CONNECTORS[name]
