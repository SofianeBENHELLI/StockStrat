"""Market data for the lab — Alpaca only, never a silent fallback.

One module feeds both the backtest (ten years of daily bars) and the paper
runner (today's bar so far plus the last trade). That is deliberate: if the
two paths read prices from different sources, a gap between paper and backtest
could come from the data rather than the market, and the whole comparison the
lab exists for would stop meaning anything.

Bars are split- and dividend-adjusted (`Adjustment.ALL`), so a return computed
across a split or an ex-dividend date is a real return, not an artefact.

Daily bars are cached on disk (`backend/data_cache/`, gitignored) and topped up
incrementally, so the first run pays ~a minute and every later one a second.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.core import settings_store
from app.core.db import SessionLocal
from app.core.paths import DATA_DIR

CACHE_DIR = DATA_DIR / "data_cache"
HISTORY_START = date(2015, 6, 1)
FIELDS = ("open", "high", "low", "close", "volume")

_lock = threading.Lock()
_panel_memo: dict[str, tuple[Panel, datetime]] = {}


class DataUnavailable(RuntimeError):
    """No credentials, or Alpaca unreachable. Raised instead of inventing prices."""


@dataclass
class Panel:
    """Dates x symbols, one frame per field. Missing = not trading yet (or halted)."""
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.close.index

    def select(self, symbols: list[str]) -> Panel:
        cols = [s for s in symbols if s in self.close.columns]
        return Panel(*(getattr(self, f)[cols] for f in FIELDS))

    def until(self, day: pd.Timestamp) -> Panel:
        return Panel(*(getattr(self, f).loc[:day] for f in FIELDS))

    def since(self, day: pd.Timestamp) -> Panel:
        return Panel(*(getattr(self, f).loc[day:] for f in FIELDS))


def _credentials() -> tuple[str, str]:
    db = SessionLocal()
    try:
        key = settings_store.resolve(db, "connections.alpaca_api_key")
        secret = settings_store.resolve(db, "connections.alpaca_secret_key")
    finally:
        db.close()
    if not key or not secret:
        raise DataUnavailable(
            "Alpaca credentials are missing — set them in Administration → Connexions.")
    return key, secret


def _stock_client():
    from alpaca.data.historical import StockHistoricalDataClient

    key, secret = _credentials()
    return StockHistoricalDataClient(key, secret)


def _fetch_bars(symbols: list[str], start: date) -> pd.DataFrame:
    """Long frame: symbol, day, open, high, low, close, volume."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = _stock_client()
    # SIP (consolidated tape) is free for history older than 15 minutes; IEX
    # alone carries a few percent of volume, which would distort volume signals.
    end = datetime.now(timezone.utc) - timedelta(minutes=20)
    frames = []
    for i in range(0, len(symbols), 50):
        chunk = symbols[i:i + 50]
        req = StockBarsRequest(symbol_or_symbols=chunk, timeframe=TimeFrame.Day,
                               start=datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc),
                               end=end, adjustment=Adjustment.ALL, feed=DataFeed.SIP)
        try:
            bars = client.get_stock_bars(req)
        except Exception:
            req.feed = DataFeed.IEX
            bars = client.get_stock_bars(req)
        df = bars.df
        if df is None or df.empty:
            continue
        df = df.reset_index()
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["symbol", "day", *FIELDS])
    raw = pd.concat(frames, ignore_index=True)
    # Daily bars are stamped at midnight New York time; keep only the session date.
    ts = pd.to_datetime(raw["timestamp"], utc=True).dt.tz_convert("America/New_York")
    raw["day"] = pd.to_datetime(ts.dt.date)
    return raw[["symbol", "day", *FIELDS]]


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.pkl"


def _to_panel(long: pd.DataFrame) -> Panel:
    frames = []
    for f in FIELDS:
        wide = long.pivot_table(index="day", columns="symbol", values=f, aggfunc="last").sort_index()
        wide.index.name = None
        wide.columns.name = None
        frames.append(wide.astype(float))
    return Panel(*frames)


def refresh(symbols: list[str], name: str = "stocks", force: bool = False) -> pd.DataFrame:
    """Top up the on-disk cache and return the long frame."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(name)
    cached = pd.read_pickle(path) if path.exists() and not force else None

    if cached is None or cached.empty:
        long = _fetch_bars(symbols, HISTORY_START)
    else:
        have = set(cached["symbol"].unique())
        new_syms = [s for s in symbols if s not in have]
        last = cached["day"].max().date()
        parts = [cached]
        # Re-fetch a few days back: the most recent bar may have been partial.
        top_up = _fetch_bars(sorted(have & set(symbols)), last - timedelta(days=5))
        if not top_up.empty:
            parts.append(top_up)
        if new_syms:
            parts.append(_fetch_bars(new_syms, HISTORY_START))
        long = (pd.concat(parts, ignore_index=True)
                .drop_duplicates(subset=["symbol", "day"], keep="last"))
    long = _completed_sessions_only(long)
    long = long.sort_values(["symbol", "day"]).reset_index(drop=True)
    long.to_pickle(path)
    return long


def _completed_sessions_only(long: pd.DataFrame) -> pd.DataFrame:
    """The cache holds finished sessions only. During the session, Alpaca's
    daily bar for today is partial (and 16 minutes behind); stored as if it
    were final, it would give every backtest run that afternoon a truncated
    last day. Today's row is the runner's job, via `with_live_row`, with a
    projected volume — never the cache's."""
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("America/New_York"))
    if now.hour * 60 + now.minute < 16 * 60 + 30:
        return long[long["day"] < pd.Timestamp(now.date())]
    return long


