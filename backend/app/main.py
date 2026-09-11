from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import init_db
from app.routers import backtest, ml, strategies, system, tournament, variants

app = FastAPI(title="Strategy Tournament API", version="0.1.0")

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(variants.router)
app.include_router(system.router)
app.include_router(strategies.router)
app.include_router(tournament.router)
app.include_router(ml.router)
app.include_router(backtest.router)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "mode": "paper_trading_only"}
