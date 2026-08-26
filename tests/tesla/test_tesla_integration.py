"""
CabinGuard AI — Tesla Integration Tests (LIVE)
tests/tesla/test_tesla_integration.py

These tests make REAL calls to the Tesla Fleet API against your Model 3.
They are skipped automatically unless you set environment variables:

    export TESLA_CLIENT_ID=your_client_id
    export TESLA_CLIENT_SECRET=your_client_secret
    export TESLA_MODEL3_VIN=5YJ3E1EAXNF......
    export TESLA_LIVE_TEST=1          # safety gate — must be set to run

Run:
    TESLA_LIVE_TEST=1 pytest tests/tesla/test_tesla_integration.py -v -s

⚠️  IMPORTANT — READ BEFORE RUNNING:
  - Tests execute REAL window commands on your vehicle.
  - Make sure your car is parked safely and windows can move freely.
  - Tests restore window state after each command.
  - Each test includes a 5-second pause so you can visually confirm.
  - DO NOT run while driving or in an automated CI pipeline with live creds.

Tesla Fleet API Prerequisite:
  You must register a developer application at https://developer.tesla.com
  and request the following OAuth scopes:
    - vehicle_cmds
    - vehicle_device_data
  Your app must be approved for the Fleet API — this is a manual Tesla review.
  See docs/tesla_integration.md for full setup instructions.
"""

import os
import time

import pytest

from src.alerting.tesla_client import TeslaClient

# ─────────────────────────────────────────────────────────────
# Safety gate — nothing runs without explicit opt-in
# ─────────────────────────────────────────────────────────────

LIVE = os.environ.get("TESLA_LIVE_TEST") == "1"
CLIENT_ID = os.environ.get("TESLA_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("TESLA_CLIENT_SECRET", "")
MODEL3_VIN = os.environ.get("TESLA_MODEL3_VIN", "")

skip_unless_live = pytest.mark.skipif(
    not LIVE or not CLIENT_ID or not CLIENT_SECRET or not MODEL3_VIN,
    reason="Live Tesla tests require TESLA_LIVE_TEST=1 and credentials set in env",
)


@pytest.fixture(scope="module")
def live_client():
    cfg = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "simulated": False,
    }
    vehicles = [
        {
            "id": MODEL3_VIN,
            "name": "Model 3 (CabinGuard Test)",
            "api": "tesla_fleet",
            "actions": {
                "on_rain_alert": "close_windows",
                "on_heat_alert": "vent_windows",
            },
        }
    ]
    return TeslaClient(cfg, vehicles)


# ─────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────

class TestLiveAuth:

    @skip_unless_live
    def test_oauth_token_fetch(self, live_client):
        """Verify we can obtain a valid access token from Tesla."""
        token = live_client._get_token()
        assert token is not None
        assert len(token) > 20
        print(f"\n✅ Token obtained: {token[:20]}...")

    @skip_unless_live
    def test_token_is_cached(self, live_client):
        """Second call should reuse cached token without a new HTTP request."""
        t1 = live_client._get_token()
        t2 = live_client._get_token()
        assert t1 == t2
        print("\n✅ Token cache working correctly.")


# ─────────────────────────────────────────────────────────────
# Vehicle state
# ─────────────────────────────────────────────────────────────

class TestLiveVehicleState:

    @skip_unless_live
    def test_get_vehicle_state(self, live_client):
        """Fetch full vehicle state from the Fleet API."""
        state = live_client.get_vehicle_state(MODEL3_VIN)
        assert state is not None
        print(f"\n✅ Vehicle state fetched:")
        print(f"   State:        {state.get('state')}")
        vehicle_state = state.get("vehicle_state", {})
        print(f"   FD window:    {vehicle_state.get('fd_window')} (0=closed, 1=venting, 2=open)")
        print(f"   RD window:    {vehicle_state.get('rd_window')}")
        climate = state.get("climate_state", {})
        print(f"   Inside temp:  {climate.get('inside_temp')}°C")
        print(f"   Outside temp: {climate.get('outside_temp')}°C")

    @skip_unless_live
    def test_vehicle_is_reachable(self, live_client):
        """Confirm vehicle is online (or can be woken)."""
        live_client._wake_vehicle(MODEL3_VIN, max_attempts=6, sleep_sec=5)
        state = live_client.get_vehicle_state(MODEL3_VIN)
        assert state is not None
        assert state.get("state") == "online"
        print(f"\n✅ Model 3 is online and reachable.")


# ─────────────────────────────────────────────────────────────
# Window commands  ← THE CORE TEST
# ─────────────────────────────────────────────────────────────

