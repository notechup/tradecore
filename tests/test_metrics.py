"""Metrics: the arithmetic, and the seam that keeps the thresholds out of this package.

The arithmetic tests moved here verbatim from the consumer that used to own this module. The
one that asserted a minimum-trade-count gate stayed behind with the system whose gate it is —
that threshold is policy and does not belong in this package, which is the whole point of the
move. What replaced it here is a test that a gate can be supplied at all, and one that it
cannot be forgotten.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from tradecore.metrics import Metrics, compute_metrics, max_drawdown, no_warnings


@dataclass(frozen=True)
class _Trade:
    """The structural minimum `compute_metrics` asks of a trade."""

    net_pnl: float
    bars_held: int
    risk_at_entry: float = 0.0


def _days(n: int) -> list[date]:
    return [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]


class TestHelpers:
    def test_max_drawdown_finds_the_peak_and_the_trough(self) -> None:
        days = _days(5)
        dd, peak, trough = max_drawdown(days, [100.0, 120.0, 90.0, 95.0, 130.0])
        assert dd == pytest.approx(-0.25)
        assert peak == days[1]
        assert trough == days[2]

    def test_sortino_exceeds_sharpe_for_a_positively_skewed_curve(self) -> None:
        equity = [100.0, 99.5, 99.0, 108.0, 107.5, 118.0]
        m = compute_metrics(_days(6), equity, [], warn=no_warnings)
        assert m.sortino > m.sharpe > 0

    def test_max_drawdown_of_an_empty_curve_is_flat_and_dateless(self) -> None:
        assert max_drawdown([], []) == (0.0, None, None)


class TestGuards:
    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="must be the same length"):
            compute_metrics(_days(3), [100.0, 101.0], [], warn=no_warnings)

    def test_an_empty_curve_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty equity curve"):
            compute_metrics([], [], [], warn=no_warnings)


class TestWarningPolicy:
    def test_the_policy_sees_the_finished_object(self) -> None:
        """It is called once, on a complete `Metrics`, so it can gate on any field."""
        seen: list[Metrics] = []

        def policy(m: Metrics) -> tuple[str, ...]:
            seen.append(m)
            return (f"{m.n_trades} trades over {m.years:.2f} years",)

        m = compute_metrics(_days(4), [100.0, 101.0, 100.0, 103.0], [_Trade(5.0, 2)], warn=policy)
        assert len(seen) == 1
        assert seen[0].n_trades == 1
        assert m.warnings == ("1 trades over 0.01 years",)

    def test_the_policy_cannot_be_forgotten(self) -> None:
        """A run scored without a gate must be a deliberate `no_warnings`, not an omission."""
        with pytest.raises(TypeError):
            compute_metrics(_days(2), [100.0, 101.0], [])  # type: ignore[call-arg]

    def test_no_warnings_is_explicit_and_silent(self) -> None:
        m = compute_metrics(_days(2), [100.0, 101.0], [], warn=no_warnings)
        assert m.warnings == ()

    def test_this_package_ships_no_threshold(self) -> None:
        """The regression this wave exists to prevent: a consumer's gate number leaking back in.

        `no_warnings` is the only policy here, and it says nothing. If a default threshold ever
        reappears, some caller will silently inherit another system's standard.
        """
        m = compute_metrics(_days(2), [100.0, 90.0], [], warn=no_warnings)
        assert m.warnings == ()
        assert m.n_trades == 0  # zero trades, no sample, and still no opinion about it
