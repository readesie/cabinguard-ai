"""
CabinGuard AI — Tesla Fleet API Client
src/alerting/tesla_client.py

Wraps the Tesla Fleet API for window vent and close commands.
Uses the refresh token flow (authorization_code grant) for personal
vehicle access — the correct flow for single-owner use.

Vehicle ID is Tesla's internal numeric ID (not the VIN).
Set TESLA_VEHICLE_ID env var or pass in config.

Tesla Fleet API reference:
  https://developer.tesla.com/docs/fleet-api
"""

import logging
import os
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger("tesla_client")


class TeslaClient:
    TOKEN_URL = "https://auth.tesla.com/oauth2/v3/token"
    API_BASE = "https://fleet-api.prd.na.vn.cloud.tesla.com"

    def __init__(self, cfg: dict, vehicles: list[dict]):
        self.cfg = cfg
        # vehicles keyed by numeric id (string or int both accepted)
        self.vehicles = {str(v["id"]): v for v in vehicles if v.get("api") == "tesla_fleet"}
        self.simulated = cfg.get("simulated", True)
        self._token: str | None = None
        self._token_expiry: float = 0.0

        # Refresh token — from config or env var
        self._refresh_token = (
            cfg.get("refresh_token")
            or os.environ.get("TESLA_REFRESH_TOKEN", "")
        )

        if self.simulated:
            log.info("TeslaClient initialized in SIMULATED mode — no commands will be sent.")
        else:
            if not self._refresh_token:
                log.error("No TESLA_REFRESH_TOKEN found — live commands will fail.")
            log.info("TeslaClient initialized in LIVE mode for vehicle(s): %s", list(self.vehicles.keys()))

    # ─────────────────────────────────────────────────────────
    # Public actions
    # ─────────────────────────────────────────────────────────

    def close_windows(self, vehicle_id: str):
        self._dispatch(str(vehicle_id), "window_control", {"command": "close"}, "CLOSE WINDOWS")

    def vent_windows(self, vehicle_id: str):
        self._dispatch(str(vehicle_id), "window_control", {"command": "vent"}, "VENT WINDOWS")

    def honk_horn(self, vehicle_id: str):
        self._dispatch(str(vehicle_id), "honk_horn", {}, "HONK HORN")

    def get_vehicle_state(self, vehicle_id: str) -> dict | None:
        """Retrieve current vehicle state including window positions and cabin temp."""
        vehicle_id = str(vehicle_id)
        if self.simulated:
            return {
                "vehicle_id": vehicle_id,
                "state": "online",
                "vehicle_state": {"fd_window": 0, "rd_window": 0, "fp_window": 0, "rp_window": 0},
                "climate_state": {"inside_temp": 28.5, "outside_temp": 31.0},
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

        log.info("→ Dispatching %s to vehicle %s (Pegasus)", label, vehicle_id)

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
                log.info("✅ %s command succeeded for Pegasus (%s)", label, vehicle_id)
            else:
                log.warning("⚠️ %s command returned non-success: %s", label, result)
        except Exception as exc:
            log.error("Failed to dispatch %s to vehicle %s: %s", label, vehicle_id, exc)

    def _wake_vehicle(self, vehicle_id: str, max_attempts: int = 10, sleep_sec: int = 5):
        """Wake Pegasus before sending commands. Model 3 typically wakes in 15-30s."""
        token = self._get_token()
        url = f"{self.API_BASE}/api/1/vehicles/{vehicle_id}/wake_up"
        headers = {"Authorization": f"Bearer {token}"}
        log.info("Waking Pegasus (this may take up to 30s)...")
        for attempt in range(max_attempts):
            try:
                r = requests.post(url, headers=headers, timeout=10)
                r.raise_for_status()
                state = r.json().get("response", {}).get("state")
                if state == "online":
                    log.info("✅ Pegasus is online.")
                    return
                log.info("Wake attempt %d/%d — state=%s", attempt + 1, max_attempts, state)
                time.sleep(sleep_sec)
            except Exception as exc:
                log.warning("Wake attempt %d failed: %s", attempt + 1, exc)
                time.sleep(sleep_sec)
        log.warning("Pegasus may still be asleep after %d attempts — proceeding anyway.", max_attempts)

    def _get_token(self) -> str:
        """Get a valid user access token, refreshing via refresh_token if needed."""
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        if not self._refresh_token:
            raise RuntimeError(
                "No refresh token available. Set TESLA_REFRESH_TOKEN env var or run "
                "the authorization flow: python tests/tesla/live_test_cli.py"
            )

        log.info("Refreshing Tesla user token via refresh_token flow...")
        r = requests.post(
            self.TOKEN_URL,
            json={
                "grant_type": "refresh_token",
                "client_id": self.cfg.get("client_id") or os.environ.get("TESLA_CLIENT_ID"),
                "client_secret": self.cfg.get("client_secret") or os.environ.get("TESLA_CLIENT_SECRET"),
                "refresh_token": self._refresh_token,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()

        if "access_token" not in data:
            raise RuntimeError(f"Token refresh failed: {data}")

        self._token = data["access_token"]
        self._token_expiry = time.time() + data.get("expires_in", 28800)

        # Tesla issues a new refresh token on each refresh — update it
        if data.get("refresh_token"):
            self._refresh_token = data["refresh_token"]
            log.info("Refresh token rotated — update TESLA_REFRESH_TOKEN env var if this persists.")

        log.info("✅ Tesla token refreshed. Expires in %ds.", data.get("expires_in", 28800))
        return self._token
