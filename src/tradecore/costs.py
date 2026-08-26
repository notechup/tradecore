"""US equity transaction costs: the arithmetic, with none of the rates.

**This module ships no default rate, deliberately, and that is the whole design.** Every
figure in a fee schedule is broker-, venue-, plan- and date-specific, and the materiality of
any *fixed* per-order term — a minimum, a cap — is entirely a function of position size. A
per-order floor that is a rounding error at institutional notionals is the dominant term two
orders of magnitude down: the fee does not move, the position does. A default would therefore
be silently wrong for every caller it did not happen to be written for, and wrong in a
direction nobody checked.

So `CostSchedule` has no defaults: a consumer must state its own numbers, and the type system
makes forgetting impossible. The rates are that system's policy and live in that system's
repo. What lives here is the part both systems agree on: which fees exist, which side of a
trade they attach to, and the order they combine in.

Two of those are easy to get wrong and are the reason this is shared rather than reimplemented:

* **The per-order maximum is applied AFTER the minimum**, because it must be able to cut below
  it. Broker schedules state this explicitly — a 10-share order in a $0.20 stock under a
  $1.00-minimum / 1%-maximum plan is charged $0.02, not $1.00. Under the opposite ordering the
  cap can never bind and the code still looks correct.
* **SEC Section 31 and FINRA TAF are sell-side only**, so a round trip pays them once, not
  twice. Clearing and consolidated-audit-trail fees are charged on both sides. A model with no
  concept of the distinction cannot be calibrated into one that has it.

Money is `Decimal` throughout. Fees are a P&L line and binary floating point does not belong
in one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from tradecore.contracts import Side

__all__ = ["CostModel", "CostSchedule", "Liquidity"]

_CENT = Decimal("0.01")
_PER_MILLION = Decimal(1000000)
_ZERO = Decimal(0)


class Liquidity(str, Enum):
    """Whether an execution removed resting liquidity or added it.

    Not an intraday-only concept: a market-on-open order takes liquidity as surely as a
    marketable limit does, and under a pass-through commission plan that is a real per-share
    fee. A consumer that does not model the distinction passes ``TAKE``, which is the
    pessimistic choice.
    """

    TAKE = "TAKE"
    MAKE = "MAKE"


@dataclass(frozen=True, slots=True)
class CostSchedule:
    """One broker's fee schedule. **Every field is required — there are no defaults.**

    Set a term to ``Decimal("0")`` to say a plan does not charge it. That is an assertion the
    caller makes explicitly, which is the point: an omitted fee and a zero fee are the same
    number and very different claims.
    """

    as_of: str
    """Provenance. When these rates were checked, against what, and what remains unverified.

    Required rather than optional because a rate without a date is not a rate, it is a memory.
    Fee schedules change without notice: statutory rates are reset on their own calendars and
    broker schedules change commercially.
    """

    commission_per_share: Decimal
    """Broker commission per share, before the minimum and maximum below."""

    commission_minimum: Decimal
    """Per-order floor. At small share counts this is usually the entire commission."""

    commission_maximum_pct_of_notional: Decimal | None
    """Per-order ceiling as a fraction of trade value. ``None`` for a plan with no cap.

    Applied **after** the minimum — see the module docstring. This is the one ordering
    decision here that fails silently when reversed.
    """

    ecn_take_fee_per_share: Decimal
    """Exchange/venue fee for removing liquidity. Zero on plans that absorb venue fees rather
    than passing them through."""

    ecn_make_rebate_per_share: Decimal
    """Credit for adding liquidity. Modelled, but see ``CostModel.compute``: the total is
    floored at zero rather than allowed to become income."""

    sec_fee_per_million: Decimal
    """SEC Section 31 fee on notional, **sells only**. Reset by the SEC on its own schedule and
    it has moved by more than 5x between recent years."""

    taf_per_share: Decimal
    """FINRA Trading Activity Fee, **sells only**."""

    taf_cap_per_trade: Decimal
    """Per-trade ceiling on the TAF. Partial executions each count as one trade."""

    clearing_per_share: Decimal
    """NSCC/DTC clearing, **both sides**."""

    clearing_max_pct_of_notional: Decimal | None
    """Ceiling on the clearing fee as a fraction of trade value, or ``None``. Separate from the
    commission cap and applied independently."""

    cat_fee_per_share: Decimal
    """FINRA Consolidated Audit Trail, **both sides**. Note that broker schedules express this
    per *quantity* while TAF is per *quantity sold*; that one-word difference decides whether a
    fee is paid once or twice per round trip."""

    pass_through_pct_of_commission: Decimal
    """Exchange and regulatory pass-throughs levied as a fraction **of the commission**, not of
    the trade. Sum the individual pass-through rates into this one figure. Applied to notional
    instead they are wrong by orders of magnitude at any realistic price."""

    short_locate_per_share: Decimal
    """Borrow cost per share per day for a short sale. Charged only when a sale is flagged
    short. Zero for a long-only system."""

    def __post_init__(self) -> None:
        for name in (
            "commission_per_share",
            "commission_minimum",
            "ecn_take_fee_per_share",
            "ecn_make_rebate_per_share",
            "sec_fee_per_million",
            "taf_per_share",
            "taf_cap_per_trade",
            "clearing_per_share",
            "cat_fee_per_share",
            "pass_through_pct_of_commission",
            "short_locate_per_share",
        ):
            if getattr(self, name) < _ZERO:
                raise ValueError(f"{name} must be non-negative")
        for name in ("commission_maximum_pct_of_notional", "clearing_max_pct_of_notional"):
            pct = getattr(self, name)
            if pct is not None and pct <= _ZERO:
                raise ValueError(
                    f"{name} must be positive, or None for no cap; got {pct}. "
                    "Zero would make the capped term free, which is not a cost model."
                )
        if not self.as_of.strip():
            raise ValueError("as_of must say when these rates were checked and against what")


class CostModel:
    """Applies a `CostSchedule` to a fill. Returned values are positive = money out."""

    __slots__ = ("schedule",)

    def __init__(self, schedule: CostSchedule) -> None:
        self.schedule = schedule

    def compute(
        self,
        *,
        side: Side,
        qty: int,
        price: float,
        liquidity: Liquidity,
        is_short: bool = False,
        locate_days: int = 1,
    ) -> Decimal:
        """Total cost of one fill, rounded to the cent.

        **Known pessimistic simplification:** the per-order minimum is charged **per fill**. A
        real per-order minimum is charged once per order however many fills it takes, so a
        partially filled order is over-charged here. That is deliberate — an ambiguous case
        should resolve against the trader — but it is a modelling choice rather than any
        broker's schedule, and it is why a fill-level total can exceed what is actually billed.
        """
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")

        s = self.schedule
        shares = Decimal(qty)
        notional = Decimal(str(price)) * shares

        commission = s.commission_per_share * shares
        if s.commission_minimum > _ZERO:
            commission = max(commission, s.commission_minimum)
        if s.commission_maximum_pct_of_notional is not None:
            commission = min(commission, notional * s.commission_maximum_pct_of_notional)
        total = commission

        if liquidity is Liquidity.TAKE:
            total += s.ecn_take_fee_per_share * shares
        else:
            total -= s.ecn_make_rebate_per_share * shares

        clearing = s.clearing_per_share * shares
        if s.clearing_max_pct_of_notional is not None:
            clearing = min(clearing, notional * s.clearing_max_pct_of_notional)
        total += clearing
        total += s.cat_fee_per_share * shares
        total += commission * s.pass_through_pct_of_commission

        if side is Side.SELL:
            total += (notional / _PER_MILLION) * s.sec_fee_per_million
            total += min(s.taf_per_share * shares, s.taf_cap_per_trade)
            if is_short:
                total += s.short_locate_per_share * shares * Decimal(max(locate_days, 1))

        # A make rebate can exceed commission on a zero-commission route. Floor at zero rather
        # than model rebate income: treating fees as a revenue line is how a strategy ends up
        # "profitable" on rebates it would not reliably capture.
        return max(total, _ZERO).quantize(_CENT, rounding=ROUND_HALF_UP)

    def round_trip_estimate(self, *, qty: int, price: float, is_short: bool = False) -> Decimal:
        """Both sides at one price, taking liquidity on entry and exit.

        The cost hurdle a trade must clear before it makes anything. Compare it against
        expected reward *before* taking the trade, not after.
        """
        entry_side = Side.SELL if is_short else Side.BUY
        exit_side = Side.BUY if is_short else Side.SELL
        return self.compute(
            side=entry_side, qty=qty, price=price, liquidity=Liquidity.TAKE, is_short=is_short
        ) + self.compute(
            side=exit_side, qty=qty, price=price, liquidity=Liquidity.TAKE, is_short=False
        )
