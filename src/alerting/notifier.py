"""
CabinGuard AI — Notifier
src/alerting/notifier.py

Dispatches alerts to one or more channels based on config.
Each channel is independently enabled. Channels currently supported:
  - push_notification (FCM)
  - sms (Twilio)
  - email (SendGrid)
  - webhook (generic HTTP POST)

Add new channels by implementing the _send_<channel> pattern.
"""

import json
import logging

import requests

from src.processing.state_machine import AlertState

log = logging.getLogger("notifier")

# Only send push/SMS/email for these states (skip WATCH — too noisy)
NOTIFY_STATES = {AlertState.WARN, AlertState.ALERT, AlertState.RESOLVED}


class Notifier:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def send(self, subject: str, body: str, state: AlertState):
        if state not in NOTIFY_STATES:
            return

        cfg = self.cfg

        if cfg.get("push_notification", {}).get("enabled"):
            self._send_fcm(subject, body, cfg["push_notification"])

        if cfg.get("sms", {}).get("enabled"):
            self._send_sms(body, cfg["sms"])

        if cfg.get("email", {}).get("enabled"):
            self._send_email(subject, body, cfg["email"])

        if cfg.get("webhook", {}).get("enabled"):
            self._send_webhook(subject, body, state, cfg["webhook"])

    # ─────────────────────────────────────────────────────────
    # Channel implementations
    # ─────────────────────────────────────────────────────────

    def _send_fcm(self, title: str, body: str, cfg: dict):
        try:
            r = requests.post(
                "https://fcm.googleapis.com/fcm/send",
                headers={
                    "Authorization": f"key={cfg['server_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "to": cfg.get("device_token", "/topics/cabinguard"),
                    "notification": {"title": title, "body": body},
                    "data": {"source": "cabinguard-ai"},
                },
                timeout=10,
            )
            r.raise_for_status()
            log.info("FCM push sent: %s", title)
        except Exception as exc:
            log.error("FCM push failed: %s", exc)

    def _send_sms(self, body: str, cfg: dict):
        try:
            r = requests.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{cfg['account_sid']}/Messages.json",
                auth=(cfg["account_sid"], cfg["auth_token"]),
                data={
                    "From": cfg["from_number"],
                    "To": cfg["to_number"],
                    "Body": body[:160],  # SMS length limit
                },
                timeout=10,
            )
            r.raise_for_status()
            log.info("SMS sent to %s", cfg["to_number"])
        except Exception as exc:
            log.error("SMS send failed: %s", exc)

    def _send_email(self, subject: str, body: str, cfg: dict):
        try:
            r = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                json={
                    "personalizations": [{"to": [{"email": cfg["to"]}]}],
                    "from": {"email": cfg["from"]},
                    "subject": subject,
                    "content": [{"type": "text/plain", "value": body}],
                },
                timeout=10,
            )
            r.raise_for_status()
            log.info("Email sent to %s", cfg["to"])
        except Exception as exc:
            log.error("Email send failed: %s", exc)

    def _send_webhook(self, subject: str, body: str, state: AlertState, cfg: dict):
        try:
            payload = {
                "subject": subject,
                "body": body,
                "state": state.value,
                "source": "cabinguard-ai",
            }
            headers = {"Content-Type": "application/json"}
            if cfg.get("secret"):
                headers["X-CabinGuard-Secret"] = cfg["secret"]
            r = requests.post(cfg["url"], json=payload, headers=headers, timeout=10)
            r.raise_for_status()
            log.info("Webhook delivered to %s", cfg["url"])
        except Exception as exc:
            log.error("Webhook delivery failed: %s", exc)
