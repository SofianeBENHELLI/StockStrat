"""Parameter search that tells you whether searching was worth it.

Trying dozens of parameter sets and keeping the best is the textbook way to
fool yourself: the best of many backtests is partly the luckiest, and its
number is inflated by the very act of choosing it. So this search splits
history in two:

- **in-sample** (default 2016-2021): where parameters are compared and ranked;
- **out-of-sample** (default 2022 -> today): never used to choose anything,
  only to check what the choice would have been worth afterwards.

The headline is not "the best parameters". It is the rank correlation between
in-sample and out-of-sample results across every combination tried. Near +1,
the past ranking predicts the future one and optimising is meaningful. Near 0,
picking the in-sample winner is picking at random — and the page says so.

Each combination runs as one continuous backtest across both periods, and the
out-of-sample result is the return of the second segment applied to a fresh
budget. The Matheux's walk-forward training is already point-in-time, so its
out-of-sample predictions never see a label from their own future either.
"""
from __future__ import annotations

import itertools
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from app.lab.engine import backtest, load_for
from app.lab.metrics import drawdown
from app.lab.profiles import get_profile

# Each block is a Cartesian grid; a profile may have several blocks (the
# Flambeur's two signal families have different parameters).
GRIDS: dict[str, list[dict[str, list]]] = {
    "flambeur": [
        {"mode": ["rebond"], "entry_rsi": [5, 10, 15], "max_positions": [3, 5, 8],
         "stop_loss_pct": [0, 8], "exit_sma": [3, 5]},
        {"mode": ["cassure"], "breakout_len": [10, 20, 55], "volume_mult": [1.5, 2.0],
         "trailing_stop_pct": [4, 6, 10], "stop_loss_pct": [0], "max_hold_days": [15]},
    ],
    "matheux": [
        {"model": ["ridge"], "top_n": [5, 10, 20], "train_months": [24, 36, 60], "regime_filter": [False, True]},
    ],
    "stratege": [
        {"lookback_months": [3, 6, 9, 12], "top_k": [2, 3, 4], "trailing_stop_pct": [0, 10, 15],
         "min_hold_months": [1, 3, 6]},
    ],
}


def combinations(profile: str) -> list[dict]:
    out = []
    for block in GRIDS[profile]:
        keys = list(block)
        for values in itertools.product(*(block[k] for k in keys)):
            out.append(dict(zip(keys, values)))
    return out


@dataclass
class Job:
    id: str
    profile: str
    start: date
    split: date
    budget: float
    total: int
    done: int = 0
    status: str = "running"          # running | done | failed
    error: str | None = None
    results: list[dict] = field(default_factory=list)
    verdict: dict = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str | None = None
    elapsed_s: float = 0.0

    def as_dict(self, with_results: bool = True) -> dict:
        d = {k: getattr(self, k) for k in ("id", "profile", "budget", "total", "done", "status", "error",
                                            "verdict", "started_at", "finished_at", "elapsed_s")}
        d["start"], d["split"] = self.start.isoformat(), self.split.isoformat()
        if with_results:
            d["results"] = self.results
        return d


JOBS: dict[str, Job] = {}
_LATEST: dict[str, str] = {}
_lock = threading.Lock()


def _segment(series: pd.Series, split: pd.Timestamp, budget: float) -> tuple[float, float, dict]:
    """In-sample P&L in dollars, and the out-of-sample segment re-based to a
    fresh budget (return, dollars, drawdown)."""
    i = int(series.index.searchsorted(split))
    i = min(max(i, 1), len(series) - 1)
    ins = float(series.iloc[i] - budget)
    oos = series.iloc[i:] / series.iloc[i] * budget
    return ins, float(oos.iloc[-1] - budget), drawdown(oos)


