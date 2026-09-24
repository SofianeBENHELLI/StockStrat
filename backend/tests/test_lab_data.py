"""Live data assembly: the parts that can make paper disagree with the backtest."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from app.lab.data import LiveBar, session_volume_fraction, with_live_row
from tests.lab_fixtures import path


def test_the_volume_curve_is_monotonic_and_ends_at_one():
    points = [session_volume_fraction(m) for m in range(-10, 400, 5)]
    assert points == sorted(points)
    assert session_volume_fraction(0) == 0
    assert session_volume_fraction(390) == 1
    # by 15:30 most, but not all, of the day has traded: the close is heavy
    assert 0.7 < session_volume_fraction(360) < 0.85


def test_the_live_row_replaces_today_and_keeps_history():
    panel = path([100.0, 101.0, 102.0])
    today = panel.dates[-1] + pd.offsets.BDay(1)
    bar = LiveBar("AAA", price=110.0, open=103, high=111, low=102, volume=2e6, prev_close=102, at=None)
    live = with_live_row(panel, {"AAA": bar}, today)
    assert list(live.close["AAA"]) == [100, 101, 102, 110]
    assert live.volume["AAA"].iloc[-1] == 2e6
    # appending twice the same day replaces rather than duplicates
    again = with_live_row(live, {"AAA": bar}, today)
    assert len(again.dates) == 4


def test_an_unknown_volume_never_triggers_a_volume_breakout():
    """NaN compares false: with no consolidated bar, the cassure stays silent
    instead of firing (or never firing) on a one-exchange volume."""
    from app.lab import indicators as ind

    panel = path([100.0] * 25 + [120.0])
    panel.volume.iloc[-1] = float("nan")
    ratio = ind.volume_ratio(panel.volume, 20).iloc[-1]["AAA"]
    assert math.isnan(ratio)
    assert not (ratio >= 1.5)
