"""
CabinGuard AI — MQTT → Kafka Bridge
src/ingestion/mqtt_bridge.py

Subscribes to an MQTT broker (e.g. Mosquitto) for IoT cabin sensor data
— temperature, humidity — and republishes to a Kafka topic in the
canonical weather event schema.

Hardware options for a cabin sensor:
  - Raspberry Pi Zero W + DHT22 sensor
  - ESP32 + DHT22 (MicroPython)
  - Commercial OBD-II BT/WiFi adapter with temp sensor
  - Any BLE/WiFi thermometer with MQTT publish support (e.g. Xiaomi via Home Assistant)

Expected MQTT payload format (from sensor):
  Topic:   cabinguard/cabin/<location_id>
  Payload: {"temp_c": 42.1, "humidity_rh": 55.3, "battery_pct": 87}
"""

import json
import logging
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import yaml
from kafka import KafkaProducer

log = logging.getLogger("mqtt_bridge")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")


def build_event(location_id: str, variables: dict) -> dict:
    return {
        "schema_version": "1.0",
        "source": "mqtt_iot_cabin",
        "location_id": location_id,
        "lat": None,   # cabin sensor doesn't have GPS; matched by location_id
        "lon": None,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "variables": variables,
    }


def main():
    with open("config/config.yaml") as f:
        cfg = yaml.safe_load(f)

    mqtt_cfg = cfg.get("streams", {}).get("mqtt_iot_cabin", {})
    if not mqtt_cfg.get("enabled", False):
        log.info("MQTT bridge is disabled in config. Exiting.")
        return

    kafka_topic = mqtt_cfg.get("kafka_topic", "weather.iot.cabin")
    producer = KafkaProducer(
        bootstrap_servers=cfg["kafka"]["bootstrap_servers"],
        acks="all",
    )

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            log.info("Connected to MQTT broker.")
            client.subscribe(mqtt_cfg.get("topic", "cabinguard/cabin/+"))
        else:
            log.error("MQTT connect failed with code %d", rc)

    def on_message(client, userdata, msg):
        try:
            # Topic pattern: cabinguard/cabin/<location_id>
            parts = msg.topic.split("/")
            location_id = parts[-1] if len(parts) >= 3 else "unknown"

            payload = json.loads(msg.payload.decode())
            variables = {k: payload[k] for k in ("temp_c", "humidity_rh") if k in payload}

            # Rename to match canonical schema
            if "temp_c" in variables:
                variables["cabin_temp_c"] = variables.pop("temp_c")
            if "humidity_rh" in variables:
                variables["cabin_humidity_rh"] = variables.pop("humidity_rh")

            event = build_event(location_id, variables)
            producer.send(kafka_topic, key=location_id.encode(), value=json.dumps(event).encode())
            log.info("MQTT → Kafka: location=%s cabin_temp=%s", location_id, variables.get("cabin_temp_c"))
        except Exception as exc:
            log.error("MQTT message processing error: %s", exc)

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    broker = mqtt_cfg.get("broker_host", "localhost")
    port = mqtt_cfg.get("broker_port", 1883)
    log.info("Connecting to MQTT broker at %s:%d", broker, port)
    client.connect(broker, port, keepalive=60)
    client.loop_forever()


if __name__ == "__main__":
    main()
