"""
Intervals.icu data fetcher voor cycling dashboard.
Gebaseerd op de robuuste normalisatielogica van Climb-Performance-Lab:
  - cloud-function/main.py     (robuuste veldnaam-fallbacks, type detectie)
  - src/utils/intervals.js     (sanity checks, max_power uit icu_intervals, eFTP)
  - src/utils/formatters.js    (date normalisatie)
  - src/utils/parsers.js       (cleanNumber, EU/US decimaal detectie)

Genereert 4 JSON bestanden in /data/:
  - activities.json   → alle fietsritten (genormaliseerd)
  - wellness.json     → CTL / ATL / TSB / HRV / gewicht
  - power_curve.json  → seizoen beste MMP curve
  - summary.json      → samenvattende statistieken
"""

import os
import json
import base64
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY      = os.environ["INTERVALS_API_KEY"]
ATHLETE_ID   = os.environ["INTERVALS_ATHLETE_ID"]
BASE_URL     = f"https://intervals.icu/api/v1/athlete/{ATHLETE_ID}"
HEADERS      = {
    "Authorization": "Basic " + base64.b64encode(f"API_KEY:{API_KEY}".encode()).decode(),
    "Accept": "application/json",
}
DATA_DIR     = "data"

END_DATE     = datetime.today()
START_DATE   = END_DATE - relativedelta(months=12)
FMT          = "%Y-%m-%d"

CYCLING_TYPES = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide", "EBikeRide"}

# Max plausibele 1-minuut power (sanity check, overgenomen uit intervals.js)
MAX_SANE_1MIN_POWER = 1200


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get(endpoint: str, params: dict = None):
    """Haal data op van de Intervals.icu API."""
    r = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _save(filename: str, data) -> None:
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    count = len(data) if isinstance(data, list) else "—"
    print(f"  ✅ {path}  ({count} records)")


