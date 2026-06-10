"""
CabinGuard AI — Weather Producer
src/ingestion/weather_producer.py

Polls OpenWeatherMap and Tomorrow.io on configurable intervals,
normalizes responses to a common schema, and publishes to Kafka.

Each enabled stream source runs in its own thread so poll intervals
are independent. New sources are added by subclassing WeatherSource.
"""

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import requests
import yaml
from kafka import KafkaProducer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
log = logging.getLogger("weather_producer")


# ─────────────────────────────────────────────────────────────
# Canonical weather event schema
# ─────────────────────────────────────────────────────────────
def build_event(
    source: str,
    location_id: str,
    lat: float,
    lon: float,
    variables: dict[str, Any],
) -> dict:
    return {
        "schema_version": "1.0",
        "source": source,
        "location_id": location_id,
        "lat": lat,
        "lon": lon,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "variables": variables,
    }


# ─────────────────────────────────────────────────────────────
# Base class for all stream sources
# ─────────────────────────────────────────────────────────────
class WeatherSource(ABC):
    def __init__(self, cfg: dict, locations: list[dict], producer: KafkaProducer):
        self.cfg = cfg
        self.locations = locations
        self.producer = producer
        self.topic = cfg["kafka_topic"]
        self.interval = cfg.get("poll_interval_seconds", 60)
        self.log = logging.getLogger(self.__class__.__name__)

    @abstractmethod
    def fetch(self, location: dict) -> dict | None:
        """Fetch raw data for a single location; return normalized variables dict."""

    def run_once(self):
        for loc in self.locations:
            try:
                variables = self.fetch(loc)
                if variables:
                    event = build_event(
                        source=self.cfg.get("name", "unknown"),
                        location_id=loc["id"],
                        lat=loc["lat"],
                        lon=loc["lon"],
                        variables=variables,
                    )
                    self.producer.send(
                        self.topic,
                        key=loc["id"].encode(),
                        value=json.dumps(event).encode(),
                    )
                    self.log.info("Published %s event for location=%s", self.topic, loc["id"])
            except Exception as exc:
                self.log.error("Fetch failed for %s/%s: %s", self.topic, loc["id"], exc)

    def run_loop(self):
        self.log.info("Starting poll loop every %ds → topic=%s", self.interval, self.topic)
        while True:
            self.run_once()
            time.sleep(self.interval)


