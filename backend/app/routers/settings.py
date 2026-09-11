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


@router.get("/monitor")
def monitor_status() -> dict:
    return scheduler.status()


@router.post("/monitor/run-once")
def monitor_run_once() -> dict:
    """Run one monitor pass now. Same code path as the loop, so this is also the
    way to exercise polling and exits when the loop is switched off."""
    return scheduler.run_once()