def _run(job: Job) -> None:
    t0 = time.monotonic()
    try:
        profile = get_profile(job.profile)
        grid = combinations(job.profile)
        panel = load_for(profile, profile.resolve({}))
        split = pd.Timestamp(job.split)
        for params in grid:
            r = backtest(job.profile, params, job.start, None, job.budget, panel=panel)
            ins, oos, oos_dd = _segment(r.equity, split, job.budget)
            _, placebo_oos, _ = _segment(r.placebo, split, job.budget)
            _, spy_oos, _ = _segment(r.benchmark, split, job.budget)
            ins_dd = drawdown(r.equity.loc[:split])
            job.results.append({
                "params": params,
                "in_sample_pnl_usd": round(ins, 2),
                "in_sample_drawdown_usd": ins_dd["max_drawdown_usd"],
                "out_of_sample_pnl_usd": round(oos, 2),
                "out_of_sample_drawdown_usd": oos_dd["max_drawdown_usd"],
                "out_of_sample_vs_placebo_usd": round(oos - placebo_oos, 2),
                "out_of_sample_vs_spy_usd": round(oos - spy_oos, 2),
                "placebo_oos_usd": round(placebo_oos, 2),
                "spy_oos_usd": round(spy_oos, 2),
                "sells": sum(1 for f in r.fills if f.side == "sell"),
            })
            job.done += 1
            job.elapsed_s = round(time.monotonic() - t0, 1)
        job.verdict = verdict(job.results)
        job.status = "done"
    except Exception as exc:  # surfaced to the page, never swallowed
        job.status, job.error = "failed", str(exc)
    finally:
        job.finished_at = datetime.now(timezone.utc).isoformat()
        job.elapsed_s = round(time.monotonic() - t0, 1)


def verdict(results: list[dict]) -> dict:
    if len(results) < 3:
        return {}
    df = pd.DataFrame(results)
    rho = float(df["in_sample_pnl_usd"].rank().corr(df["out_of_sample_pnl_usd"].rank()))
    df["oos_rank"] = df["out_of_sample_pnl_usd"].rank(ascending=False, method="min").astype(int)
    best = df.sort_values("in_sample_pnl_usd", ascending=False).iloc[0]
    top5 = df.sort_values("in_sample_pnl_usd", ascending=False).head(5)
    median_oos = float(df["out_of_sample_pnl_usd"].median())
    if rho >= 0.5:
        reading = ("Le classement sur le passé annonce bien le futur : ici, optimiser les paramètres a un sens.")
    elif rho >= 0.2:
        reading = ("Le passé donne une indication, faible : les meilleures combinaisons tendent à rester "
                   "correctes, sans garantie. Préférez une zone stable à un réglage pointu.")
    elif rho > -0.2:
        reading = ("Le classement sur le passé ne dit presque rien du futur : choisir la meilleure "
                   "combinaison revient à peu près à tirer au sort. Gardez des réglages simples.")
    else:
        reading = ("Le passé classe à l'envers : ce qui marchait le mieux a ensuite fait le moins bien. "
                   "Optimiser ici aggrave les choses.")
    return {
        "combinations": len(df),
        "rank_correlation": round(rho, 3),
        "reading": reading,
        "best_in_sample": {
            "params": best["params"],
            "in_sample_pnl_usd": float(best["in_sample_pnl_usd"]),
            "out_of_sample_pnl_usd": float(best["out_of_sample_pnl_usd"]),
            "out_of_sample_rank": int(best["oos_rank"]),
        },
        "top5_in_sample_mean_oos_usd": round(float(top5["out_of_sample_pnl_usd"].mean()), 2),
        "median_oos_usd": round(median_oos, 2),
        "placebo_oos_usd": float(df["placebo_oos_usd"].iloc[0]),
        "spy_oos_usd": float(df["spy_oos_usd"].iloc[0]),
        "share_beating_placebo_oos_pct": round(float((df["out_of_sample_vs_placebo_usd"] > 0).mean() * 100), 1),
    }


def start(profile: str, start_day: date = date(2016, 1, 4), split: date = date(2022, 1, 3),
          budget: float = 10_000.0) -> Job:
    get_profile(profile)
    if split <= start_day:
        raise ValueError("la date de coupure doit suivre la date de début")
    with _lock:
        running = [j for j in JOBS.values() if j.status == "running"]
        if running:
            raise ValueError(f"une optimisation est déjà en cours ({running[0].profile}) — un calcul à la fois")
        job = Job(id=uuid.uuid4().hex[:10], profile=profile, start=start_day, split=split, budget=budget,
                  total=len(combinations(profile)))
        JOBS[job.id] = job
        _LATEST[profile] = job.id
    threading.Thread(target=_run, args=(job,), daemon=True).start()
    return job


def get(job_id: str) -> Job | None:
    return JOBS.get(job_id)


def latest(profile: str) -> Job | None:
    job_id = _LATEST.get(profile)
    return JOBS.get(job_id) if job_id else None