def safe_int(val, default=0) -> int:
    """Veilig converteren naar int, None en ongeldige waarden afvangen."""
    if val is None:
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def safe_float(val, default=0.0) -> float:
    """Veilig converteren naar float, None en ongeldige waarden afvangen."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_first_valid(*values, default=None):
    """Geeft de eerste waarde terug die niet None, 0 of leeg is."""
    for val in values:
        if val is not None and val != 0 and val != "":
            return val
    return default


def normalize_date(date_str: str) -> str:
    """
    Normaliseer datum naar YYYY-MM-DD.
    Gebaseerd op formatters.js normalizeDate().
    """
    if not date_str:
        return datetime.now().strftime(FMT)
    if "T" in str(date_str):
        return str(date_str)[:10]
    try:
        return datetime.fromisoformat(str(date_str)).strftime(FMT)
    except (ValueError, AttributeError):
        return str(date_str)[:10] if len(str(date_str)) >= 10 else str(date_str)


def determine_activity_type(api_type: str, title: str, is_indoor: bool = False) -> str:
    """
    Bepaal het activiteitstype met detectie van indoor ritten.
    Combineert logica van main.py en parsers.js.
    """
    clean = (api_type or "").replace(" ", "").lower()

    # Directe type mapping
    type_map = {
        "virtualride": "VirtualRide",
        "run": "Run",
        "walk": "Walk",
        "hike": "Hike",
        "gravelride": "GravelRide",
        "gravel": "GravelRide",
        "mountainbikeride": "MountainBikeRide",
        "mtb": "MountainBikeRide",
        "ebikeride": "EBikeRide",
        "ebike": "EBikeRide",
        "swim": "Swim",
        "weighttraining": "WeightTraining",
    }
    if clean in type_map:
        return type_map[clean]

    # Trainer flag → VirtualRide
    if is_indoor and clean in ("ride", "cycling", ""):
        return "VirtualRide"

    # Titel keywords → VirtualRide
    title_lower = (title or "").lower()
    virtual_keywords = [
        "virtual", "indoor", "trainer",
        "zwift", "watopia", "makuri", "richmond", "london", "innsbruck",
        "yorkshire", "bologna", "crit city", "scotland", "neokyo", "france",
        "mywhoosh", "rouvy", "fulgaz", "bkool", "rgt", "indievelo", "kinomap",
        "tacx", "trainerroad", "sufferfest", "systm", "wahoo", "kickr",
    ]
    if any(kw in title_lower for kw in virtual_keywords):
        return "VirtualRide"

    return "Ride" if clean in ("ride", "cycling", "") else (api_type or "Ride")


# ── Activiteiten normalisatie ─────────────────────────────────────────────────

def normalize_activity(a: dict) -> dict:
    """
    Normaliseer een Intervals.icu activiteit naar een consistent formaat.

    Gebaseerd op de beste logica van drie bronnen:
    - src/utils/intervals.js  → getVal(), sanity checks, max_power_source, eFTP
    - cloud-function/main.py  → get_first_valid(), RPE validatie
    - src/utils/parsers.js    → type detectie
    """
    def val(*keys):
        """Geeft de eerste niet-None/nul waarde van de opgegeven keys."""
        for k in keys:
            v = a.get(k)
            if v is not None and v != 0 and v != "":
                return v
        return None

    # ── Basis ──────────────────────────────────────────────────────────────
    activity_id   = str(val("id", "activity_id") or "")
    title         = str(val("name", "title") or "Untitled")
    is_indoor     = bool(a.get("trainer", False))
    raw_type      = val("type", "sub_type", "activity_type") or "Ride"
    activity_type = determine_activity_type(raw_type, title, is_indoor)

    date_str = val("start_date_local", "start_date", "startDate", "date")
    date     = normalize_date(date_str)

    # ── Tijd (seconden) ────────────────────────────────────────────────────
    moving_time = safe_int(val("moving_time", "movingTime", "icu_recording_time",
                               "elapsed_time", "elapsedTime"))

    # ── Afstand (meter → km) ───────────────────────────────────────────────
    # Indoor: voorkeur icu_distance (trainer), Outdoor: voorkeur GPS distance
    if is_indoor:
        dist_m = safe_float(val("icu_distance", "distance", "total_distance"))
    else:
        dist_m = safe_float(val("distance", "icu_distance", "total_distance"))
    distance_km = round(dist_m / 1000, 2) if dist_m > 0 else 0.0

    # ── Hoogte (meter) ─────────────────────────────────────────────────────
    elevation = safe_int(val("total_elevation_gain", "elevationGain",
                             "icu_elevation_gain", "total_elevation_loss"))

    # ── Snelheid (m/s → km/h) ─────────────────────────────────────────────
    avg_speed_ms = safe_float(val("average_speed", "avg_speed", "avgSpeed", "velocity_smooth"))
    if avg_speed_ms > 0 and avg_speed_ms < 15:  # Beveiliging: > 15 m/s is al > 54 km/h
        avg_speed_kmh = round(avg_speed_ms * 3.6, 1)
    elif distance_km > 0 and moving_time > 0:
        avg_speed_kmh = round(distance_km / (moving_time / 3600), 1)
    else:
        avg_speed_kmh = 0.0

    # ── Power (watt) ───────────────────────────────────────────────────────
    avg_power = safe_int(val("icu_average_watts", "average_watts", "avgWatts",
                             "device_watts", "avg_power", "power"))
    norm_power = safe_int(val("icu_weighted_avg_watts", "weighted_avg_watts",
                              "normalizedPower", "norm_power", "np", "normalized_power"))
    if norm_power == 0 and avg_power > 0:
        norm_power = avg_power  # Fallback als NP ontbreekt

    # ── Max Power (met fallback naar nested icu_intervals) ─────────────────
    max_power = safe_int(val("max_power", "maxWatts", "icu_max_watts", "max_watts",
                             "p_max", "icu_pm_p_max"))
    max_power_source = "instant"

    if max_power == 0:
        # Probeer max uit interval splits (intervals.js logica)
        icu_intervals = a.get("icu_intervals") or []
        if isinstance(icu_intervals, list) and icu_intervals:
            interval_max = max((safe_int(i.get("max_watts")) for i in icu_intervals), default=0)
            if interval_max > 0:
                max_power = interval_max
                max_power_source = "interval"

    # ── Best Efforts ───────────────────────────────────────────────────────
    best_1min = safe_int(val("Best1minpower", "best_1min", "best_1min_power", "w1min"))
    if best_1min > MAX_SANE_1MIN_POWER:
        best_1min = 0  # Sanity check (intervals.js: filter insane values)

    if max_power == 0 and best_1min > 0:
        max_power = best_1min
        max_power_source = "1min"

    best_5min  = safe_int(val("Best5minpower",  "best_5min",  "best_5min_power",  "w5min",
                              "icu_power_5min"))
    best_10min = safe_int(val("Best10minpower", "best_10min", "best_10min_power", "w10min"))
    best_20min = safe_int(val("Best20minpower", "best_20min", "best_20min_power", "w20min",
                              "icu_power_20min"))
    best_60min = safe_int(val("Best60minpower", "best_60min", "best_60min_power", "w60min",
                              "icu_power_60min"))

    # ── Training Load (TSS equivalent) ────────────────────────────────────
    load = safe_int(val("icu_training_load", "training_load", "trainingLoad",
                        "power_load", "hr_load", "trimp", "tss"))

    # ── RPE (1–10) ─────────────────────────────────────────────────────────
    rpe_raw = val("icu_rpe", "perceived_exertion", "feel", "session_rpe", "rpe")
    rpe = safe_int(rpe_raw) if rpe_raw is not None else None
    if rpe is not None and not (1 <= rpe <= 10):
        rpe = None  # Buiten geldig bereik → weggooien

    # ── Hartslag ───────────────────────────────────────────────────────────
    avg_hr = safe_int(val("icu_average_heartrate", "average_heartrate", "average_heart_rate",
                          "avg_hr", "icu_avg_hr", "avg_heartrate"))
    max_hr = safe_int(val("max_heartrate", "max_heart_rate", "icu_max_heartrate",
                          "icu_max_hr", "max_hr", "athlete_max_hr"))

    # ── Werk (kJ) ──────────────────────────────────────────────────────────
    work_kj = safe_float(val("work", "total_work", "kilojoules", "energy"))
    if work_kj == 0:
        joules = safe_float(val("icu_joules", "joules"))
        if joules > 0:
            work_kj = joules / 1000
        elif avg_power > 0 and moving_time > 0:
            work_kj = (avg_power * moving_time) / 1000  # Benadering
    work_kj = round(work_kj)

    # ── FTP & eFTP ─────────────────────────────────────────────────────────
    ftp  = safe_int(val("icu_ftp", "icu_pm_ftp", "ftp", "cp"))
    eftp = safe_int(val("icu_eftp"))

    # ── Altitude ───────────────────────────────────────────────────────────
    avg_altitude = safe_int(val("average_altitude", "avgAltitude", "avg_altitude"))
    max_altitude = safe_int(val("maximum_altitude", "maxAltitude", "max_altitude"))

    return {
        "id":               activity_id,
        "date":             date,
        "type":             activity_type,
        "title":            title,
        "moving_time":      moving_time,
        "distance_km":      distance_km,
        "elevation_m":      elevation,
        "avg_speed_kmh":    avg_speed_kmh,
        "avg_power_w":      avg_power,
        "norm_power_w":     norm_power,
        "max_power_w":      max_power,
        "max_power_source": max_power_source,
        "best_1min_w":      best_1min,
        "best_5min_w":      best_5min,
        "best_10min_w":     best_10min,
        "best_20min_w":     best_20min,
        "best_60min_w":     best_60min,
        "tss":              load,
        "rpe":              rpe,
        "avg_hr_bpm":       avg_hr,
        "max_hr_bpm":       max_hr,
        "work_kj":          work_kj,
        "ftp_w":            ftp,
        "eftp_w":           eftp,
        "avg_altitude_m":   avg_altitude,
        "max_altitude_m":   max_altitude,
    }


# ── Fetch functies ────────────────────────────────────────────────────────────

def fetch_activities():
    print("📡 Activiteiten ophalen...")
    raw = _get("activities", {
        "oldest": START_DATE.strftime(FMT),
        "newest": END_DATE.strftime(FMT),
    })

    # Filter op fietsactiviteiten met zinvolle data
    rides = [
        normalize_activity(a) for a in raw
        if a.get("type") in CYCLING_TYPES
    ]
    rides = [r for r in rides if r["distance_km"] > 0 or r["moving_time"] > 0 or r["avg_power_w"] > 0]
    rides.sort(key=lambda r: r["date"], reverse=True)

    _save("activities.json", rides)
    return rides


def fetch_wellness():
    print("📡 Wellness (CTL/ATL/TSB) ophalen...")
    raw = _get("wellness", {
        "oldest": START_DATE.strftime(FMT),
        "newest": END_DATE.strftime(FMT),
    })

    cleaned = []
    for w in raw:
        ctl = safe_float(w.get("ctl"))
        atl = safe_float(w.get("atl"))
        if ctl == 0:
            continue
        cleaned.append({
            "date":       w.get("id"),   # Wellness ID is de datum YYYY-MM-DD
            "ctl":        round(ctl, 1),
            "atl":        round(atl, 1) if atl else None,
            "tsb":        round(ctl - atl, 1) if atl else None,
            "rhr_bpm":    w.get("restingHR"),
            "hrv":        w.get("hrv"),
            "weight_kg":  w.get("weight"),
            "sleep_secs": w.get("sleepSecs"),
        })

    _save("wellness.json", cleaned)
    return cleaned


def fetch_power_curve():
    print("📡 Power curve ophalen...")
    try:
        curve = _get("power-curves", {
            "oldest":  START_DATE.strftime(FMT),
            "newest":  END_DATE.strftime(FMT),
            "filters": "type:Ride|VirtualRide|GravelRide",
        })
        _save("power_curve.json", curve)
        return curve
    except Exception as e:
        print(f"  ⚠️  Power curve niet beschikbaar: {e}")
        _save("power_curve.json", [])
        return []


def build_summary(activities: list, wellness: list):
    print("📊 Samenvatting berekenen...")

    total_distance  = sum(a["distance_km"] for a in activities)
    total_elevation = sum(a["elevation_m"] for a in activities)
    total_rides     = len(activities)
    total_hours     = sum(a["moving_time"] for a in activities) / 3600
    total_kj        = sum(a["work_kj"] for a in activities)

    best_20min = max((a["best_20min_w"] for a in activities if a["best_20min_w"] > 0), default=0)
    best_60min = max((a["best_60min_w"] for a in activities if a["best_60min_w"] > 0), default=0)

    latest_eftp = next((a["eftp_w"] for a in activities if a["eftp_w"] > 0), 0)

    latest_w = wellness[-1] if wellness else {}

    summary = {
        "updated_at":           datetime.now().isoformat(),
        "period_start":         START_DATE.strftime(FMT),
        "period_end":           END_DATE.strftime(FMT),
        "total_rides":          total_rides,
        "total_distance_km":    round(total_distance, 1),
        "total_elevation_m":    round(total_elevation),
        "total_hours":          round(total_hours, 1),
        "total_work_kj":        total_kj,
        "best_20min_power_w":   best_20min,
        "best_60min_power_w":   best_60min,
        "latest_eftp_w":        latest_eftp,
        "current_ctl":          latest_w.get("ctl"),
        "current_atl":          latest_w.get("atl"),
        "current_tsb":          latest_w.get("tsb"),
        "current_weight_kg":    latest_w.get("weight_kg"),
    }

    _save("summary.json", summary)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"\n🚴 Cycling Dashboard — data sync")
    print(f"   Periode: {START_DATE.strftime(FMT)} → {END_DATE.strftime(FMT)}\n")

    activities = fetch_activities()
    wellness   = fetch_wellness()
    fetch_power_curve()
    build_summary(activities, wellness)

    print(f"\n🎉 Klaar! {len(activities)} ritten opgeslagen in /{DATA_DIR}/")
