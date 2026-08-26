"""Mechanism tests. No rate here is anyone's real schedule.

Every schedule below is constructed for the property under test, with deliberately round
numbers so an assertion reads as arithmetic rather than as a claim about a broker. The one
exception is `test_vendor_published_worked_example`, which reproduces a figure published by a
broker precisely because an independent source is the strongest check available on the one
ordering decision that fails silently.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from tradecore.contracts import Side
from tradecore.costs import CostModel, CostSchedule, Liquidity

D = Decimal


def schedule(**overrides: object) -> CostSchedule:
    """A zeroed schedule, with only what a test switches on.

    Starting from zero rather than from anything plausible is what keeps these tests about
    mechanism: a figure that is not named by the test cannot influence its result.
    """
    base = CostSchedule(
        as_of="test fixture — not a real schedule",
        commission_per_share=D("0"),
        commission_minimum=D("0"),
        commission_maximum_pct_of_notional=None,
        ecn_take_fee_per_share=D("0"),
        ecn_make_rebate_per_share=D("0"),
        sec_fee_per_million=D("0"),
        taf_per_share=D("0"),
        taf_cap_per_trade=D("0"),
        clearing_per_share=D("0"),
        clearing_max_pct_of_notional=None,
        cat_fee_per_share=D("0"),
        pass_through_pct_of_commission=D("0"),
        short_locate_per_share=D("0"),
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------
# No defaults. This is the design, so it gets a test.
# --------------------------------------------------------------------------------------


def test_schedule_has_no_defaults() -> None:
    """The point of the module. A rate that can be omitted is a rate that will be omitted,
    and it would be silently wrong for every caller it was not written for."""
    with pytest.raises(TypeError):
        CostSchedule()  # type: ignore[call-arg]


def test_as_of_must_say_something() -> None:
    """A rate without provenance is a memory, not a rate."""
    with pytest.raises(ValueError, match="as_of"):
        schedule(as_of="   ")


def test_negative_rates_are_rejected() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        schedule(commission_per_share=D("-0.01"))


def test_a_cap_may_be_absent_but_not_zero() -> None:
    """None means "no cap"; zero would make the capped term free, which is a different and
    much worse claim."""
    assert schedule(commission_maximum_pct_of_notional=None) is not None
    with pytest.raises(ValueError, match="not a cost model"):
        schedule(commission_maximum_pct_of_notional=D("0"))
    with pytest.raises(ValueError, match="not a cost model"):
        schedule(clearing_max_pct_of_notional=D("0"))


# --------------------------------------------------------------------------------------
# The ordering that fails silently
# --------------------------------------------------------------------------------------


def test_maximum_is_applied_after_the_minimum() -> None:
    """The cap must be able to cut BELOW the floor. Reversed, it can never bind."""
    s = schedule(
        commission_per_share=D("0.005"),
        commission_minimum=D("1.00"),
        commission_maximum_pct_of_notional=D("0.01"),
    )
    # 10 x 0.005 = 0.05 -> floored to 1.00 -> capped at 10 x 0.20 x 1% = 0.02
    assert CostModel(s).compute(side=Side.BUY, qty=10, price=0.20, liquidity=Liquidity.TAKE) == D(
        "0.02"
    )


def test_vendor_published_worked_example() -> None:
    """Interactive Brokers publishes this example as a footnote to its US commission table:
    buying 10 shares of a $0.20 stock under a $0.005/share, $1.00-minimum, 1%-maximum plan is
    charged **$0.02**.

    An independent published figure is the strongest available check on the ordering above —
    stronger than reasoning about it, which is how it was originally got right by luck.
    """
    s = schedule(
        commission_per_share=D("0.005"),
        commission_minimum=D("1.00"),
        commission_maximum_pct_of_notional=D("0.01"),
    )
    assert CostModel(s).compute(side=Side.BUY, qty=10, price=0.20, liquidity=Liquidity.TAKE) == D(
        "0.02"
    )


def test_minimum_governs_below_the_crossover_and_the_rate_above_it() -> None:
    s = schedule(commission_per_share=D("0.01"), commission_minimum=D("1.00"))
    m = CostModel(s)
    assert m.compute(side=Side.BUY, qty=50, price=10.0, liquidity=Liquidity.TAKE) == D("1.00")
    assert m.compute(side=Side.BUY, qty=100, price=10.0, liquidity=Liquidity.TAKE) == D("1.00")
    assert m.compute(side=Side.BUY, qty=200, price=10.0, liquidity=Liquidity.TAKE) == D("2.00")


# --------------------------------------------------------------------------------------
# Which side a fee attaches to
# --------------------------------------------------------------------------------------


def test_sec_and_taf_are_sell_side_only() -> None:
    """A round trip pays them once, not twice. This is statutory, not a calibration."""
    s = schedule(
        sec_fee_per_million=D("20.00"), taf_per_share=D("0.001"), taf_cap_per_trade=D("100")
    )
    m = CostModel(s)
    assert m.compute(side=Side.BUY, qty=1000, price=10.0, liquidity=Liquidity.TAKE) == D("0.00")
    # 10,000 notional -> 0.20 SEC; 1000 x 0.001 = 1.00 TAF
    assert m.compute(side=Side.SELL, qty=1000, price=10.0, liquidity=Liquidity.TAKE) == D("1.20")


def test_clearing_and_cat_are_charged_on_both_sides() -> None:
    """Broker schedules write CAT per *quantity* and TAF per *quantity sold*. Getting that
    backwards under-charges every buy or double-charges every round trip."""
    s = schedule(clearing_per_share=D("0.0002"), cat_fee_per_share=D("0.000003"))
    m = CostModel(s)
    buy = m.compute(side=Side.BUY, qty=10_000, price=10.0, liquidity=Liquidity.TAKE)
    sell = m.compute(side=Side.SELL, qty=10_000, price=10.0, liquidity=Liquidity.TAKE)
    assert buy == sell == D("2.03")


def test_taf_is_capped_per_trade() -> None:
    s = schedule(taf_per_share=D("0.001"), taf_cap_per_trade=D("5.00"))
    huge = CostModel(s).compute(side=Side.SELL, qty=10_000_000, price=2.0, liquidity=Liquidity.TAKE)
    assert huge == D("5.00")


def test_clearing_has_its_own_cap_independent_of_the_commission_cap() -> None:
    s = schedule(clearing_per_share=D("1.00"), clearing_max_pct_of_notional=D("0.005"))
    # 100 x 1.00 = 100 uncapped; 0.5% of 1,000 notional = 5.00
    assert CostModel(s).compute(side=Side.BUY, qty=100, price=10.0, liquidity=Liquidity.TAKE) == D(
        "5.00"
    )


def test_short_locate_applies_only_to_a_short_sale() -> None:
    s = schedule(short_locate_per_share=D("0.02"))
    m = CostModel(s)
    assert m.compute(side=Side.SELL, qty=100, price=10.0, liquidity=Liquidity.TAKE) == D("0.00")
    assert m.compute(
        side=Side.SELL, qty=100, price=10.0, liquidity=Liquidity.TAKE, is_short=True
    ) == D("2.00")
    # A short flag on a BUY is a cover, and does not pay a locate.
    assert m.compute(
        side=Side.BUY, qty=100, price=10.0, liquidity=Liquidity.TAKE, is_short=True
    ) == D("0.00")


def test_locate_scales_with_days_and_is_never_less_than_one() -> None:
    s = schedule(short_locate_per_share=D("0.02"))
    m = CostModel(s)
    three = m.compute(
        side=Side.SELL, qty=100, price=10.0, liquidity=Liquidity.TAKE, is_short=True, locate_days=3
    )
    zero = m.compute(
        side=Side.SELL, qty=100, price=10.0, liquidity=Liquidity.TAKE, is_short=True, locate_days=0
    )
    assert three == D("6.00")
    assert zero == D("2.00"), "a zero-day borrow still costs one day"


# --------------------------------------------------------------------------------------
# Pass-throughs, rebates, and the zero floor
# --------------------------------------------------------------------------------------


def test_pass_throughs_scale_with_commission_not_with_trade_value() -> None:
    """They are a fraction OF THE COMMISSION. Applied to notional they are wrong by orders of
    magnitude at any realistic price."""
    s = schedule(commission_minimum=D("100.00"), pass_through_pct_of_commission=D("0.001"))
    m = CostModel(s)
    cheap = m.compute(side=Side.BUY, qty=1, price=1.0, liquidity=Liquidity.TAKE)
    rich = m.compute(side=Side.BUY, qty=1, price=1000.0, liquidity=Liquidity.TAKE)
    assert cheap == rich == D("100.10")


def test_making_liquidity_is_cheaper_than_taking_it() -> None:
    s = schedule(
        commission_minimum=D("1.00"),
        ecn_take_fee_per_share=D("0.003"),
        ecn_make_rebate_per_share=D("0.002"),
    )
    m = CostModel(s)
    take = m.compute(side=Side.BUY, qty=100, price=10.0, liquidity=Liquidity.TAKE)
    make = m.compute(side=Side.BUY, qty=100, price=10.0, liquidity=Liquidity.MAKE)
    assert take == D("1.30")
    assert make == D("0.80")


def test_a_rebate_can_never_become_income() -> None:
    """Treating fees as a revenue line is how a strategy ends up "profitable" on rebates it
    would not reliably capture."""
    s = schedule(ecn_make_rebate_per_share=D("0.10"))
    assert CostModel(s).compute(side=Side.BUY, qty=1000, price=2.0, liquidity=Liquidity.MAKE) == D(
        "0.00"
    )


def test_zero_quantity_is_rejected() -> None:
    with pytest.raises(ValueError, match="qty must be positive"):
        CostModel(schedule()).compute(side=Side.BUY, qty=0, price=2.0, liquidity=Liquidity.TAKE)


def test_round_trip_pays_sell_side_fees_once() -> None:
    s = schedule(commission_minimum=D("1.00"), sec_fee_per_million=D("20.00"))
    rt = CostModel(s).round_trip_estimate(qty=100, price=10.0)
    # 2 x 1.00 commission + 0.02 SEC on the sell only (1,000 notional)
    assert rt == D("2.02")


def test_short_round_trip_sells_first() -> None:
    """Entry is the sale, so the locate is charged on the entry, not the exit."""
    s = schedule(short_locate_per_share=D("0.02"))
    assert CostModel(s).round_trip_estimate(qty=100, price=10.0, is_short=True) == D("2.00")


def test_a_zero_schedule_is_free_and_that_is_a_deliberate_statement() -> None:
    """Every term zero means the caller asserted the plan charges nothing. It is not the same
    as forgetting to supply rates, which `test_schedule_has_no_defaults` makes impossible."""
    assert CostModel(schedule()).compute(
        side=Side.SELL, qty=100, price=10.0, liquidity=Liquidity.TAKE
    ) == D("0.00")
