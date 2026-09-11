from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./strategy_tournament.db"
    market_data_provider: str = "yfinance"  # yfinance | mock
    cors_origins: list[str] = ["http://localhost:3001"]
    tiingo_api_key: str | None = None  # optional, paid — real fundamentals (app/data/fundamentals.py)
    anthropic_api_key: str | None = None  # optional — agentic trade explanations (app/agent/explain.py)


@lru_cache
def get_settings() -> Settings:
    return Settings()
