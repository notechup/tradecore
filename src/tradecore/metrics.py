"""Performance statistics for an equity curve and a trade list.

The statistics are mechanism. **The thresholds that decide whether a result is good enough are
not**, and they are not in this module — see `WarningPolicy` below.

Conventions, stated because every package chooses differently and a consumer that assumes the
other choice will misread every number here:

  * Returns are simple daily returns of end-of-session equity.
  * Sharpe is annualised at sqrt(252) with a **zero risk-free rate**. That flatters any
    long-biased strategy, and the size of the flattery depends on the era being measured, so a
    consumer comparing against a published figure should check which convention that figure
    used. A financing series is the alternative and this module does not have one.
  * Max drawdown is on end-of-session equity, so it understates the intraday figure.
  * CAGR uses calendar years between the first and last session, not bar count / 252.

`compute_metrics` types `trades` structurally rather than importing a trade type, so it can
also score a trade list that came from somewhere else.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Protocol

__all__ = [
    "TRADING_DAYS_PER_YEAR",
    "Metrics",
    "WarningPolicy",
    "compute_metrics",
    "max_drawdown",
    "no_warnings",
]

TRADING_DAYS_PER_YEAR = 252
DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class Metrics:
    """Headline statistics, plus the reasons not to trust them.

    `warnings` has no default: constructing this type means deciding what the caveats are.
    """

    start: date | None
    end: date | None
    years: float
    start_equity: float
    end_equity: float
    total_return: float
    cagr: float
    max_drawdown: float
    max_drawdown_start: date | None
    max_drawdown_end: date | None
    sharpe: float
    sortino: float
    volatility: float
    n_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    expectancy: float
    expectancy_r: float
    avg_bars_held: float
    time_in_market: float
    total_costs: float
    warnings: tuple[str, ...]

    def as_row(self) -> str:
        """One-line summary for a report table."""
        return (
            f"CAGR {self.cagr:7.2%}  MaxDD {self.max_drawdown:7.2%}  "
            f"Sharpe {self.sharpe:5.2f}  Win {self.win_rate:6.2%}  "
            f"PF {self.profit_factor:5.2f}  n={self.n_trades:4d}"
        )


class WarningPolicy(Protocol):
    """Decides which caveats a finished `Metrics` should carry. Supplied by the consumer.

    **This is the seam that keeps this module free of policy.** "Too few trades to call this
    strategy validated" is a statement about one system's gate, not about arithmetic: the
    threshold, the stage of the programme it belongs to and the document it cites all differ per
    consumer, and a default here would quietly hand one system's standard to another.

    So this package ships the type and no implementation but `no_warnings`. A consumer states
    its own thresholds and its own wording, in its own repo, and passes them to
    `compute_metrics` — where `warn` is a required keyword argument for exactly that reason: a
    forgotten policy must not be indistinguishable from a clean result.

    The caveats are attached to the `Metrics` object rather than returned beside it because the
    failure they exist to prevent is a headline number travelling without them.
    """

    def __call__(self, m: Metrics) -> tuple[str, ...]: ...


def no_warnings(m: Metrics) -> tuple[str, ...]:
    """A `WarningPolicy` that never warns. For callers with no gate to apply — not a default."""
    return ()


def max_drawdown(
    days: Sequence[date], equity: Sequence[float]
) -> tuple[float, date | None, date | None]:
    """Largest peak-to-trough decline, as a negative fraction, with the dates that bracket it."""
    if not equity:
        return 0.0, None, None
    peak = equity[0]
    peak_day = days[0]
    worst = 0.0
    worst_peak_day: date | None = None
    worst_day: date | None = None
    for day, eq in zip(days, equity, strict=True):
        if eq > peak:
            peak, peak_day = eq, day
        dd = (eq - peak) / peak if peak > 0 else 0.0
        if dd < worst:
            worst, worst_peak_day, worst_day = dd, peak_day, day
    return worst, worst_peak_day, worst_day


def _annualised_vol(values: Sequence[float]) -> float:
    """Annualised standard deviation of daily returns (population form, ddof=0)."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _annualised_downside(values: Sequence[float]) -> float:
    """Annualised downside deviation: the denominator of Sortino.

    Two details that are easy to get wrong and that both flatter or punish the ratio by a
    large factor. Deviations are measured against ZERO, not against the sample mean — Sortino
    asks how bad the bad days are, not how dispersed they are. And the sum is divided by the
    count of ALL observations, not by the count of down days; dividing by the down days alone
    inflates the denominator and drives Sortino below Sharpe, which is the wrong way round for
    any series with positive skew.
    """
    if len(values) < 2:
        return 0.0
    var = sum(min(v, 0.0) ** 2 for v in values) / len(values)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS_PER_YEAR)


