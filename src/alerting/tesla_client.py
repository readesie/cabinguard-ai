"""
CabinGuard AI — Tesla Fleet API Client
src/alerting/tesla_client.py

Wraps the Tesla Fleet API for window vent and close commands.
Operates in simulated mode by default — set tesla.simulated: false
in config.yaml and provide real OAuth credentials to send live commands.

Tesla Fleet API reference:
  https://developer.tesla.com/docs/fleet-api
"""

import logging
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger("tesla_client")


class TeslaClient:
    TOKEN_URL = "https://auth.tesla.com/oauth2/v3/token"
    API_BASE = "https://fleet-api.prd.na.vn.cloud.tesla.com"

    def __init__(self, cfg: dict, vehicles: list[dict]):
        self.cfg = cfg
        self.vehicles = {v["id"]: v for v in vehicles if v.get("api") == "tesla_fleet"}
        self.simulated = cfg.get("simulated", True)
        self._token: str | None = None
        self._token_expiry: float = 0.0

        if self.simulated:
            log.info("TeslaClient initialized in SIMULATED mode — no commands will be sent.")
        else:
            log.info("TeslaClient initialized in LIVE mode.")

    # ─────────────────────────────────────────────────────────
    # Public actions
    # ─────────────────────────────────────────────────────────

    def close_windows(self, vehicle_id: str):
        self._dispatch(vehicle_id, "window_control", {"command": "close"}, "CLOSE WINDOWS")

    def vent_windows(self, vehicle_id: str):
        self._dispatch(vehicle_id, "window_control", {"command": "vent"}, "VENT WINDOWS")

    def honk_horn(self, vehicle_id: str):
        """Optional: honk to alert the owner before remote action."""
        self._dispatch(vehicle_id, "honk_horn", {}, "HONK HORN")

    def get_vehicle_state(self, vehicle_id: str) -> dict | None:
        """Retrieve current vehicle state including window positions."""
        if self.simulated:
            return {
                "vehicle_id": vehicle_id,
                "state": "online",
                "windows": {"fd": "open", "rd": "open", "fp": "closed", "rp": "closed"},
                "simulated": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        try:
            token = self._get_token()
            url = f"{self.API_BASE}/api/1/vehicles/{vehicle_id}/vehicle_data"
            headers = {"Authorization": f"Bearer {token}"}
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            return r.json().get("response", {})
        except Exception as exc:
            log.error("Failed to get vehicle state for %s: %s", vehicle_id, exc)
            return None

    # ─────────────────────────────────────────────────────────
    # Internal
    # ─────────────────────────────────────────────────────────

    def _dispatch(self, vehicle_id: str, command: str, params: dict, label: str):
        if vehicle_id not in self.vehicles:
            log.warning("Vehicle %s not registered in config — skipping %s", vehicle_id, label)
            return

        log.info("→ Dispatching %s to vehicle %s", label, vehicle_id)

        if self.simulated:
            log.info(
                "[SIMULATED] Would send command=%s params=%s to vehicle=%s",
                command, params, vehicle_id,
            )
            return

        try:
            self._wake_vehicle(vehicle_id)
            token = self._get_token()
            url = f"{self.API_BASE}/api/1/vehicles/{vehicle_id}/command/{command}"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            r = requests.post(url, json=params, headers=headers, timeout=15)
            r.raise_for_status()
            result = r.json().get("response", {})
            if result.get("result"):
                log.info("✅ %s command succeeded for vehicle %s", label, vehicle_id)
            else:
                log.warning("⚠️ %s command returned non-success: %s", label, result)
        except Exception as exc:
            log.error("Failed to dispatch %s to vehicle %s: %s", label, vehicle_id, exc)

    def _wake_vehicle(self, vehicle_id: str, max_attempts: int = 5, sleep_sec: int = 5):
        """Wake a sleeping vehicle before sending commands."""
        token = self._get_token()
        url = f"{self.API_BASE}/api/1/vehicles/{vehicle_id}/wake_up"
        headers = {"Authorization": f"Bearer {token}"}
        for attempt in range(max_attempts):
            try:
                r = requests.post(url, headers=headers, timeout=10)
                r.raise_for_status()
                state = r.json().get("response", {}).get("state")
                if state == "online":
                    log.info("Vehicle %s is online.", vehicle_id)
                    return
                log.info("Wake attempt %d/%d — state=%s", attempt + 1, max_attempts, state)
                time.sleep(sleep_sec)
            except Exception as exc:
                log.warning("Wake attempt %d failed: %s", attempt + 1, exc)
                time.sleep(sleep_sec)
        log.warning("Vehicle %s may still be asleep after %d attempts.", vehicle_id, max_attempts)

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        log.info("Refreshing Tesla OAuth token...")
        r = requests.post(
            self.TOKEN_URL,
            json={
                "grant_type": "client_credentials",
                "client_id": self.cfg.get("client_id"),
                "client_secret": self.cfg.get("client_secret"),
                "scope": "vehicle_cmds vehicle_device_data",
                "audience": self.cfg.get("api_base_url", self.API_BASE),
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        log.info("Tesla token refreshed. Expires in %ds.", data.get("expires_in", 3600))
        return self._token
