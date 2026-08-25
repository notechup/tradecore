"""Core message contracts shared across all AURUM services.

These types are the ONLY way services talk to each other (data -> strategy -> risk ->
execution). Keeping them in one place matches the architecture principle in
docs/01_ARCHITECTURE.md: "Loosely coupled services with typed message contracts."

Every message that crosses a service boundary must be one of these types, must be
timestamped, and must be persisted (see docs/01_ARCHITECTURE.md: "Everything is logged
and replayable").
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


def _now_utc() -> datetime:
    """Timezone-AWARE current UTC.

    Every `ts` on these contracts is aware UTC. `datetime.utcnow()` is deprecated in 3.12 and
    returned a naive datetime, which could not be compared against the aware timestamps used
    in the data and risk layers without a normalisation step. See docs/DECISIONS.md D010.
    """
    return datetime.now(UTC)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"


class RiskEventKind(str, Enum):
    HALT = "HALT"
    LIMIT_BREACH = "LIMIT_BREACH"
    RECONCILE_FAIL = "RECONCILE_FAIL"
    KILL = "KILL"


class TargetPosition(BaseModel):
    """Emitted by a strategy module. Expresses desired exposure, NOT an order.

    Only the risk engine is allowed to turn this into an OrderIntent.
    """

    strategy_id: str
    instrument: str
    target_qty: float
    conviction: float = Field(ge=0.0, le=1.0)
    ttl: datetime  # signal expires after this timestamp if not acted on
    reason: str
    ts: datetime = Field(default_factory=_now_utc)


class OrderIntent(BaseModel):
    """Emitted by the risk engine after applying limits in docs/05_RISK.md.

    This is the only message type the execution layer is allowed to act on.
    """

    instrument: str
    side: Side
    qty: float
    order_type: OrderType
    limit_px: float | None = None
    #: Trigger price for a STOP order. Optional here because this is mechanism, not policy:
    #: whether a strategy is *allowed* to emit a stopless intent is a risk-engine question and
    #: each system answers it differently (docs/07_SHARED_CORE.md, Wave 0). AURUM's engine
    #: does not require one; BELLATOR's will reject an intent without it.
    stop_px: float | None = None
    source_strategy: str
    risk_checks_passed: list[str]
    ts: datetime = Field(default_factory=_now_utc)


class Fill(BaseModel):
    """Emitted by a broker adapter when an order (partially) executes."""

    order_id: str
    instrument: str
    #: Required, not derived. `qty` is unsigned, so without this a fill cannot be reconciled
    #: against a position except by inferring direction from the order that caused it — and
    #: the reconciliation loop exists precisely for the case where that link is untrustworthy.
    side: Side
    qty: float
    px: float
    ts: datetime
    fees: float = 0.0


class RiskEvent(BaseModel):
    """Emitted by the risk engine or any service on a fail-closed condition.

    See docs/05_RISK.md "Loss limits & circuit breakers" and "Kill switch".
    """

    kind: RiskEventKind
    detail: str
    ts: datetime = Field(default_factory=_now_utc)
