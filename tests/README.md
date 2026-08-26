# ☁️ CabinGuard AI — Precipitation-Aware Vehicle Ventilation Pipeline

> *"Close before it rains. Open before it bakes."*

**CabinGuard AI** is a real-time streaming data pipeline that monitors precipitation events by location and triggers alerts so you can remotely close (or open) your car windows before weather ruins your interior — or before summer heat turns your cabin into an oven.

Designed with **Tesla owners** specifically in mind (Vent/Close Windows API), but architected as a platform-agnostic alerting engine that can push to any vehicle API, mobile notification, or webhook.

---

## 🌧️ The Problem

You crack your car windows to ventilate on a hot summer day. A storm rolls in. Your interior gets soaked — or worse, you forget they're open overnight. Conversely, on a 95°F day you'd love the car pre-vented *before* you walk out to it.

**CabinGuard AI** solves both sides of that equation with a streaming weather pipeline feeding a configurable alerting engine.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    STREAMING INGESTION LAYER                        │
│                                                                     │
│  OpenWeatherMap API ──►  NiFi / Kafka Producer  ──► Kafka Topic    │
│  Tomorrow.io API    ──►  (weather.raw.stream)                       │
│  MQTT Broker (IoT)  ──►  NiFi Flow / MQTT Bridge                   │
│  NWS/NOAA WebSocket ──►  Direct Kafka Ingest                        │
└─────────────────────────┬───────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────────┐
│                    PROCESSING LAYER (Spark/Python)                  │
│                                                                     │
│  Kafka Consumer → Spark Streaming / Python processor                │
│  - Parse precipitation probability, intensity, type                 │
│  - Geofence matching (car location vs. storm polygon)               │
│  - Threshold evaluation (configurable per user/vehicle)             │
│  - State machine: CLEAR → WATCH → WARN → ALERT → RESOLVED          │
└─────────────────────────┬───────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────────────┐
│                    ALERTING & ACTION LAYER                          │
│                                                                     │
│  Flask-RESTful API ──► Push Notification (FCM/APNs)                 │
│                   ──► Tesla Fleet API (vent/close windows)          │
│                   ──► SMS/Email (Twilio/SendGrid)                   │
│                   ──► Webhook (custom integrations)                 │
│                   ──► MQTT publish (IoT device feedback)            │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 📡 Streaming Variables

This pipeline is designed for **experimentation** — multiple weather data streams can be toggled independently via config. Here is the full variable inventory:

### Tier 1 — Core Precipitation Signals
| Variable | Source | Notes |
|---|---|---|
| `precip_probability` | OpenWeatherMap, Tomorrow.io | 0–100%; primary alert trigger |
| `precip_intensity_mm_hr` | Tomorrow.io, NWS | mm/hour; distinguishes drizzle from downpour |
| `precip_type` | Tomorrow.io | `rain`, `snow`, `sleet`, `freezing_rain` |
| `radar_reflectivity_dbz` | NOAA NEXRAD (WebSocket) | dBZ > 20 = light rain; > 40 = heavy |
| `precip_accumulation_1hr` | OpenWeatherMap | Cumulative mm in next 60 min |

### Tier 2 — Contextual / Leading Indicators
| Variable | Source | Notes |
|---|---|---|
| `cloud_cover_pct` | OpenWeatherMap | Rising trend precedes precip |
| `humidity_rh` | OpenWeatherMap, IoT sensor | Rapid rise can precede storm |
| `dew_point_c` | Tomorrow.io | High dew point + instability = storm risk |
| `wind_speed_ms` | OpenWeatherMap | Gusts can drive rain into open windows |
| `wind_direction_deg` | OpenWeatherMap | For directional window management |
| `pressure_hpa` | OpenWeatherMap | Rapid drop = approaching front |
| `pressure_tendency_3hr` | NWS | Rate of change more telling than absolute |
| `lightning_strike_distance_km` | Blitzortung (WebSocket) | Free real-time lightning data |
| `storm_cell_distance_km` | Tomorrow.io Storm Events | Distance to nearest convective cell |

### Tier 3 — Heat / Comfort Variables (Ventilation Use Case)
| Variable | Source | Notes |
|---|---|---|
| `temp_c` | OpenWeatherMap | For "it's too hot, crack windows" logic |
| `uv_index` | OpenWeatherMap | Solar load proxy |
| `cabin_temp_c` | IoT (MQTT) | Optional: OBD-II or BLE sensor in car |
| `solar_irradiance_wm2` | Tomorrow.io | Direct solar load on vehicle |
| `feels_like_c` | OpenWeatherMap | Heat index aware |

### Tier 4 — Experimental / Advanced
| Variable | Source | Notes |
|---|---|---|
| `nwp_model_run` | NOAA GFS/HRRR | Numerical weather prediction grid |
| `goes_satellite_ir` | NOAA GOES-18 | Infrared cloud tops — storm intensity |
| `mesonet_obs` | Iowa Environmental Mesonet | Ground-truth surface obs |
| `personal_wx_station` | Weather Underground PWS | Hyperlocal; nearest 1–2 mi |
| `air_quality_index` | AirNow / OpenWeatherMap | Bonus: close windows for AQI spikes too |

---

## 🚀 Quickstart

### Prerequisites

