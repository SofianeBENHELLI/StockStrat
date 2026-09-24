"""Push notifications to the owner's phone.

Two channels, both optional and both configured in Administration →
Notifications:

- **ntfy** (default): no account. The app posts to a topic; the ntfy app on the
  phone subscribes to it. Topics on the public server are readable by anyone
  who knows the name, so the name is a long random string and is treated as a
  secret. A self-hosted ntfy server works the same way.
- **Telegram**: a bot token and a chat id.

Sending never raises and never blocks for long: a notification that fails is
logged and dropped — a phone being unreachable must not stop a trading
decision.
"""
from __future__ import annotations

import json
import logging
import secrets
import urllib.request

from sqlalchemy.orm import Session

from app.core import settings_store

log = logging.getLogger("notify")
TIMEOUT_S = 5

PRIORITY = {"low": 2, "default": 3, "high": 4, "urgent": 5}


def new_topic() -> str:
    return f"stockstrat-{secrets.token_urlsafe(18).replace('_', '').replace('-', '').lower()[:22]}"


def _post(url: str, data: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 - URL comes from settings
        resp.read()


def send(db: Session, title: str, message: str, priority: str = "default", tags: list[str] | None = None,
         kind: str = "info") -> list[str]:
    """Deliver to every configured channel. Returns the channels that accepted it."""
    v = settings_store.resolve_all(db)
    if not v.get("notify.enabled"):
        return []
    toggle = {"exit": "notify.on_exits", "error": "notify.on_errors", "fill": "notify.on_fills",
              "summary": "notify.daily_summary", "promote": "notify.on_exits"}.get(kind)
    if toggle and not v.get(toggle, True):
        return []

    delivered = []
    topic = v.get("notify.ntfy_topic")
    if topic:
        server = str(v.get("notify.ntfy_server") or "https://ntfy.sh").rstrip("/")
        # JSON publishing, not the header form: HTTP headers are latin-1, so a
        # title like "Clôture" or "Stratège" would arrive garbled on the phone.
        try:
            _post(server, json.dumps({"topic": topic, "title": title, "message": message,
                                      "priority": PRIORITY.get(priority, 3), "tags": tags or []},
                                     ensure_ascii=False).encode("utf-8"),
                  {"Content-Type": "application/json; charset=utf-8"})
            delivered.append("ntfy")
        except Exception as exc:
            log.warning("ntfy notification failed: %s", exc)

    token, chat = v.get("notify.telegram_bot_token"), v.get("notify.telegram_chat_id")
    if token and chat:
        try:
            _post(f"https://api.telegram.org/bot{token}/sendMessage",
                  json.dumps({"chat_id": chat, "text": f"{title}\n{message}"}).encode(),
                  {"Content-Type": "application/json"})
            delivered.append("telegram")
        except Exception as exc:
            log.warning("telegram notification failed: %s", exc)
    return delivered
