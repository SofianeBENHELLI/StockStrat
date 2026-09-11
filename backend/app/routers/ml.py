from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.ml import inference, train

router = APIRouter(prefix="/api/ml", tags=["ml"])


@router.get("/model")
def get_model() -> dict:
    meta = inference.model_metadata()
    if meta is None:
        return {"trained": False}
    return {"trained": True, **meta}


@router.post("/train")
def train_model() -> dict:
    try:
        artifact = train.main()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"trained": True, **artifact}