```bash
python 3.11+
docker + docker-compose
Apache Kafka (via Docker)
Apache NiFi (optional, for flow-based ingestion)
```

### Setup

```bash
git clone https://github.com/readesie/cabinguard-ai.git
cd cabinguard-ai

# Copy and configure environment
cp config/config.example.yaml config/config.yaml
# Edit config.yaml: add API keys, vehicle ID, alert thresholds

# Spin up Kafka + supporting services
docker-compose up -d

# Install Python dependencies
pip install -r requirements.txt

# Start the ingestion producer
python src/ingestion/weather_producer.py

# Start the stream processor
python src/processing/stream_processor.py

# Start the alert API
python src/api/alert_api.py
```

---

## ⚙️ Configuration

`config/config.yaml` controls which streams are active and what thresholds trigger alerts:

```yaml
# See config/config.example.yaml for full documentation
streams:
  openweathermap:
    enabled: true
    api_key: YOUR_KEY
    poll_interval_seconds: 60

  tomorrow_io:
    enabled: true
    api_key: YOUR_KEY

  noaa_websocket:
    enabled: false   # experimental

  mqtt_iot:
    enabled: false   # requires local broker

alert_thresholds:
  precip_probability_pct: 40      # alert if > this
  precip_intensity_mm_hr: 0.5     # alert if > this
  cabin_temp_c: 38                # alert if car interior > this (open windows)
  lightning_distance_km: 30       # alert if storm within this radius

vehicles:
  - id: YOUR_TESLA_VIN
    name: "Tesla Model Y"
    api: tesla_fleet
    actions:
      on_rain_alert: close_windows
      on_heat_alert: vent_windows
```

---

## 📁 Repository Structure

```
cabinguard-ai/
├── README.md
├── docker-compose.yml
├── requirements.txt
├── config/
│   ├── config.example.yaml       # Template — copy to config.yaml
│   └── stream_variables.yaml     # Full variable registry with enable/disable flags
├── src/
│   ├── ingestion/
│   │   ├── weather_producer.py   # Kafka producer: OpenWeatherMap + Tomorrow.io
│   │   ├── mqtt_bridge.py        # MQTT → Kafka bridge (IoT cabin sensor)
│   │   ├── noaa_ws_client.py     # NOAA WebSocket client (experimental)
│   │   └── blitzortung_client.py # Lightning data client (experimental)
│   ├── processing/
│   │   ├── stream_processor.py   # Main Kafka consumer + alert logic
│   │   ├── geofence.py           # Location matching utilities
│   │   ├── state_machine.py      # CLEAR→WATCH→WARN→ALERT→RESOLVED
│   │   └── variable_registry.py  # Dynamic stream variable loader
│   ├── alerting/
│   │   ├── notifier.py           # Push / SMS / email dispatcher
│   │   ├── tesla_client.py       # Tesla Fleet API integration
│   │   └── webhook.py            # Generic outbound webhook
│   └── api/
│       └── alert_api.py          # Flask-RESTful status + control API
├── tests/
│   ├── test_stream_processor.py
│   ├── test_geofence.py
│   └── test_state_machine.py
├── docs/
│   ├── streaming_variables.md    # Full variable reference
│   ├── tesla_integration.md      # Tesla Fleet API setup guide
│   └── architecture.md           # Detailed architecture diagrams
└── .github/
    └── workflows/
        └── ci.yml
```

---

## 🔑 Skills Demonstrated

This project draws directly from the NorthStar portfolio's engineering stack:

| Skill | Application |
|---|---|
| **Apache Kafka** | Core message bus for all weather streams |
| **Apache NiFi** | Optional visual flow design for ingestion routing |
| **Apache Spark / PySpark** | Streaming processor (Spark Structured Streaming mode) |
| **Python / Pandas / PyArrow** | Data parsing, threshold evaluation, geofencing |
| **Flask-RESTful** | Alert API and vehicle control endpoint |
| **MQTT / Mosquitto** | IoT cabin temperature sensor bridge |
| **WebSockets** | NOAA and Blitzortung real-time feed clients |
| **REST APIs** | OpenWeatherMap, Tomorrow.io, Tesla Fleet API |
| **Docker** | Kafka, Zookeeper, NiFi containerization |
| **Apache Airflow** | Optional: scheduled batch forecast pre-fetch |
| **IoT / ThingsBoard** | Optional: dashboard for stream variable monitoring |
| **JWT / OAuth2** | Tesla Fleet API authentication |

---

## 🧪 Experimental Notes

The `stream_variables.yaml` file is the lab notebook for this project. Each variable has an `enabled` flag and an `experimental` flag. The intent is to run the pipeline with only Tier 1 variables in production while Tier 2–4 variables accumulate data for correlation analysis — eventually feeding a lightweight ML model (scikit-learn or LSTM) to predict "windows will need closing in the next 20 minutes" with higher precision than any single threshold.

---

## 🌟 Easter Egg

The North Star (Polaris) stays fixed while everything else moves. This pipeline keeps your car dry and comfortable no matter where it's parked. *Find your bearing.*

---

## 📄 License

MIT License. See [LICENSE](LICENSE).

---

*Part of the [NorthStar Portfolio](https://github.com/readesie) — enterprise data engineering discipline applied to real-world problems.*
