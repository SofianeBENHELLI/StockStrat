from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.db import init_db
from app.monitor import scheduler
from app.routers import lab, settings as settings_router


# scikit-learn's joblib cannot count physical cores inside the macOS sandbox and
# warns on every fit; the logical count is what it would fall back to anyway.
os.environ.setdefault("LOKY_MAX_CPU_COUNT", str(os.cpu_count() or 4))


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(title="StockStrat Lab API", version="0.2.0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(lab.router)
app.include_router(settings_router.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "mode": "paper_trading_only"}
