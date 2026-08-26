"""
CabinGuard AI — Tesla Client Unit Tests
tests/tesla/test_tesla_client_unit.py

Full mock coverage of every Tesla Fleet API operation.
No real credentials or vehicle required — all HTTP is intercepted.

Run:
    pytest tests/tesla/test_tesla_client_unit.py -v
"""

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from src.alerting.tesla_client import TeslaClient


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────

MODEL3_VIN = "5YJ3E1EA1NF000001"

BASE_CFG = {
    "client_id": "fake-client-id",
    "client_secret": "fake-client-secret",
    "api_base_url": "https://fleet-api.prd.na.vn.cloud.tesla.com",
    "simulated": False,
}

VEHICLES = [
    {
        "id": MODEL3_VIN,
        "name": "My Model 3",
        "api": "tesla_fleet",
        "actions": {
            "on_rain_alert": "close_windows",
            "on_heat_alert": "vent_windows",
        },
    }
]

FAKE_TOKEN_RESPONSE = {
    "access_token": "fake-access-token-abc123",
    "expires_in": 3600,
    "token_type": "Bearer",
}


def make_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """Helper: build a mock requests.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status = MagicMock()
    return mock


def make_client(simulated: bool = False) -> TeslaClient:
    cfg = {**BASE_CFG, "simulated": simulated}
    return TeslaClient(cfg, VEHICLES)


# ─────────────────────────────────────────────────────────────
# Auth / token tests
# ─────────────────────────────────────────────────────────────

class TestTokenManagement:

    @patch("src.alerting.tesla_client.requests.post")
    def test_fetches_token_on_first_call(self, mock_post):
        mock_post.return_value = make_response(FAKE_TOKEN_RESPONSE)
        client = make_client()
        token = client._get_token()
        assert token == "fake-access-token-abc123"
        assert mock_post.call_count == 1

    @patch("src.alerting.tesla_client.requests.post")
    def test_reuses_cached_token(self, mock_post):
        mock_post.return_value = make_response(FAKE_TOKEN_RESPONSE)
        client = make_client()
        client._get_token()
        client._get_token()
        # Token should only be fetched once
        assert mock_post.call_count == 1

    @patch("src.alerting.tesla_client.requests.post")
    def test_refreshes_expired_token(self, mock_post):
        mock_post.return_value = make_response(FAKE_TOKEN_RESPONSE)
        client = make_client()
        client._get_token()
        # Manually expire it
        client._token_expiry = time.time() - 1
        client._get_token()
        assert mock_post.call_count == 2

    @patch("src.alerting.tesla_client.requests.post")
    def test_token_request_sends_correct_scopes(self, mock_post):
        mock_post.return_value = make_response(FAKE_TOKEN_RESPONSE)
        client = make_client()
        client._get_token()
        call_kwargs = mock_post.call_args
        body = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs[0][1]
        assert "vehicle_cmds" in body.get("scope", "")
        assert "vehicle_device_data" in body.get("scope", "")


# ─────────────────────────────────────────────────────────────
# Wake vehicle tests
# ─────────────────────────────────────────────────────────────

class TestWakeVehicle:

    @patch("src.alerting.tesla_client.requests.post")
    def test_wake_succeeds_on_first_attempt(self, mock_post):
        # First call: token, second call: wake_up → online
        mock_post.side_effect = [
            make_response(FAKE_TOKEN_RESPONSE),
            make_response({"response": {"state": "online"}}),
        ]
        client = make_client()
        client._wake_vehicle(MODEL3_VIN, sleep_sec=0)
        assert mock_post.call_count == 2

    @patch("src.alerting.tesla_client.time.sleep", return_value=None)
    @patch("src.alerting.tesla_client.requests.post")
    def test_wake_retries_until_online(self, mock_post, mock_sleep):
        mock_post.side_effect = [
            make_response(FAKE_TOKEN_RESPONSE),
            make_response({"response": {"state": "asleep"}}),
            make_response({"response": {"state": "asleep"}}),
            make_response({"response": {"state": "online"}}),
        ]
        client = make_client()
        client._wake_vehicle(MODEL3_VIN, max_attempts=5, sleep_sec=1)
        # Should have called wake_up 3 times before getting online
        wake_calls = [c for c in mock_post.call_args_list if "wake_up" in str(c)]
        assert len(wake_calls) == 3

    @patch("src.alerting.tesla_client.time.sleep", return_value=None)
    @patch("src.alerting.tesla_client.requests.post")
    def test_wake_gives_up_after_max_attempts(self, mock_post, mock_sleep):
        # Always returns asleep — should not raise, just warn and continue
        mock_post.side_effect = [make_response(FAKE_TOKEN_RESPONSE)] + \
            [make_response({"response": {"state": "asleep"}})] * 5
        client = make_client()
        # Should not raise
        client._wake_vehicle(MODEL3_VIN, max_attempts=5, sleep_sec=0)


# ─────────────────────────────────────────────────────────────
# Window command tests
# ─────────────────────────────────────────────────────────────

class TestWindowCommands:

    @patch("src.alerting.tesla_client.time.sleep", return_value=None)
    @patch("src.alerting.tesla_client.requests.post")
    def test_close_windows_sends_correct_command(self, mock_post, mock_sleep):
        mock_post.side_effect = [
            make_response(FAKE_TOKEN_RESPONSE),                          # token
            make_response({"response": {"state": "online"}}),            # wake
            make_response(FAKE_TOKEN_RESPONSE),                          # token (dispatch)
            make_response({"response": {"result": True, "reason": ""}}), # command
        ]
        client = make_client()
        client.close_windows(MODEL3_VIN)

        # Find the window_control command call
        command_call = [c for c in mock_post.call_args_list if "window_control" in str(c)]
        assert len(command_call) == 1
        body = command_call[0][1].get("json", {})
        assert body.get("command") == "close"

    @patch("src.alerting.tesla_client.time.sleep", return_value=None)
    @patch("src.alerting.tesla_client.requests.post")
    def test_vent_windows_sends_correct_command(self, mock_post, mock_sleep):
        mock_post.side_effect = [
            make_response(FAKE_TOKEN_RESPONSE),
            make_response({"response": {"state": "online"}}),
            make_response(FAKE_TOKEN_RESPONSE),
            make_response({"response": {"result": True, "reason": ""}}),
        ]
        client = make_client()
        client.vent_windows(MODEL3_VIN)

        command_call = [c for c in mock_post.call_args_list if "window_control" in str(c)]
        assert len(command_call) == 1
        body = command_call[0][1].get("json", {})
        assert body.get("command") == "vent"

    @patch("src.alerting.tesla_client.time.sleep", return_value=None)
    @patch("src.alerting.tesla_client.requests.post")
    def test_honk_horn_sends_correct_command(self, mock_post, mock_sleep):
        mock_post.side_effect = [
            make_response(FAKE_TOKEN_RESPONSE),
            make_response({"response": {"state": "online"}}),
            make_response(FAKE_TOKEN_RESPONSE),
            make_response({"response": {"result": True}}),
        ]
        client = make_client()
        client.honk_horn(MODEL3_VIN)

        honk_call = [c for c in mock_post.call_args_list if "honk_horn" in str(c)]
        assert len(honk_call) == 1

    def test_command_skipped_for_unregistered_vehicle(self, caplog):
        client = make_client()
        # VIN not in VEHICLES
        client.close_windows("5YJ3E1EA1NF999999")
        assert "not registered" in caplog.text

    @patch("src.alerting.tesla_client.time.sleep", return_value=None)
    @patch("src.alerting.tesla_client.requests.post")
    def test_command_logs_warning_on_non_success_response(self, mock_post, mock_sleep, caplog):
        mock_post.side_effect = [
            make_response(FAKE_TOKEN_RESPONSE),
            make_response({"response": {"state": "online"}}),
            make_response(FAKE_TOKEN_RESPONSE),
            make_response({"response": {"result": False, "reason": "window_obstruction"}}),
        ]
        client = make_client()
        client.close_windows(MODEL3_VIN)
        assert "non-success" in caplog.text or "window_obstruction" in caplog.text


# ─────────────────────────────────────────────────────────────
# Vehicle state tests
# ─────────────────────────────────────────────────────────────

class TestVehicleState:

    @patch("src.alerting.tesla_client.requests.post")
    @patch("src.alerting.tesla_client.requests.get")
    def test_get_vehicle_state_returns_data(self, mock_get, mock_post):
        mock_post.return_value = make_response(FAKE_TOKEN_RESPONSE)
        mock_get.return_value = make_response({
            "response": {
                "id": MODEL3_VIN,
                "state": "online",
                "vehicle_state": {
                    "fd_window": 0,   # 0=closed, 1=venting, 2=open
                    "rd_window": 0,
                    "fp_window": 0,
                    "rp_window": 0,
                },
                "climate_state": {
                    "inside_temp": 28.5,
                    "outside_temp": 31.0,
                },
            }
        })
        client = make_client()
        state = client.get_vehicle_state(MODEL3_VIN)
        assert state is not None
        assert state.get("state") == "online"

    @patch("src.alerting.tesla_client.requests.post")
    @patch("src.alerting.tesla_client.requests.get")
    def test_get_vehicle_state_returns_none_on_error(self, mock_get, mock_post):
        mock_post.return_value = make_response(FAKE_TOKEN_RESPONSE)
        mock_get.side_effect = Exception("Network timeout")
        client = make_client()
        state = client.get_vehicle_state(MODEL3_VIN)
        assert state is None


# ─────────────────────────────────────────────────────────────
# Simulated mode tests
# ─────────────────────────────────────────────────────────────

class TestSimulatedMode:

    def test_simulated_close_windows_does_not_call_http(self):
        with patch("src.alerting.tesla_client.requests.post") as mock_post:
            client = make_client(simulated=True)
            client.close_windows(MODEL3_VIN)
            # Token endpoint should never be called in simulated mode
            assert mock_post.call_count == 0

    def test_simulated_vent_windows_does_not_call_http(self):
        with patch("src.alerting.tesla_client.requests.post") as mock_post:
            client = make_client(simulated=True)
            client.vent_windows(MODEL3_VIN)
            assert mock_post.call_count == 0

    def test_simulated_get_vehicle_state_returns_mock_data(self):
        client = make_client(simulated=True)
        state = client.get_vehicle_state(MODEL3_VIN)
        assert state is not None
        assert state.get("simulated") is True
        assert "windows" in state

    def test_simulated_mode_logs_intent(self, caplog):
        import logging
        with caplog.at_level(logging.INFO, logger="tesla_client"):
            client = make_client(simulated=True)
            client.close_windows(MODEL3_VIN)
        assert "SIMULATED" in caplog.text
