"""
CabinGuard AI — State Machine Tests
tests/test_state_machine.py
"""

import pytest
from src.processing.state_machine import AlertState, LocationStateMachine

THRESHOLDS = {
    "precip_probability_pct": {"warn": 30, "critical": 50},
    "precip_intensity_mm_hr": {"warn": 0.2, "critical": 2.0},
    "cabin_temp_c": {"warn": 35, "critical": 45},
    "lightning_distance_km": {"warn": 50, "critical": 20},
    "wind_speed_ms": {"warn": 10, "critical": 15},
}


def make_sm():
    return LocationStateMachine(
        location_id="test",
        thresholds=THRESHOLDS,
        cooldown_minutes=0,
    )


def test_initial_state_is_clear():
    sm = make_sm()
    assert sm.state == AlertState.CLEAR


def test_transitions_to_watch_on_low_precip_prob():
    sm = make_sm()
    sm.evaluate({"precip_probability": 22})
    assert sm.state == AlertState.WATCH


def test_transitions_to_warn():
    sm = make_sm()
    sm.evaluate({"precip_probability": 35})
    assert sm.state == AlertState.WARN


def test_transitions_to_alert_on_critical():
    sm = make_sm()
    sm.evaluate({"precip_probability": 60})
    assert sm.state == AlertState.ALERT


def test_transitions_to_resolved_after_alert():
    sm = make_sm()
    sm.evaluate({"precip_probability": 60})
    assert sm.state == AlertState.ALERT
    sm.evaluate({"precip_probability": 5, "cloud_cover_pct": 10})
    assert sm.state == AlertState.RESOLVED


def test_callback_fires_on_transition():
    events = []
    sm = LocationStateMachine(
        location_id="test",
        thresholds=THRESHOLDS,
        cooldown_minutes=0,
        on_transition=lambda e: events.append(e),
    )
    sm.evaluate({"precip_probability": 60})
    assert len(events) == 1
    assert events[0].state == AlertState.ALERT


def test_lightning_inverted_threshold():
    sm = make_sm()
    # Lightning at 15km should be critical (lower = more dangerous)
    sm.evaluate({"lightning_strike_distance_km": 15})
    assert sm.state == AlertState.ALERT


def test_cabin_temp_alert():
    sm = make_sm()
    sm.evaluate({"cabin_temp_c": 46})
    assert sm.state == AlertState.ALERT


def test_no_duplicate_state_transition_callback():
    events = []
    sm = LocationStateMachine(
        location_id="test",
        thresholds=THRESHOLDS,
        cooldown_minutes=0,
        on_transition=lambda e: events.append(e),
    )
    # Two consecutive ALERT evaluations should not fire callback twice
    sm.evaluate({"precip_probability": 60})
    sm.evaluate({"precip_probability": 65})
    assert len(events) == 1
