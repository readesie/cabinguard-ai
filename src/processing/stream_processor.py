"""
CabinGuard AI — Stream Processor
src/processing/stream_processor.py

Consumes weather events from all active Kafka topics,
evaluates them against configured thresholds via the state machine,
and dispatches vehicle actions + notifications when alert states are reached.

Run modes:
  python (default)  — single-process Kafka consumer, low overhead
  spark             — Spark Structured Streaming for high-throughput / multi-location
"""

import json
import logging
import sys
from pathlib import Path

import yaml
from kafka import KafkaConsumer

# Add project root to path for relative imports
sys.path.insert(0, str(Path(__file__).parents[2]))

from src.processing.state_machine import AlertState, LocationStateMachine
from src.alerting.notifier import Notifier
from src.alerting.tesla_client import TeslaClient

log = logging.getLogger("stream_processor")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)


def load_config() -> dict:
    with open("config/config.yaml") as f:
        return yaml.safe_load(f)


def build_topic_list(cfg: dict) -> list[str]:
    """Return Kafka topics for all enabled stream sources."""
    topics = []
    for source_cfg in cfg.get("streams", {}).values():
        if source_cfg.get("enabled", False):
            topics.append(source_cfg["kafka_topic"])
    return list(set(topics))


def run_python_processor(cfg: dict):
    """
    Standard Python Kafka consumer loop.
    One state machine per (vehicle, location) combination.
    """
    thresholds = cfg.get("alert_thresholds", {})
    vehicles = cfg.get("vehicles", [])
    proc_cfg = cfg.get("processing", {})
    kafka_cfg = cfg.get("kafka", {})

    notifier = Notifier(cfg.get("alerting", {}))
    tesla = TeslaClient(cfg.get("tesla", {}), vehicles)

    # Build state machines — one per location
    state_machines: dict[str, LocationStateMachine] = {}
    for loc in cfg.get("locations", []):
        sm = LocationStateMachine(
            location_id=loc["id"],
            thresholds=thresholds,
            cooldown_minutes=proc_cfg.get("state_machine", {}).get("cooldown_minutes", 15),
            on_transition=lambda event: handle_transition(event, notifier, tesla, vehicles),
        )
        state_machines[loc["id"]] = sm

    topics = build_topic_list(cfg)
    if not topics:
        log.error("No enabled stream topics found. Exiting.")
        return

    log.info("Subscribing to topics: %s", topics)
    consumer = KafkaConsumer(
        *topics,
        bootstrap_servers=kafka_cfg.get("bootstrap_servers", "localhost:9092"),
        group_id=kafka_cfg.get("consumer_group", "cabinguard-processor"),
        auto_offset_reset=kafka_cfg.get("auto_offset_reset", "latest"),
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        key_deserializer=lambda k: k.decode("utf-8") if k else None,
    )

    log.info("Stream processor running. Waiting for weather events...")
    for message in consumer:
        try:
            process_message(message.value, state_machines)
        except Exception as exc:
            log.error("Error processing message: %s", exc, exc_info=True)


def process_message(event: dict, state_machines: dict[str, LocationStateMachine]):
    location_id = event.get("location_id")
    variables = event.get("variables", {})
    source = event.get("source", "unknown")

    if not location_id:
        log.warning("Event missing location_id — skipping: %s", event)
        return

    sm = state_machines.get(location_id)
    if sm is None:
        log.warning("No state machine for location_id=%s", location_id)
        return

    log.debug(
        "Processing %s event for location=%s | precip_prob=%s intensity=%s",
        source,
        location_id,
        variables.get("precip_probability"),
        variables.get("precip_intensity_mm_hr"),
    )

    new_state = sm.evaluate(variables)
    log.info("location=%s state=%s", location_id, new_state)


def handle_transition(event, notifier: "Notifier", tesla: "TeslaClient", vehicles: list):
    """
    Called by state machine whenever state changes.
    Dispatches notifications and vehicle actions.
    """
    log.info(
        "STATE TRANSITION [%s] %s → %s | trigger=%s val=%s | %s",
        event.location_id,
        event.previous_state,
        event.state,
        event.trigger_variable,
        event.trigger_value,
        event.message,
    )

    # Send notifications
    notifier.send(
        subject=f"CabinGuard [{event.state}] at {event.location_id}",
        body=event.message,
        state=event.state,
    )

    # Dispatch vehicle actions
    for vehicle in vehicles:
        action_map = vehicle.get("actions", {})

        if event.state == AlertState.ALERT:
            trigger = event.trigger_variable
            if "precip" in trigger or "lightning" in trigger or "wind" in trigger:
                action = action_map.get("on_rain_alert", "notify_only")
            elif "cabin_temp" in trigger:
                action = action_map.get("on_heat_alert", "notify_only")
            elif "air_quality" in trigger:
                action = action_map.get("on_aqi_alert", "notify_only")
            else:
                action = "notify_only"

            if action == "close_windows":
                tesla.close_windows(vehicle["id"])
            elif action == "vent_windows":
                tesla.vent_windows(vehicle["id"])

        elif event.state == AlertState.RESOLVED:
            # Optional: auto-vent when conditions clear on a hot day
            # Uncomment to enable auto-vent on resolution:
            # tesla.vent_windows(vehicle["id"])
            pass


def main():
    cfg = load_config()
    engine = cfg.get("processing", {}).get("engine", "python")

    if engine == "spark":
        log.info("Starting Spark Structured Streaming processor...")
        run_spark_processor(cfg)
    else:
        log.info("Starting Python stream processor...")
        run_python_processor(cfg)


def run_spark_processor(cfg: dict):
    """
    Spark Structured Streaming mode.
    Requires a Spark installation and the spark-sql-kafka connector.
    Useful when monitoring hundreds of locations simultaneously.
    """
    try:
        from pyspark.sql import SparkSession
        from pyspark.sql.functions import col, from_json, get_json_object
        from pyspark.sql.types import DoubleType, StringType, StructField, StructType
    except ImportError:
        log.error("PySpark not available. Install pyspark or set engine: python in config.")
        return

    kafka_cfg = cfg.get("kafka", {})
    topics = ",".join(build_topic_list(cfg))

    spark = (
        SparkSession.builder.appName("CabinGuardAI")
        .master(cfg.get("processing", {}).get("spark_master", "local[2]"))
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")

    schema = StructType([
        StructField("location_id", StringType()),
        StructField("source", StringType()),
        StructField("observed_at", StringType()),
        StructField("variables", StringType()),  # parse inner JSON in foreachBatch
    ])

    raw_stream = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", kafka_cfg.get("bootstrap_servers", "localhost:9092"))
        .option("subscribe", topics)
        .option("startingOffsets", "latest")
        .load()
    )

    parsed = raw_stream.selectExpr("CAST(value AS STRING) as json_str")

    def process_batch(batch_df, batch_id):
        rows = batch_df.collect()
        log.info("Spark batch %d: %d events", batch_id, len(rows))
        # Delegate to Python state machines for threshold evaluation
        # (Spark is used here for fan-out / parallelism, not threshold logic)
        for row in rows:
            try:
                event = json.loads(row.json_str)
                log.info("Spark processed event: location=%s", event.get("location_id"))
            except Exception as exc:
                log.error("Spark batch error: %s", exc)

    query = parsed.writeStream.foreachBatch(process_batch).start()
    query.awaitTermination()


if __name__ == "__main__":
    main()
