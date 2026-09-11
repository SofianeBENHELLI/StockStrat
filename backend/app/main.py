from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import init_db
from app.monitor import scheduler
from app.routers import backtest, ml, settings as settings_router, strategies, system, tournament, variants


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(title="Strategy Tournament API", version="0.1.0", lifespan=lifespan)

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
app.include_router(settings_router.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "mode": "paper_trading_only"}
