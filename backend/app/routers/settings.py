"""Administration panel API.

The settings schema is served, not hardcoded in the frontend: the panel renders
whatever `GET /api/settings` describes, so adding a setting is one entry in
app/core/settings_store.FIELDS and nothing else.

Secrets are write-only across this boundary. A PUT accepts a new value; the
response — like every read — reports only whether one is configured and its
last four characters. There is no endpoint that returns a stored credential.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core import settings_store
from app.core.db import get_db
from app.monitor import scheduler
from app.paper import venue
from app.paper.brokers import available_brokers
from app.paper.service import get_system_state, set_kill_switch

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


@router.get("")
def read_settings(db: Session = Depends(get_db)) -> dict:
    state = get_system_state(db)
    return {
        **settings_store.public_view(db),
        "brokers": available_brokers(),
        "monitor": scheduler.status(),
        "kill_switch": {"engaged": state.kill_switch_engaged, "reason": state.kill_switch_reason},
        "trading_mode": "paper",  # constant, not a setting — no API can change it
    }


@router.put("")
def update_settings(body: SettingsUpdate, db: Session = Depends(get_db)) -> dict:
    if not body.values:
        raise HTTPException(status_code=400, detail="no values supplied")
    try:
        changed = settings_store.apply_updates(db, body.values)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"changed": changed, **read_settings(db)}


class KillSwitchIn(BaseModel):
    engaged: bool
    reason: str = ""


@router.post("/kill-switch")
def kill_switch(body: KillSwitchIn, db: Session = Depends(get_db)) -> dict:
    s = set_kill_switch(db, body.engaged, body.reason)
    return {"engaged": s.kill_switch_engaged, "reason": s.kill_switch_reason}


@router.post("/connections/{broker_name}/test")
def test_connection(broker_name: str, db: Session = Depends(get_db)) -> dict:
    """Check stored credentials against the venue. Returns a verdict rather
    than an error status: "the key is wrong" is an expected answer here, and
    nothing in the response contains the credentials themselves."""
    if broker_name not in venue.VENUE_BROKERS:
        raise HTTPException(status_code=404, detail=f"unknown venue broker: {broker_name}")
    return venue.test_connection(db, broker_name)


@router.get("/connections/{broker_name}/reconcile")
def reconcile(broker_name: str, db: Session = Depends(get_db)) -> dict:
    if broker_name not in venue.VENUE_BROKERS:
        raise HTTPException(status_code=404, detail=f"unknown venue broker: {broker_name}")
    return venue.reconcile(db, broker_name)


@router.get("/monitor")
def monitor_status() -> dict:
    return scheduler.status()


@router.post("/monitor/run-once")
def monitor_run_once() -> dict:
    """Run one monitor pass now. Same code path as the loop, so this is also the
    way to exercise polling and exits when the loop is switched off."""
    return scheduler.run_once()


@router.post("/notify/topic")
def generate_topic(db: Session = Depends(get_db)) -> dict:
    """Create a fresh random ntfy topic. Returned once, here, so it can be
    subscribed to on the phone; afterwards it is masked like any secret."""
    from app import notify
    topic = notify.new_topic()
    settings_store.apply_updates(db, {"notify.ntfy_topic": topic})
    server = str(settings_store.resolve(db, "notify.ntfy_server")).rstrip("/")
    return {"topic": topic, "subscribe_url": f"{server}/{topic}"}


@router.post("/notify/test")
def test_notification(db: Session = Depends(get_db)) -> dict:
    from app import notify
    if not settings_store.resolve(db, "notify.enabled"):
        raise HTTPException(status_code=409, detail="les notifications sont désactivées")
    delivered = notify.send(db, "StockStrat · test", "Les notifications fonctionnent. Tu recevras ici le "
                                                     "résumé du soir, les stops et les erreurs.")
    if not delivered:
        raise HTTPException(status_code=502, detail="aucun canal n'a accepté le message (sujet ntfy ou Telegram manquant ?)")
    return {"delivered": delivered}