def load_panel(symbols: list[str], name: str = "stocks", max_age_minutes: int = 60) -> Panel:
    """Daily panel for `symbols`, refreshed from Alpaca if the cache is stale."""
    now = datetime.now(timezone.utc)
    with _lock:
        memo = _panel_memo.get(name)
        if memo and (now - memo[1]).total_seconds() < max_age_minutes * 60:
            panel = memo[0]
            if all(s in panel.close.columns for s in symbols):
                return panel.select(symbols)
        long = refresh(symbols, name)
        panel = _to_panel(long)
        _panel_memo[name] = (panel, now)
    return panel.select(symbols)


def load_cached_panel(name: str = "stocks") -> Panel | None:
    """Whatever is on disk, without touching the network. Tests and offline use."""
    path = _cache_path(name)
    if not path.exists():
        return None
    return _to_panel(pd.read_pickle(path))


@dataclass
class LiveBar:
    symbol: str
    price: float          # last trade (IEX, real time)
    open: float
    high: float
    low: float
    volume: float         # consolidated, projected to a full session — NaN when unknown
    prev_close: float | None
    at: datetime
    volume_fraction: float = 1.0   # share of a typical session's volume the projection is based on


SIP_DELAY = timedelta(minutes=16)  # the free plan serves consolidated data older than 15 minutes


def session_volume_fraction(minutes_since_open: float) -> float:
    """Typical share of a US session's volume traded by a given minute.

    Intraday volume is U-shaped: heavy in the first half hour, thin at midday,
    heavy again into the close (the closing auction alone is often ~10%). This
    piecewise-linear approximation — 15% by 10:00, 78% by 15:30, 100% at 16:00
    — is enough to project a partial day to a full one, which is what the
    volume signals were computed on in the backtest. It is an estimate, and it
    is only needed during the session: after the close the bar is complete.
    """
    m = minutes_since_open
    if m <= 0:
        return 0.0
    if m <= 30:
        return 0.15 * m / 30
    if m <= 360:
        return 0.15 + 0.63 * (m - 30) / 330
    if m < 390:
        return 0.78 + 0.22 * (m - 360) / 30
    return 1.0


def _sip_today(client, symbols: list[str], day: date) -> tuple[dict, float]:
    """Today's consolidated daily bar, as of 16 minutes ago, and the share of
    the session it covers."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    end = datetime.now(timezone.utc) - SIP_DELAY
    opened = datetime.combine(day, datetime.min.time(), tzinfo=ny).replace(hour=9, minute=30)
    fraction = session_volume_fraction((end - opened).total_seconds() / 60)
    out: dict = {}
    for i in range(0, len(symbols), 100):
        req = StockBarsRequest(symbol_or_symbols=symbols[i:i + 100], timeframe=TimeFrame.Day,
                               start=datetime.combine(day, datetime.min.time(), tzinfo=ny), end=end,
                               feed=DataFeed.SIP)
        try:
            bars = client.get_stock_bars(req)
        except Exception:
            continue
        for sym, rows in bars.data.items():
            if rows:
                out[sym] = rows[-1]
    return out, fraction


def live_bars(symbols: list[str]) -> dict[str, LiveBar]:
    """Today's session so far, per symbol, with the last trade as the close.

    This is what lets the paper runner call the exact same decision code as
    the backtest: it appends one more row — today — to the historical panel.

    Two feeds, on purpose. The *price* is the last IEX trade: real time, and
    IEX prices track the national best bid/offer. The *volume* must not come
    from IEX: that one exchange sees ~3% of the day's shares (788k of 24.5M
    for AAPL on the day this was written), while the historical panel holds
    consolidated volume — a volume signal would read ~30x too low and never
    fire in paper while firing in the backtest. So volume comes from the
    consolidated bar (16 minutes behind on the free plan), projected to a full
    session; if that bar is unavailable the volume is left unknown (NaN), and
    no volume signal can trigger on it.
    """
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockSnapshotRequest
    from zoneinfo import ZoneInfo

    client = _stock_client()
    day = datetime.now(ZoneInfo("America/New_York")).date()
    sip, fraction = _sip_today(client, symbols, day)
    out: dict[str, LiveBar] = {}
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        snaps = client.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=chunk, feed=DataFeed.IEX))
        for sym, s in snaps.items():
            trade, iex_bar, prev = s.latest_trade, s.daily_bar, s.previous_daily_bar
            if trade is None or iex_bar is None:
                continue
            price = float(trade.price)
            bar = sip.get(sym)
            if bar is not None and bar.timestamp.astimezone(ZoneInfo("America/New_York")).date() == day and fraction > 0:
                o, h, lo = float(bar.open), max(float(bar.high), price), min(float(bar.low), price)
                volume = float(bar.volume) / fraction
            else:
                o, h, lo = float(iex_bar.open), max(float(iex_bar.high), price), min(float(iex_bar.low), price)
                volume = float("nan")
            out[sym] = LiveBar(symbol=sym, price=price, open=o, high=h, low=lo, volume=volume,
                               prev_close=float(prev.close) if prev is not None else None, at=trade.timestamp,
                               volume_fraction=fraction)
    return out


def latest_prices(symbols: list[str]) -> dict[str, float]:
    return {s: b.price for s, b in live_bars(symbols).items()}


def with_live_row(panel: Panel, bars: dict[str, LiveBar], day: pd.Timestamp) -> Panel:
    """Append (or replace) the row for `day` with the live session bars."""
    frames = []
    for f in FIELDS:
        frame = getattr(panel, f).copy()
        row = {s: (b.price if f == "close" else getattr(b, f)) for s, b in bars.items() if s in frame.columns}
        frame.loc[day, list(row)] = list(row.values())
        frames.append(frame.sort_index())
    return Panel(*frames)
