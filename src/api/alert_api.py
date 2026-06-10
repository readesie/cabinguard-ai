"""
CabinGuard AI — Alert API
src/api/alert_api.py

Flask-RESTful API exposing:
  GET  /status              — current alert states for all locations
  GET  /status/<location>   — state for one location
  POST /action/<vehicle_id> — manually trigger a window command
  GET  /variables           — list of all streaming variables and enabled status
  GET  /health              — heartbeat

Run: python src/api/alert_api.py
"""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from flask import Flask, jsonify, request
from flask_restful import Api, Resource

sys.path.insert(0, str(Path(__file__).parents[2]))

from src.alerting.tesla_client import TeslaClient
from src.processing.state_machine import AlertState

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("alert_api")

app = Flask(__name__)
api = Api(app)

# In production, shared state would live in Redis or a database.
# For this prototype, a module-level dict suffices.
_state_store: dict[str, str] = {}  # location_id → AlertState value
_cfg: dict = {}
_tesla: TeslaClient | None = None


def load_app():
    global _cfg, _tesla
    with open("config/config.yaml") as f:
        _cfg = yaml.safe_load(f)
    _tesla = TeslaClient(_cfg.get("tesla", {}), _cfg.get("vehicles", []))
    # Initialize all locations to CLEAR
    for loc in _cfg.get("locations", []):
        _state_store[loc["id"]] = AlertState.CLEAR.value


# ─────────────────────────────────────────────────────────────
# Resources
# ─────────────────────────────────────────────────────────────

class HealthResource(Resource):
    def get(self):
        return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}, 200


class StatusResource(Resource):
    def get(self):
        return {
            "locations": [
                {"location_id": loc_id, "state": state}
                for loc_id, state in _state_store.items()
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, 200


class LocationStatusResource(Resource):
    def get(self, location_id: str):
        state = _state_store.get(location_id)
        if state is None:
            return {"error": f"Location '{location_id}' not found"}, 404
        return {"location_id": location_id, "state": state}, 200


class ActionResource(Resource):
    def post(self, vehicle_id: str):
        """
        Manually dispatch a window command.
        Body: {"action": "close_windows" | "vent_windows"}
        """
        data = request.get_json(force=True, silent=True) or {}
        action = data.get("action")

        if action not in ("close_windows", "vent_windows"):
            return {"error": "action must be 'close_windows' or 'vent_windows'"}, 400

        if _tesla is None:
            return {"error": "TeslaClient not initialized"}, 503

        if action == "close_windows":
            _tesla.close_windows(vehicle_id)
        else:
            _tesla.vent_windows(vehicle_id)

        return {
            "vehicle_id": vehicle_id,
            "action": action,
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
            "simulated": _tesla.simulated,
        }, 200


class VariablesResource(Resource):
    def get(self):
        """Return all configured streaming variables and their enabled/experimental flags."""
        variables = []
        for source_name, source_cfg in _cfg.get("streams", {}).items():
            enabled = source_cfg.get("enabled", False)
            experimental = source_cfg.get("experimental", False)
            for var in source_cfg.get("variables", []):
                variables.append({
                    "variable": var,
                    "source": source_name,
                    "enabled": enabled,
                    "experimental": experimental,
                    "kafka_topic": source_cfg.get("kafka_topic"),
                })
        return {"variables": variables, "count": len(variables)}, 200


# ─────────────────────────────────────────────────────────────
# Route registration
# ─────────────────────────────────────────────────────────────
api.add_resource(HealthResource, "/health")
api.add_resource(StatusResource, "/status")
api.add_resource(LocationStatusResource, "/status/<string:location_id>")
api.add_resource(ActionResource, "/action/<string:vehicle_id>")
api.add_resource(VariablesResource, "/variables")


if __name__ == "__main__":
    load_app()
    log.info("Starting CabinGuard AI API on :5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