# ─────────────────────────────────────────────────────────────
# OpenWeatherMap source
# ─────────────────────────────────────────────────────────────
class OpenWeatherMapSource(WeatherSource):
    BASE = "https://api.openweathermap.org/data/3.0/onecall"

    def fetch(self, location: dict) -> dict | None:
        params = {
            "lat": location["lat"],
            "lon": location["lon"],
            "appid": self.cfg["api_key"],
            "units": "metric",
            "exclude": "minutely,daily,alerts",
        }
        r = requests.get(self.BASE, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        current = data.get("current", {})
        hourly = data.get("hourly", [{}])[0]

        rain_1h = current.get("rain", {}).get("1h", 0.0)
        snow_1h = current.get("snow", {}).get("1h", 0.0)

        # Tomorrow.io / OWM don't expose precip probability on current;
        # pull from first hourly entry which has pop.
        pop = hourly.get("pop", 0.0) * 100  # 0–1 → 0–100

        return {
            "precip_probability": round(pop, 1),
            "precip_intensity_mm_hr": round(rain_1h + snow_1h, 2),
            "precip_accumulation_1hr": round(rain_1h, 2),
            "temp_c": current.get("temp"),
            "feels_like_c": current.get("feels_like"),
            "humidity_rh": current.get("humidity"),
            "cloud_cover_pct": current.get("clouds"),
            "wind_speed_ms": current.get("wind_speed"),
            "wind_direction_deg": current.get("wind_deg"),
            "pressure_hpa": current.get("pressure"),
            "uv_index": current.get("uvi"),
        }


# ─────────────────────────────────────────────────────────────
# Tomorrow.io source
# ─────────────────────────────────────────────────────────────
PRECIP_TYPE_MAP = {0: "none", 1: "rain", 2: "snow", 3: "freezing_rain", 4: "sleet"}


class TomorrowIOSource(WeatherSource):
    BASE = "https://api.tomorrow.io/v4/weather/realtime"

    def fetch(self, location: dict) -> dict | None:
        params = {
            "location": f"{location['lat']},{location['lon']}",
            "apikey": self.cfg["api_key"],
            "units": "metric",
        }
        r = requests.get(self.BASE, params=params, timeout=10)
        r.raise_for_status()
        vals = r.json().get("data", {}).get("values", {})

        return {
            "precip_probability": vals.get("precipitationProbability"),
            "precip_intensity_mm_hr": vals.get("precipitationIntensity"),
            "precip_type": PRECIP_TYPE_MAP.get(vals.get("precipitationType", 0), "unknown"),
            "dew_point_c": vals.get("dewPoint"),
            "solar_irradiance_wm2": vals.get("solarGHI"),
            "temp_c": vals.get("temperature"),
            "humidity_rh": vals.get("humidity"),
            "wind_speed_ms": vals.get("windSpeed"),
            "pressure_hpa": vals.get("pressureSurfaceLevel"),
        }


# ─────────────────────────────────────────────────────────────
# Simulated source — generates synthetic data for local dev/testing
# without consuming real API quota
# ─────────────────────────────────────────────────────────────
import math
import random


class SimulatedSource(WeatherSource):
    """
    Cycles through a synthetic weather pattern useful for testing
    the full pipeline without live API keys.
    Produces a slow-building rain scenario every ~20 minutes.
    """
    _tick = 0

    def fetch(self, location: dict) -> dict | None:
        SimulatedSource._tick += 1
        t = SimulatedSource._tick
        # Build up precip probability over time then reset
        cycle = t % 40
        pop = min(100, cycle * 3.0)
        intensity = max(0.0, (cycle - 20) * 0.2) if cycle > 20 else 0.0

        return {
            "precip_probability": round(pop + random.uniform(-2, 2), 1),
            "precip_intensity_mm_hr": round(intensity + random.uniform(0, 0.1), 2),
            "precip_type": "rain" if pop > 40 else "none",
            "temp_c": round(28 + 5 * math.sin(t / 10), 1),
            "feels_like_c": round(32 + 4 * math.sin(t / 10), 1),
            "humidity_rh": round(60 + cycle * 0.5),
            "cloud_cover_pct": round(min(100, cycle * 2.5)),
            "wind_speed_ms": round(3 + random.uniform(0, 4), 1),
            "pressure_hpa": round(1013 - cycle * 0.3, 1),
            "uv_index": max(0, round(8 - cycle * 0.1, 1)),
        }


# ─────────────────────────────────────────────────────────────
# Source registry — add new sources here
# ─────────────────────────────────────────────────────────────
SOURCE_REGISTRY = {
    "openweathermap": OpenWeatherMapSource,
    "tomorrow_io": TomorrowIOSource,
    "simulated": SimulatedSource,
}


# ─────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────
def main():
    with open("config/config.yaml") as f:
        cfg = yaml.safe_load(f)

    producer = KafkaProducer(
        bootstrap_servers=cfg["kafka"]["bootstrap_servers"],
        retries=5,
        acks="all",
    )
    locations = cfg["locations"]
    threads = []

    for source_name, source_cfg in cfg.get("streams", {}).items():
        if not source_cfg.get("enabled", False):
            log.info("Source %s is disabled — skipping", source_name)
            continue

        source_cls = SOURCE_REGISTRY.get(source_name)
        if source_cls is None:
            log.warning("No implementation for source '%s' — skipping", source_name)
            continue

        source_cfg["name"] = source_name
        source = source_cls(source_cfg, locations, producer)
        t = threading.Thread(target=source.run_loop, name=source_name, daemon=True)
        threads.append(t)
        t.start()

    if not threads:
        log.error("No sources enabled. Check config/config.yaml → streams section.")
        return

    log.info("%d source thread(s) running. Ctrl-C to stop.", len(threads))
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        log.info("Shutting down weather producer.")
        producer.flush()


if __name__ == "__main__":
    main()
