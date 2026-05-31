# 🌱 CropWatch

CropWatch overvåker avlingsforhold for dyrkingsregioner. Den henter ferdige
data om **vegetasjonshelse (NDVI)** fra satellitt og **vær**, sammenligner mot
det historiske normalnivået for årstiden, og viser det i et dashbord med kart,
grafer og enkle grønn/gul/rød-indikatorer.

CropWatch **tolker ikke** for deg – den gjør forholdene synlige, så du selv kan
vurdere dem. Ingen AI er involvert.

Første region er **sukkerrør i Brasil**.

---

## Hva du ser i dashbordet

- **Vegetasjon (NDVI):** hvor grønn og frodig vegetasjonen er, målt fra satellitt,
  sammenlignet med snittet for samme årstid de siste årene.
- **Nedbør hittil i år:** hvor mye regn som har falt i år mot et normalår.
- **Varmestress:** antall svært varme dager siste måned.
- **Tørkestress:** lengste tørre periode siste måned.

Farger: **grønn** = normalt eller bra, **gul** = følg med, **rød** = tydelig avvik.

---

## Datakilder (begge gratis)

| Hva | Kilde | Nøkkel kreves? |
|-----|-------|----------------|
| NDVI (vegetasjon) | NASA MODIS via ORNL DAAC | Nei |
| Vær (nedbør/temperatur) | Open-Meteo | Nei |

NDVI oppdateres hver ~16. dag (det er så ofte satellitten gir en ny verdi).
Vær hentes daglig. Appen henter automatisk bare når det trengs.

---

## Komme i gang (én gang)

Du trenger **Python 3** installert. Åpne et terminalvindu i denne mappen og
kjør disse fire kommandoene, én etter én:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app
```

Åpne så nettleseren på **http://localhost:8000**.

Første gang henter appen flere år med historikk i bakgrunnen (et par minutter).
Dashbordet fungerer med en gang for det første området, og resten fylles inn
etter hvert. Du kan trygt la appen kjøre – den oppdaterer seg selv.

For å stoppe appen: trykk `Ctrl + C` i terminalen.
For å starte den igjen senere: `source .venv/bin/activate` og så `uvicorn app.main:app`.

---

## Legge til en ny region (uten å programmere)

Dette er det fine: en ny region eller råvare krever **ingen kodeendring** – bare
en ny konfigurasjonsfil.

1. Gå til mappen `app/config/regions/`.
2. Kopier `brazil_sugar.yaml` og gi kopien et nytt navn, f.eks.
   `ivorycoast_cocoa.yaml`.
3. Åpne den nye filen og endre verdiene: navn, råvare, og dyrkingsområdene med
   deres koordinater (breddegrad `lat` og lengdegrad `lon`). Filen har
   forklarende kommentarer på hver linje.
4. Start appen på nytt. Den nye regionen dukker opp i nedtrekksmenyen automatisk.

Slik finner du koordinater: søk opp stedet på et kart, høyreklikk og kopier
breddegrad/lengdegrad (lat/lon).

---

## Hvordan det henger sammen (kort)

```
app/
├── config/regions/   ← regionene dine (YAML-filer). Ny region = ny fil her.
├── connectors/       ← henter data: ndvi.py (NASA), weather.py (Open-Meteo)
├── storage/          ← lagrer dataene (én SQLite-fil per region, i data/)
├── indicators/       ← regner ut avvik mot normalt
├── scheduler.py      ← henter automatisk med riktig frekvens
├── service.py        ← limet mellom delene
└── main.py           ← nettappen (API + dashbord)
frontend/             ← selve dashbordet (kart + grafer)
```

**Bytte NDVI-kilde senere?** NDVI-connectoren er bygget for å kunne byttes ut
(f.eks. til Agromonitoring eller NASA Earthdata) ved å legge til én ny klasse i
`app/connectors/ndvi.py` – uten å røre resten av appen.

---

## Vanlige spørsmål

**Må jeg gjøre noe daglig?** Nei. Lar du appen kjøre, oppdaterer den seg selv.

**Koster noe av dette penger?** Nei. Begge datakildene er gratis og krever ingen
konto eller nøkkel.

**Hvor lagres dataene?** I mappen `data/`, som én fil per region. Disse filene
er ikke en del av koden og lastes ikke opp til GitHub (se `.gitignore`).
