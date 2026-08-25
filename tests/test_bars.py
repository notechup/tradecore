"""Baseline invariants for src/aurum/common/bars.py.

`DailyBar` and `PriceAdjustment` had no tests of their own — they were exercised only through
their consumers (`test_s1_trend.py`, `test_backtest_engine.py`, `test_databento_history.py`),
which prove that *those* modules work, not that these types kept their shape. This file is
written deliberately **before** the docs/07_SHARED_CORE.md Wave 1 move into `tradecore`: the
gate for that wave is "AURUM's suite passes unedited", and a module with no direct tests is a
module the gate cannot vouch for.

So what is pinned here is what a move could silently drop: the OHLC validation, `frozen`,
`slots`, and the stored string values of `PriceAdjustment`.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from tradecore.bars import DailyBar, PriceAdjustment

DAY = date(2026, 8, 25)


def _bar(**over: object) -> DailyBar:
    fields: dict[str, object] = {"day": DAY, "o": 100.0, "h": 105.0, "l": 99.0, "c": 104.0}
    fields.update(over)
    return DailyBar(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# OHLC validation — the reason __post_init__ exists
# ---------------------------------------------------------------------------------------


def test_a_consistent_bar_is_accepted() -> None:
    bar = _bar()
    assert (bar.day, bar.o, bar.h, bar.l, bar.c) == (DAY, 100.0, 105.0, 99.0, 104.0)


def test_volume_is_optional_and_defaults_to_none() -> None:
    """FRED-style series carry no volume and no S1 rule reads it, so absent must not be 0.0 —
    a zero-volume session is a real thing and must stay distinguishable from an unknown one."""
    assert _bar().v is None
    assert _bar(v=0.0).v == 0.0


@pytest.mark.parametrize(
    ("name", "over"),
    [
        ("open above high", {"o": 106.0}),
        ("open below low", {"o": 98.0}),
        ("close above high", {"c": 106.0}),
        ("close below low", {"c": 98.0}),
        ("high below low", {"h": 98.0, "l": 99.0, "o": 98.5, "c": 98.5}),
    ],
)
def test_an_inconsistent_bar_is_refused_at_construction(name: str, over: dict[str, float]) -> None:
    """Fail closed at the boundary. A bar whose OHLC cannot have happened is bad data, and the
    backtest is the last place it should be discovered — every indicator downstream would
    quietly produce a number from it."""
    with pytest.raises(ValueError, match="refusing to backtest on it"):
        _bar(**over)


def test_the_rejection_names_the_session_and_the_prices() -> None:
    """The message is the whole diagnostic: a loader pulling 4,000 bars needs to know WHICH
    one, not that one of them was bad."""
    with pytest.raises(ValueError) as excinfo:
        _bar(o=106.0)
    message = str(excinfo.value)
    assert str(DAY) in message
    assert "o=106.0" in message and "h=105.0" in message


def test_a_flat_session_is_valid() -> None:
    """o == h == l == c. A limit-locked or untraded session is degenerate, not inconsistent,
    and the validation uses <= for exactly this reason."""
    assert _bar(o=100.0, h=100.0, l=100.0, c=100.0).c == 100.0


# ---------------------------------------------------------------------------------------
# Immutability — a bar is a fact about a session that has closed
# ---------------------------------------------------------------------------------------


def test_a_bar_cannot_be_mutated() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _bar().c = 999.0  # type: ignore[misc]


def test_a_bar_carries_no_dict_and_refuses_new_attributes() -> None:
    """`slots=True` is load-bearing, not decoration: bars are allocated per symbol per day in
    the backtest loop, and slots is also what stops a consumer from stapling vendor metadata
    onto a bar — which the module docstring rules out on purpose."""
    bar = _bar()
    assert not hasattr(bar, "__dict__")
    assert DailyBar.__slots__ == ("day", "o", "h", "l", "c", "v")
    #: Note the exception type: a frozen dataclass raises FrozenInstanceError for a *known*
    #: field, but `slots=True` makes CPython rebuild the class, and the rebuilt __setattr__
    #: raises TypeError for an unknown one. Pinned as observed rather than as assumed — the
    #: point of the assertion is that the write fails, not which of the two it fails as.
    with pytest.raises((TypeError, AttributeError)):
        bar.source = "databento"  # type: ignore[attr-defined]


def test_bars_compare_and_hash_by_value() -> None:
    """Frozen implies hashable, and the feed and the dedupe paths rely on it."""
    assert _bar() == _bar()
    assert _bar() != _bar(c=103.0)
    assert len({_bar(), _bar(), _bar(c=103.0)}) == 2


# ---------------------------------------------------------------------------------------
# PriceAdjustment — the stored values are a wire format, not labels
# ---------------------------------------------------------------------------------------


def test_price_adjustment_values_are_the_persisted_strings() -> None:
    """The loader writes these and the backtest feed reads them back (docs/06_DATA.md), so
    they are a storage format. Renaming a value silently re-labels every row already written —
    including the DIFFERENCE rows whose ratios are invalid (D025, D026)."""
    assert PriceAdjustment.NONE.value == "none"
    assert PriceAdjustment.DIFFERENCE.value == "difference"
    assert PriceAdjustment.RATIO.value == "ratio"
    assert [a.value for a in PriceAdjustment] == ["none", "difference", "ratio"]


def test_price_adjustment_round_trips_through_its_stored_value() -> None:
    for adjustment in PriceAdjustment:
        assert PriceAdjustment(adjustment.value) is adjustment


def test_price_adjustment_is_a_str_enum() -> None:
    """It is compared against raw strings out of the database without an explicit cast."""
    assert PriceAdjustment.NONE == "none"
    assert isinstance(PriceAdjustment.RATIO, str)