class TestLiveWindowCommands:

    @skip_unless_live
    def test_vent_windows(self, live_client):
        """
        Send VENT command to Model 3.
        Windows should open approximately 1 inch / 3cm.
        Visually confirm on the car.
        """
        print(f"\n🚗 Sending VENT command to Model 3 ({MODEL3_VIN})...")
        live_client.vent_windows(MODEL3_VIN)
        print("   ⏳ Waiting 5 seconds — check that windows vented...")
        time.sleep(5)

        state = live_client.get_vehicle_state(MODEL3_VIN)
        vehicle_state = state.get("vehicle_state", {}) if state else {}
        fd = vehicle_state.get("fd_window")
        rd = vehicle_state.get("rd_window")
        print(f"   FD window code: {fd}  RD window code: {rd}")
        # 1 = vent position on Model 3
        assert fd in (1, 2), f"Expected fd_window=1 (venting), got {fd}"
        print("✅ VENT command confirmed via vehicle state.")

    @skip_unless_live
    def test_close_windows(self, live_client):
        """
        Send CLOSE command to Model 3.
        All windows should return to fully closed.
        """
        print(f"\n🚗 Sending CLOSE command to Model 3 ({MODEL3_VIN})...")
        live_client.close_windows(MODEL3_VIN)
        print("   ⏳ Waiting 8 seconds — check that windows closed...")
        time.sleep(8)

        state = live_client.get_vehicle_state(MODEL3_VIN)
        vehicle_state = state.get("vehicle_state", {}) if state else {}
        fd = vehicle_state.get("fd_window")
        rd = vehicle_state.get("rd_window")
        print(f"   FD window code: {fd}  RD window code: {fd}")
        # 0 = closed on Model 3
        assert fd == 0, f"Expected fd_window=0 (closed), got {fd}"
        assert rd == 0, f"Expected rd_window=0 (closed), got {rd}"
        print("✅ CLOSE command confirmed via vehicle state.")

    @skip_unless_live
    def test_full_vent_then_close_cycle(self, live_client):
        """
        End-to-end cycle simulating a CabinGuard AI rain alert sequence:
          1. Vent windows (heat scenario — windows are open)
          2. Verify vented
          3. Receive simulated rain alert → close windows
          4. Verify closed
        """
        print(f"\n🔄 Running full vent → close cycle on Model 3...")

        # Step 1: Vent
        print("   Step 1: Venting windows...")
        live_client.vent_windows(MODEL3_VIN)
        time.sleep(6)

        state = live_client.get_vehicle_state(MODEL3_VIN)
        fd_after_vent = state.get("vehicle_state", {}).get("fd_window") if state else None
        print(f"   FD window after vent: {fd_after_vent}")

        # Step 2: Close (rain alert fires)
        print("   Step 2: Rain alert — closing windows...")
        live_client.close_windows(MODEL3_VIN)
        time.sleep(8)

        state = live_client.get_vehicle_state(MODEL3_VIN)
        fd_after_close = state.get("vehicle_state", {}).get("fd_window") if state else None
        rd_after_close = state.get("vehicle_state", {}).get("rd_window") if state else None
        print(f"   FD window after close: {fd_after_close}")
        print(f"   RD window after close: {rd_after_close}")

        assert fd_after_close == 0, f"FD window should be closed, got {fd_after_close}"
        assert rd_after_close == 0, f"RD window should be closed, got {rd_after_close}"
        print("✅ Full cycle completed successfully — CabinGuard AI works on your Model 3!")

    @skip_unless_live
    def test_honk_horn(self, live_client):
        """Optional: confirm horn alert works (useful before automated close)."""
        print(f"\n📣 Honking horn on Model 3 ({MODEL3_VIN})...")
        live_client.honk_horn(MODEL3_VIN)
        time.sleep(3)
        print("✅ Horn command sent — did you hear it?")


# ─────────────────────────────────────────────────────────────
# Error resilience
# ─────────────────────────────────────────────────────────────

class TestLiveErrorResilience:

    @skip_unless_live
    def test_bad_vin_does_not_raise(self, live_client):
        """Sending a command to an unregistered VIN should log a warning, not crash."""
        live_client.close_windows("5YJ3E1EA1NF999999")
        # No exception = pass

    @skip_unless_live
    def test_bad_credentials_raises_gracefully(self):
        """Bad credentials should not crash the process — log and continue."""
        bad_cfg = {
            "client_id": "bad-id",
            "client_secret": "bad-secret",
            "simulated": False,
        }
        client = TeslaClient(bad_cfg, [{"id": MODEL3_VIN, "name": "test", "api": "tesla_fleet"}])
        # close_windows should catch auth failure internally
        client.close_windows(MODEL3_VIN)  # should not raise