def compute_metrics(
    days: Sequence[date],
    equity: Sequence[float],
    trades: Sequence,
    *,
    warn: WarningPolicy,
    bars_in_market: int = 0,
    total_costs: float = 0.0,
) -> Metrics:
    """Build the statistics. `trades` is any sequence of objects with `net_pnl` and `bars_held`.

    `warn` is the consumer's `WarningPolicy` and is required; pass `no_warnings` to state
    explicitly that there is no gate to apply. It is called once on the finished object, so it
    can see every field.
    """
    if len(days) != len(equity):
        raise ValueError(f"days ({len(days)}) and equity ({len(equity)}) must be the same length")
    if not days:
        raise ValueError("cannot compute metrics over an empty equity curve")

    start, end = days[0], days[-1]
    years = max((end - start).days / DAYS_PER_YEAR, 1e-9)
    start_eq, end_eq = equity[0], equity[-1]
    total_return = end_eq / start_eq - 1.0 if start_eq > 0 else 0.0
    cagr = (end_eq / start_eq) ** (1.0 / years) - 1.0 if start_eq > 0 and end_eq > 0 else -1.0

    rets = [
        (equity[i] / equity[i - 1] - 1.0) if equity[i - 1] > 0 else 0.0
        for i in range(1, len(equity))
    ]
    vol = _annualised_vol(rets)
    downside = _annualised_downside(rets)
    mean_daily = sum(rets) / len(rets) if rets else 0.0
    ann_return = mean_daily * TRADING_DAYS_PER_YEAR
    sharpe = ann_return / vol if vol > 0 else 0.0
    sortino = ann_return / downside if downside > 0 else 0.0

    dd, dd_start, dd_end = max_drawdown(days, equity)

    pnls = [t.net_pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    n = len(pnls)
    avg_win = gross_win / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0
    expectancy = sum(pnls) / n if n else 0.0
    # Expectancy in R: average trade divided by the average money-at-risk at entry, which is
    # the risk budget the sizing formula spent. That makes the number comparable across
    # instruments and equity levels in a way a currency expectancy is not.
    risked = [getattr(t, "risk_at_entry", 0.0) for t in trades]
    avg_risk = sum(r for r in risked if r > 0) / max(sum(1 for r in risked if r > 0), 1)
    expectancy_r = expectancy / avg_risk if avg_risk > 0 else 0.0

    m = Metrics(
        start=start,
        end=end,
        years=years,
        start_equity=start_eq,
        end_equity=end_eq,
        total_return=total_return,
        cagr=cagr,
        max_drawdown=dd,
        max_drawdown_start=dd_start,
        max_drawdown_end=dd_end,
        sharpe=sharpe,
        sortino=sortino,
        volatility=vol,
        n_trades=n,
        win_rate=len(wins) / n if n else 0.0,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=(gross_win / gross_loss)
        if gross_loss > 0
        else math.inf
        if gross_win
        else 0.0,
        expectancy=expectancy,
        expectancy_r=expectancy_r,
        avg_bars_held=sum(t.bars_held for t in trades) / n if n else 0.0,
        time_in_market=bars_in_market / len(days) if days else 0.0,
        total_costs=total_costs,
        warnings=(),
    )
    return replace(m, warnings=tuple(warn(m)))
