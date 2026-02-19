# 🚴 Cycling Dashboard

Persoonlijk cycling dashboard gevoed door de **Intervals.icu API**, gehost via **GitHub Pages** met een dagelijkse **GitHub Actions** data pipeline.

## Structuur

```
cycling-dashboard/
├── .github/
│   └── workflows/
│       └── fetch-data.yml      # Dagelijkse cron job
├── scripts/
│   └── fetch_intervals.py      # Intervals.icu data fetcher
├── data/                       # Auto-gegenereerde JSON bestanden
│   ├── summary.json
│   ├── activities.json
│   ├── wellness.json
│   └── power_curve.json
├── index.html                  # Dashboard frontend
└── README.md
```

## Setup

### 1. GitHub Secrets instellen

Ga naar je repo → **Settings → Secrets → Actions** en voeg toe:

| Secret | Waarde |
|--------|--------|
| `INTERVALS_API_KEY` | Jouw Intervals.icu API key (Settings → API Key) |
| `INTERVALS_ATHLETE_ID` | Jouw athlete ID (te vinden in je profiel URL: `intervals.icu/athlete/**i12345**/...`) |

### 2. GitHub Pages activeren

Ga naar **Settings → Pages** en kies:
- Source: `Deploy from a branch`
- Branch: `main` / `root`

### 3. Eerste data sync

Ga naar **Actions → Fetch Cycling Data → Run workflow** om handmatig de eerste sync te starten. Daarna loopt het elke nacht automatisch om 03:00 UTC.

### 4. Aanpassen

Open `index.html` en pas aan:
```js
const WEIGHT_KG = 75;  // ← Jouw gewicht voor W/kg berekening
```

## Synology NAS

Voor live hosting op je NAS: kopieer de hele repo naar je webroot en stel een cron job in die `fetch_intervals.py` direct aanroept (Python + secrets als environment variabelen in de cron).

## Data refresh

- **GitHub Pages**: elke nacht automatisch via Actions
- **Synology**: cron instellen, bijv. `0 3 * * * python3 /pad/naar/fetch_intervals.py`
