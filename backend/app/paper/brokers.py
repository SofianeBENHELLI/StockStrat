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
from functools import lru_cache
from typing import Protocol

from app.paper.broker import simulate_fill

# Alpaca order states that mean "nothing more will happen to this order".
# Everything else — including `partially_filled` — is treated as still live, so
# the poller keeps watching it. See AlpacaPaperBroker._translate.
_ALPACA_TERMINAL = {"filled", "canceled", "cancelled", "expired", "rejected", "done_for_day", "stopped"}


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
    client_order_id: str | None = None  # our tag, visible in the Alpaca dashboard


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
            # A partial fill here would be terminal, while at a real venue the
            # remainder keeps working until it fills. Terminal partials left
            # ~18% of lab orders short, stranding idle cash and residual
            # positions no real broker would leave. Spread and slippage stay.
            allow_partial=False,
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


@dataclass(frozen=True)
class Credentials:
    api_key: str | None = None
    secret_key: str | None = None

    def complete(self) -> bool:
        return bool(self.api_key and self.secret_key)


class AlpacaPaperBroker:
    """Alpaca's paper endpoint.

    `paper=True` is hardcoded and there is no setting, environment variable or
    API field that can flip it. Reaching live trading from here is not a matter
    of changing a flag — it needs a different class and a different credential
    pair that this code has no way to read.

    Two translation decisions carry real risk if got wrong:

    - **Unknown statuses degrade to `open`.** Alpaca has states this code has
      never seen (`pending_new`, `accepted_for_bidding`, venue-specific ones,
      and whatever is added later). Treating an unrecognised state as finished
      would silently abandon a live order at the venue while the book believes
      it is settled. Treating it as open merely means polling it again.
    - **`partially_filled` is NOT settled.** Our settlement is one-shot per
      order, while Alpaca fills incrementally. Settling a partial immediately
      would book the filled slice and then ignore the rest. So a partial stays
      `open` and is only settled once the order actually finishes — at which
      point `filled_qty` is whatever really executed, including the case of a
      cancel that had partially filled first.
    """
    name = "alpaca_paper"

    def __init__(self, credentials: Credentials | None = None) -> None:
        credentials = credentials or Credentials()
        if not credentials.complete():
            raise BrokerUnavailable(
                "Alpaca paper is selected but its credentials are missing. Add the key ID and "
                "secret in Administration → Connexions, or as ALPACA_API_KEY / ALPACA_SECRET_KEY "
                "in backend/.env."
            )
        try:
            from alpaca.trading.client import TradingClient
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise BrokerUnavailable(f"alpaca-py is not installed: {exc}") from exc

        self._client = TradingClient(
            credentials.api_key, credentials.secret_key,
            paper=True,  # never configurable — see the class docstring
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _translate(order) -> BrokerOrderResult:
        raw = str(getattr(order, "status", "")).lower().rsplit(".", 1)[-1]
        filled_qty = float(getattr(order, "filled_qty", 0) or 0)
        avg_price = getattr(order, "filled_avg_price", None)
        avg_price = float(avg_price) if avg_price is not None else None
        order_id = str(getattr(order, "id", ""))

        if raw not in _ALPACA_TERMINAL:
            # Includes `partially_filled`: still live, keep polling.
            return BrokerOrderResult(order_id, "open", detail=f"alpaca status: {raw or 'unknown'}")
        if raw == "rejected":
            return BrokerOrderResult(order_id, "rejected",
                                     detail=str(getattr(order, "reject_reason", "") or "rejected by Alpaca"))
        if raw == "filled":
            return BrokerOrderResult(order_id, "filled", filled_qty=filled_qty,
                                     filled_avg_price=avg_price, detail="alpaca: filled")
        # canceled / expired / done_for_day / stopped — settle whatever executed.
        if filled_qty > 0 and avg_price is not None:
            return BrokerOrderResult(order_id, "partial_fill", filled_qty=filled_qty,
                                     filled_avg_price=avg_price,
                                     detail=f"alpaca: {raw} after a partial fill")
        return BrokerOrderResult(order_id, "cancelled", detail=f"alpaca: {raw}")

    # -- port ---------------------------------------------------------------

    def submit(self, spec: OrderSpec, market_price: float | None) -> BrokerOrderResult:
        """`market_price` is ignored: the venue has its own book. It stays in the
        signature because the port is shared with the simulator, which needs it."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

        side = OrderSide.BUY if spec.side == "buy" else OrderSide.SELL
        common = {"symbol": spec.symbol, "qty": spec.qty, "side": side,
                  "time_in_force": TimeInForce.DAY}
        if spec.client_order_id:
            common["client_order_id"] = spec.client_order_id
        try:
            if spec.order_type == "limit":
                if spec.limit_price is None:
                    return BrokerOrderResult("", "rejected", detail="limit order without limit price")
                request = LimitOrderRequest(**common, limit_price=spec.limit_price)
            else:
                request = MarketOrderRequest(**common)
            return self._translate(self._client.submit_order(request))
        except Exception as exc:
            # A venue refusal is a normal outcome of trading (no buying power, a
            # symbol that is not fractionable, a closed market), so it comes back
            # as a rejected order rather than an exception that would abort the
            # whole tournament cycle.
            return BrokerOrderResult("", "rejected", detail=f"alpaca rejected the order: {exc}")

    def poll(self, broker_order_id: str, spec: OrderSpec, market_price: float | None) -> BrokerOrderResult:
        try:
            return self._translate(self._client.get_order_by_id(broker_order_id))
        except Exception as exc:
            # Never invent a terminal state from a failed lookup: a network blip
            # must not be recorded as a cancelled order.
            return BrokerOrderResult(broker_order_id, "open", detail=f"alpaca poll failed: {exc}")

    def cancel(self, broker_order_id: str) -> bool:
        try:
            self._client.cancel_order_by_id(broker_order_id)
            return True
        except Exception:
            return False

    # -- operational --------------------------------------------------------

    def account(self) -> dict:
        a = self._client.get_account()
        return {
            "account_number": str(getattr(a, "account_number", "")),
            "status": str(getattr(a, "status", "")),
            "currency": str(getattr(a, "currency", "")),
            "cash": float(getattr(a, "cash", 0) or 0),
            "equity": float(getattr(a, "equity", 0) or 0),
            "buying_power": float(getattr(a, "buying_power", 0) or 0),
            "pattern_day_trader": bool(getattr(a, "pattern_day_trader", False)),
        }

    def positions(self) -> dict[str, float]:
        return {str(p.symbol): float(p.qty) for p in self._client.get_all_positions()}

    def clock(self) -> dict:
        c = self._client.get_clock()
        return {
            "is_open": bool(c.is_open),
            "next_open": c.next_open.isoformat() if getattr(c, "next_open", None) else None,
            "next_close": c.next_close.isoformat() if getattr(c, "next_close", None) else None,
        }


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
        "available": True,
        "needs_credentials": True,
        "description": "Route les ordres vers le endpoint paper d'Alpaca (paper=True codé en dur, "
                       "jamais configurable). Exécution réelle contre le NBBO, sans argent réel.",
    },
}


def get_broker(name: str, credentials: Credentials | None = None) -> BrokerProtocol:
    """Resolve a broker by name. Unknown names fall back to the simulator
    rather than raising: a stale row in the database must never be able to stop
    a paper portfolio from trading."""
    entry = _REGISTRY.get(name)
    if entry is None:
        return _SIM
    if entry.get("needs_credentials"):
        credentials = credentials or Credentials()
        return _alpaca_client(credentials.api_key, credentials.secret_key)
    return entry["factory"]()


@lru_cache(maxsize=4)
def _alpaca_client(api_key: str | None, secret_key: str | None) -> AlpacaPaperBroker:
    """One client per credential pair. A tournament cycle resolves a broker per
    order; without this each one would open its own HTTP session. Failures are
    not cached — lru_cache only stores successful returns — so fixing the keys
    in the panel takes effect on the next attempt."""
    return AlpacaPaperBroker(Credentials(api_key=api_key, secret_key=secret_key))


def available_brokers() -> list[dict]:
    return [
        {"name": name, "label": e["label"], "available": e["available"],
         "needs_credentials": bool(e.get("needs_credentials")), "description": e["description"]}
        for name, e in _REGISTRY.items()
    ]
