from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.paper.service import get_system_state, set_kill_switch

router = APIRouter(prefix="/api/system", tags=["system"])


class KillSwitchIn(BaseModel):
    engaged: bool
    reason: str = ""


@router.get("/state")
def state(db: Session = Depends(get_db)) -> dict:
    s = get_system_state(db)
    return {"kill_switch_engaged": s.kill_switch_engaged, "kill_switch_reason": s.kill_switch_reason}


@router.post("/kill-switch")
def kill_switch(body: KillSwitchIn, db: Session = Depends(get_db)) -> dict:
    s = set_kill_switch(db, body.engaged, body.reason)
    return {"kill_switch_engaged": s.kill_switch_engaged, "kill_switch_reason": s.kill_switch_reason}
