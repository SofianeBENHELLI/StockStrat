"""The broker port: the one seam between "we decided to trade" and "something
executed it".

Only app/paper/service.py may import from this module. That single-caller rule
is what makes the safety guarantees checkable in one place instead of spread
across every route and the tournament cycle.

Three deliberate choices, each of which costs nothing now and saves a rewrite
when a real venue is added:

- **`market_price` is injected, never fetched here.** The simulator needs a
  price, an API broker ignores it. Keeping the fetch outside means a broker has
  no market-data dependency and stays trivially testable — and it is the same
  property that lets app/backtest/engine.py replay fills at a past date.
- **A rejection is a return value, not an exception.** `status="rejected"` plus
  a human-readable `detail` that reaches the UI. Only *configuration* problems
  raise (`BrokerUnavailable`), because those are the operator's problem, not
  the strategy's.
- **The broker holds no order state.** The simulator is stateless: `poll()`
  re-evaluates the order it is handed against a fresh price. State lives in the
  database, so a restart loses nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.paper.broker import simulate_fill


class BrokerUnavailable(RuntimeError):
    """Raised when a broker is selected but cannot be constructed — missing
    credentials, or not implemented yet. Deliberately distinct from a rejected
    order: this one is a misconfiguration, and it is surfaced when a portfolio
    is created rather than at the moment an order is submitted."""


@dataclass(frozen=True)
class OrderSpec:
    """Everything a broker needs to act, with no ORM object attached."""
    order_id: int
    symbol: str
    side: str                      # buy | sell
    qty: float
    order_type: str = "market"     # market | limit
    limit_price: float | None = None


@dataclass
class BrokerOrderResult:
    broker_order_id: str
    status: str                    # open | filled | partial_fill | rejected | cancelled
    filled_qty: float = 0.0
    filled_avg_price: float | None = None
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    detail: str = ""


class BrokerProtocol(Protocol):
    name: str

    def submit(self, spec: OrderSpec, market_price: float | None) -> BrokerOrderResult: ...
    def poll(self, broker_order_id: str, spec: OrderSpec, market_price: float | None) -> BrokerOrderResult: ...
    def cancel(self, broker_order_id: str) -> bool: ...


class SimBroker:
    """In-process fill simulator — the default, and the only broker that needs
    no credentials and makes no network call.

    It delegates to app/paper/broker.simulate_fill, which models a bid-ask
    spread, size-scaled slippage and partial fills, deterministically from
    (symbol, order_id, side, qty). That determinism is why the same function
    backs the historical backtest: identical inputs give identical fills.
    """
    name = "sim"

    def submit(self, spec: OrderSpec, market_price: float | None) -> BrokerOrderResult:
        result = simulate_fill(
            symbol=spec.symbol, side=spec.side, qty=spec.qty, order_type=spec.order_type,
            limit_price=spec.limit_price, market_price=market_price, order_id=spec.order_id,
            unfilled_limit_status="open",
        )
        return BrokerOrderResult(
            broker_order_id=f"sim-{spec.order_id}", status=result.status,
            filled_qty=result.filled_qty, filled_avg_price=result.filled_avg_price,
            spread_bps=result.spread_bps, slippage_bps=result.slippage_bps, detail=result.detail,
        )

    def poll(self, broker_order_id: str, spec: OrderSpec, market_price: float | None) -> BrokerOrderResult:
        """Stateless: re-run the same simulation against the refreshed price. An
        order that still has not crossed simply comes back `open` again."""
        result = self.submit(spec, market_price)
        result.broker_order_id = broker_order_id
        return result

    def cancel(self, broker_order_id: str) -> bool:
        return True  # nothing is resting at a venue; the DB row is the order


class AlpacaPaperBroker:
    """Not implemented yet — phase 3.

    Declared here rather than omitted so the port has a second implementation
    to be shaped against, and so the Administration panel can show the option
    with an honest reason instead of an empty dropdown. When it is built it
    hardcodes `paper=True`, maps unknown venue statuses to `open`, and needs a
    position-reconciliation pass that the simulator does not.
    """
    name = "alpaca_paper"

    def __init__(self) -> None:
        raise BrokerUnavailable(
            "Alpaca paper execution is not implemented yet (phase 3). Use the 'sim' broker, "
            "which models spread, slippage and partial fills in process."
        )


_SIM = SimBroker()

_REGISTRY: dict[str, dict] = {
    "sim": {
        "factory": lambda: _SIM,
        "label": "Simulateur (intégré)",
        "available": True,
        "description": "Modélise le spread bid-ask, un slippage proportionnel à la taille et les fills "
                       "partiels. Aucun identifiant, aucun appel réseau, déterministe et reproductible.",
    },
    "alpaca_paper": {
        "factory": AlpacaPaperBroker,
        "label": "Alpaca (paper)",
        "available": False,
        "description": "Route les ordres vers le endpoint paper d'Alpaca. Pas encore implémenté (phase 3).",
    },
}


def get_broker(name: str) -> BrokerProtocol:
    """Resolve a broker by name. Unknown names fall back to the simulator
    rather than raising: a stale row in the database must never be able to stop
    a paper portfolio from trading."""
    entry = _REGISTRY.get(name)
    if entry is None:
        return _SIM
    return entry["factory"]()


def available_brokers() -> list[dict]:
    return [
        {"name": name, "label": e["label"], "available": e["available"], "description": e["description"]}
        for name, e in _REGISTRY.items()
    ]
