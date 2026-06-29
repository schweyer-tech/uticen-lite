#!/usr/bin/env python3
"""One-time snapshot of OpenWeatherMap current conditions → datacenter_weather.csv.

This is NOT a live connector (STRATEGY.md non-goal; see docs/learnings/0025). It is a
single, user-initiated batch of GETs that freezes the response into a local CSV which then
becomes the source of truth for the ``datacenter_weather`` file source. Runs occur only when
a human invokes this script — there is no scheduler, polling, or background refresh.

The data-center site inventory (site_id, city, country, latitude, longitude) is static and
read from the existing CSV; only the live readings (temperature_c, wind_kmh, observed_at) are
re-fetched, so the column shape and row keys are preserved (see docs/learnings/0031).

Usage:
    OPENWEATHERMAP_API_KEY=<key> python tools/refresh_datacenter_weather.py

Get a free key at https://openweathermap.org/api (Current Weather Data endpoint).
"""

from __future__ import annotations

import csv
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

OWM_URL = "https://api.openweathermap.org/data/2.5/weather"
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "datacenter_weather.csv"
HEADER = [
    "site_id", "city", "country", "latitude", "longitude",
    "temperature_c", "wind_kmh", "observed_at",
]


def _fetch_one(lat: str, lon: str, api_key: str) -> dict[str, object]:
    """GET current conditions for one lat/lon (units=metric → °C and m/s)."""
    query = f"lat={lat}&lon={lon}&units=metric&appid={api_key}"
    req = urllib.request.Request(f"{OWM_URL}?{query}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 user-initiated
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = "check your OPENWEATHERMAP_API_KEY" if e.code == 401 else e.reason
        sys.exit(f"OpenWeatherMap returned HTTP {e.code} for {lat},{lon}: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach OpenWeatherMap for {lat},{lon}: {e.reason}")


def _reading(payload: dict[str, object]) -> tuple[str, str, str]:
    """Extract (temperature_c, wind_kmh, observed_at) from an OWM response."""
    main = payload.get("main") or {}
    wind = payload.get("wind") or {}
    temp_c = round(float(main["temp"]), 1)  # type: ignore[index]
    wind_kmh = round(float(wind.get("speed", 0.0)) * 3.6, 1)  # m/s → km/h
    observed_at = datetime.fromtimestamp(int(payload["dt"]), UTC).strftime("%Y-%m-%dT%H:%M")  # type: ignore[arg-type]
    return str(temp_c), str(wind_kmh), observed_at


def main() -> None:
    api_key = os.environ.get("OPENWEATHERMAP_API_KEY", "").strip()
    if not api_key:
        sys.exit("Set OPENWEATHERMAP_API_KEY (free key at https://openweathermap.org/api).")

    with CSV_PATH.open(newline="", encoding="utf-8") as fh:
        sites = list(csv.DictReader(fh))

    rows: list[list[str]] = []
    for site in sites:
        temp_c, wind_kmh, observed_at = _reading(_fetch_one(
            site["latitude"], site["longitude"], api_key))
        rows.append([
            site["site_id"], site["city"], site["country"],
            site["latitude"], site["longitude"], temp_c, wind_kmh, observed_at,
        ])
        print(f"  {site['site_id']:7} {site['city']:12} {temp_c}°C  {wind_kmh} km/h")

    with CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {CSV_PATH}")


if __name__ == "__main__":
    main()
