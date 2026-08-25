"""Contract invariants for src/aurum/common/contracts.py.

The field SET is fixed by docs/01_ARCHITECTURE.md ("Message contracts") and is asserted by the
architecture tests. What this file guards is the one property that section does not spell out
but the whole system depends on: every auto-stamped `ts` is timezone-AWARE UTC
(docs/DECISIONS.md D010). A naive default would compare-fail against the aware timestamps the
data and risk layers use, and a comparison that raises inside the risk path is a HALT.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tradecore.contracts import (
    OrderIntent,
    OrderType,
    RiskEvent,
    RiskEventKind,
    Side,
    TargetPosition,
)


def _target() -> TargetPosition:
    return TargetPosition(
        strategy_id="S1",
        instrument="MGC",
        target_qty=1.0,
        conviction=1.0,
        ttl=datetime.now(UTC) + timedelta(hours=1),
        reason="unit test",
    )


def _intent() -> OrderIntent:
    return OrderIntent(
        instrument="MGC",
        side=Side.BUY,
        qty=1.0,
        order_type=OrderType.MARKET,
        source_strategy="S1",
        risk_checks_passed=[],
    )


def _event() -> RiskEvent:
    return RiskEvent(kind=RiskEventKind.HALT, detail="unit test")


def test_default_timestamps_are_timezone_aware_utc() -> None:
    for msg in (_target(), _intent(), _event()):
        assert msg.ts.tzinfo is not None, f"{type(msg).__name__}.ts default is naive"
        assert msg.ts.utcoffset() == timedelta(0), f"{type(msg).__name__}.ts default is not UTC"


def test_default_timestamps_compare_against_aware_datetimes() -> None:
    """The regression the naive default caused: this raised TypeError."""
    now = datetime.now(UTC)
    for msg in (_target(), _intent(), _event()):
        assert abs((msg.ts - now).total_seconds()) < 60


def test_order_intent_limit_px_is_optional() -> None:
    assert _intent().limit_px is None
    assert (
        OrderIntent(
            instrument="MGC",
            side=Side.BUY,
            qty=1.0,
            order_type=OrderType.LIMIT,
            limit_px=2400.5,
            source_strategy="S1",
            risk_checks_passed=[],
        ).limit_px
        == 2400.5
    )
