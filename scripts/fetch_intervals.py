"""
Intervals.icu data fetcher voor cycling dashboard.
Haalt activiteiten, wellness (CTL/ATL/TSB) en power curves op.
"""

import os
import json
import requests
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

# ── Config ────────────────────────────────────────────────────────────────────
API_KEY      = os.environ["INTERVALS_API_KEY"]
ATHLETE_ID   = os.environ["INTERVALS_ATHLETE_ID"]
BASE_URL     = "https://intervals.icu/api/v1/athlete"
HEADERS      = {"Authorization": f"Basic {_b64(f'API_KEY:{API_KEY}')}"}
DATA_DIR     = "data"

# Datumbereik: afgelopen 12 maanden
END_DATE   = datetime.today()
START_DATE = END_DATE - relativedelta(months=12)
FMT        = "%Y-%m-%d"


def _b64(s: str) -> str:
    import base64
    return base64.b64encode(s.encode()).decode()


def _get(endpoint: str, params: dict = None) -> dict | list:
    url = f"{BASE_URL}/{ATHLETE_ID}/{endpoint}"
    r = requests.get(url, headers=HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _save(filename: str, data) -> None:
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ Saved {path}  ({len(data) if isinstance(data, list) else '—'} records)")


# ── Activiteiten ──────────────────────────────────────────────────────────────
def fetch_activities():
    activities = _get("activities", {
        "oldest": START_DATE.strftime(FMT),
        "newest": END_DATE.strftime(FMT),
    })

    # Filter op cycling types
    cycling_types = {"Ride", "VirtualRide", "GravelRide", "MountainBikeRide", "EBikeRide"}
    rides = [a for a in activities if a.get("type") in cycling_types]

    # Bewaar relevante velden
    cleaned = []
    for a in rides:
        cleaned.append({
            "id":           a.get("id"),
            "date":         a.get("start_date_local", "")[:10],
            "name":         a.get("name"),
            "type":         a.get("type"),
            "distance_km":  round((a.get("distance") or 0) / 1000, 2),
            "duration_sec": a.get("moving_time"),
            "elevation_m":  a.get("total_elevation_gain"),
            "avg_power_w":  a.get("average_watts"),
            "np_w":         a.get("weighted_average_watts"),
            "tss":          a.get("training_load"),
            "avg_hr":       a.get("average_heartrate"),
            "avg_speed":    round((a.get("average_speed") or 0) * 3.6, 2),
            "kilojoules":   a.get("kilojoules"),
        })

    _save("activities.json", cleaned)


# ── Wellness (CTL / ATL / TSB) ────────────────────────────────────────────────
def fetch_wellness():
    wellness = _get("wellness", {
        "oldest": START_DATE.strftime(FMT),
        "newest": END_DATE.strftime(FMT),
    })

    cleaned = []
    for w in wellness:
        ctl = w.get("ctl")
        atl = w.get("atl")
        if ctl is None:
            continue
        cleaned.append({
            "date": w.get("id"),   # wellness id is de datum (YYYY-MM-DD)
            "ctl":  round(ctl, 1),
            "atl":  round(atl, 1) if atl else None,
            "tsb":  round(ctl - atl, 1) if (ctl and atl) else None,
            "rhr":  w.get("restingHR"),
            "hrv":  w.get("hrv"),
            "weight": w.get("weight"),
        })

    _save("wellness.json", cleaned)


# ── Power curve (season best MMP) ─────────────────────────────────────────────
def fetch_power_curve():
    # Intervals.icu heeft een endpoint voor fitness/power curves per periode
    curve = _get("power-curves", {
        "oldest":  START_DATE.strftime(FMT),
        "newest":  END_DATE.strftime(FMT),
        "filters": "type:Ride|VirtualRide|GravelRide",
    })
    _save("power_curve.json", curve)


# ── Stats samenvatting ────────────────────────────────────────────────────────
def build_summary():
    with open(os.path.join(DATA_DIR, "activities.json")) as f:
        activities = json.load(f)

    with open(os.path.join(DATA_DIR, "wellness.json")) as f:
        wellness = json.load(f)

    total_distance = sum(a["distance_km"] for a in activities)
    total_elevation = sum((a["elevation_m"] or 0) for a in activities)
    total_rides = len(activities)
    total_hours = sum((a["duration_sec"] or 0) for a in activities) / 3600

    best_power = {}
    for a in activities:
        if a["avg_power_w"]:
            best_power["longest_avg"] = max(best_power.get("longest_avg", 0), a["avg_power_w"])

    latest_wellness = wellness[-1] if wellness else {}

    summary = {
        "updated_at":     datetime.now().isoformat(),
        "period_start":   START_DATE.strftime(FMT),
        "period_end":     END_DATE.strftime(FMT),
        "total_rides":    total_rides,
        "total_distance_km": round(total_distance, 1),
        "total_elevation_m": round(total_elevation),
        "total_hours":    round(total_hours, 1),
        "current_ctl":    latest_wellness.get("ctl"),
        "current_atl":    latest_wellness.get("atl"),
        "current_tsb":    latest_wellness.get("tsb"),
    }

    _save("summary.json", summary)


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"📡 Fetching data: {START_DATE.strftime(FMT)} → {END_DATE.strftime(FMT)}")

    fetch_activities()
    fetch_wellness()
    fetch_power_curve()
    build_summary()

    print("🎉 Done! Data opgeslagen in /data/")
