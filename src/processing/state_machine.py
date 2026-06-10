"""
CabinGuard AI — Alert State Machine
src/processing/state_machine.py

Manages the lifecycle of a weather alert for a single location:
    CLEAR → WATCH → WARN → ALERT → RESOLVED

Transitions are driven by evaluated threshold results from the
stream processor. The state machine enforces cooldown periods
and tracks which vehicle actions have been dispatched so we
don't spam the Tesla API on every Kafka message.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable


class AlertState(str, Enum):
    CLEAR = "CLEAR"
    WATCH = "WATCH"      # precip probable but not imminent
    WARN = "WARN"        # thresholds at warning level
    ALERT = "ALERT"      # thresholds at critical level — act now
    RESOLVED = "RESOLVED"


@dataclass
class AlertEvent:
    location_id: str
    state: AlertState
    previous_state: AlertState
    trigger_variable: str          # which variable caused the transition
    trigger_value: float
    threshold_level: str           # "warn" or "critical"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    message: str = ""


class LocationStateMachine:
    """
    One state machine instance per monitored location.
    Thread-safe for single-threaded stream processor; add locking if parallel.
    """

    def __init__(
        self,
        location_id: str,
        thresholds: dict,
        cooldown_minutes: int = 15,
        on_transition: Callable[[AlertEvent], None] | None = None,
    ):
        self.location_id = location_id
        self.thresholds = thresholds
        self.cooldown_seconds = cooldown_minutes * 60
        self.on_transition = on_transition

        self.state = AlertState.CLEAR
        self._last_alert_time: datetime | None = None
        self._actions_dispatched: set[str] = set()

    # ─────────────────────────────────────────────────────────
    # Public interface
    # ─────────────────────────────────────────────────────────

    def evaluate(self, variables: dict) -> AlertState:
        """
        Evaluate a new set of weather variables against thresholds.
        Updates internal state and fires on_transition callback if state changes.
        Returns the current state after evaluation.
        """
        severity, trigger_var, trigger_val, level = self._assess_severity(variables)
        new_state = self._next_state(severity)

        if new_state != self.state:
            event = AlertEvent(
                location_id=self.location_id,
                state=new_state,
                previous_state=self.state,
                trigger_variable=trigger_var,
                trigger_value=trigger_val,
                threshold_level=level,
                message=self._build_message(new_state, trigger_var, trigger_val),
            )
            self.state = new_state

            if new_state in (AlertState.WARN, AlertState.ALERT):
                self._last_alert_time = datetime.now(timezone.utc)

            if new_state in (AlertState.RESOLVED, AlertState.CLEAR):
                self._actions_dispatched.clear()

            if self.on_transition:
                self.on_transition(event)

        return self.state

    def needs_action(self, action_key: str) -> bool:
        """
        Returns True if this action (e.g. 'close_windows') has not yet been
        dispatched during the current alert episode and cooldown has not blocked it.
        """
        if action_key in self._actions_dispatched:
            return False
        if self._last_alert_time:
            elapsed = (datetime.now(timezone.utc) - self._last_alert_time).total_seconds()
            if elapsed < self.cooldown_seconds:
                return True  # within alert window, action not yet taken
        return False

    def mark_action_dispatched(self, action_key: str):
        self._actions_dispatched.add(action_key)

    # ─────────────────────────────────────────────────────────
    # Internal logic
    # ─────────────────────────────────────────────────────────

    def _assess_severity(self, variables: dict) -> tuple[int, str, float, str]:
        """
        Returns (severity_score, trigger_variable, trigger_value, level).
        severity: 0=clear, 1=watch, 2=warn, 3=critical
        """
        max_severity = 0
        trigger_var = ""
        trigger_val = 0.0
        trigger_level = "none"

        check_map = {
            "precip_probability": "precip_probability_pct",
            "precip_intensity_mm_hr": "precip_intensity_mm_hr",
            "cabin_temp_c": "cabin_temp_c",
            "lightning_strike_distance_km": "lightning_distance_km",
            "wind_speed_ms": "wind_speed_ms",
            "air_quality_index": "air_quality_index",
        }

        for var_name, threshold_key in check_map.items():
            val = variables.get(var_name)
            if val is None:
                continue

            thresholds = self.thresholds.get(threshold_key, {})
            warn_val = thresholds.get("warn")
            crit_val = thresholds.get("critical")

            # For lightning/distance, lower = more dangerous (inverted logic)
            inverted = "distance" in var_name.lower()

            if inverted:
                if crit_val is not None and val <= crit_val:
                    severity = 3
                    level = "critical"
                elif warn_val is not None and val <= warn_val:
                    severity = 2
                    level = "warn"
                else:
                    severity = 0
                    level = "none"
            else:
                if crit_val is not None and val >= crit_val:
                    severity = 3
                    level = "critical"
                elif warn_val is not None and val >= warn_val:
                    severity = 2
                    level = "warn"
                else:
                    severity = 0
                    level = "none"

            if severity > max_severity:
                max_severity = severity
                trigger_var = var_name
                trigger_val = val
                trigger_level = level

        # Contextual leading-indicator check → WATCH (severity 1)
        if max_severity == 0:
            precip_prob = variables.get("precip_probability", 0)
            cloud_cover = variables.get("cloud_cover_pct", 0)
            if precip_prob >= 20 or cloud_cover >= 70:
                max_severity = 1
                trigger_var = "precip_probability"
                trigger_val = precip_prob
                trigger_level = "watch"

        return max_severity, trigger_var, trigger_val, trigger_level

    def _next_state(self, severity: int) -> AlertState:
        if severity == 0:
            if self.state in (AlertState.ALERT, AlertState.WARN, AlertState.WATCH):
                return AlertState.RESOLVED
            return AlertState.CLEAR
        elif severity == 1:
            return AlertState.WATCH
        elif severity == 2:
            return AlertState.WARN
        else:
            return AlertState.ALERT

    def _build_message(self, state: AlertState, var: str, val: float) -> str:
        msgs = {
            AlertState.WATCH: f"Heads up — {var}={val} suggests rain possible. Monitoring.",
            AlertState.WARN: f"⚠️ Warning — {var}={val} is at warning threshold. Consider closing windows.",
            AlertState.ALERT: f"🚨 Alert — {var}={val} has crossed critical threshold. Close windows now!",
            AlertState.RESOLVED: "✅ Conditions have improved. Windows may be reopened.",
            AlertState.CLEAR: "Weather is clear.",
        }
        return msgs.get(state, "")
