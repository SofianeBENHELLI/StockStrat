"""In-memory portfolio state for the backtest engine — mirrors the essential
fields of the DB models (PaperPortfolio/Position, app/models.py) without any
SQLAlchemy, so a multi-year, 14-variant backtest never touches the database."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BtPosition:
    symbol: str
    qty: float = 0.0
    avg_entry_price: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class BtPortfolio:
    variant_id: str  # f"{engine}-{key}" — synthetic id for this run, not a DB id
    name: str
    engine: str
    variant_key: str
    initial_cash: float
    cash: float
    status: str = "active"  # active | killed
    scaled: bool = False  # cash boost is one-time — see app/tournament/cycle.py's module docstring
    positions: dict[str, BtPosition] = field(default_factory=dict)
    equity_history: list[tuple[str, float]] = field(default_factory=list)  # (ISO date, equity)
    n_fills: int = 0  # buy fills — see engine.py's module docstring for why this
    # gates kill/scale instead of closed trades (nothing in this app auto-sells)

    def equity(self, prices: dict[str, float]) -> float:
        positions_value = sum(
            p.qty * prices.get(p.symbol, p.avg_entry_price) for p in self.positions.values()
        )
        return self.cash + positions_value

    def held_symbols(self) -> set[str]:
        return {s for s, p in self.positions.items() if p.qty > 0}
