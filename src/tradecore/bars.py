"""The daily bar as it crosses the data -> strategy boundary.

`contracts.py` holds the messages that flow *forward* through the pipeline
(TargetPosition -> OrderIntent -> Fill). A bar is the input that starts the chain, and both
`aurum.strategy` and `aurum.backtest` need to agree on its shape, so it lives in `common`
rather than in either of them.

Deliberately minimal and immutable: a bar is a fact about a session that has closed. It
carries no vendor metadata — which feed it came from is a property of the `bars` table
(`source`, see docs/06_DATA.md), not something a strategy is allowed to branch on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

__all__ = ["DailyBar", "PriceAdjustment"]


class PriceAdjustment(str, Enum):
    """How a stored price series relates to the price that was actually traded.

    SERIES-level metadata, deliberately not a `DailyBar` field: it is a property of a whole
    series, and putting it on every bar would invite a rule to branch on it per bar. It lives
    here because the loader writes it and the backtest feed reads it, and they must agree.

    Why it has to be recorded at all: a continuous futures series is stitched from contracts
    that trade at different prices, and the stitching method decides which computations remain
    valid. `NONE` is the traded price, and everything is valid. `DIFFERENCE` adds a per-era
    constant, which preserves point moves and breaks ratios — measured at up to +12.9% of the
    true price on IBKR's CONTFUT GC (docs/DECISIONS.md D025, D026). `RATIO` multiplies, which
    preserves returns and ratios but not point distances.
    """

    NONE = "none"
    DIFFERENCE = "difference"
    RATIO = "ratio"


@dataclass(frozen=True, slots=True)
class DailyBar:
    """One completed daily session.

    `day` is the session's own date, not a timestamp — the storage layer stamps daily bars at
    00:00 UTC on the observation date (see `aurum.data.free_history`), and re-deriving a date
    from that in every consumer is how timezone bugs get in.

    Volume is optional: FRED-style series and some vendors do not carry it, and no S1 rule
    reads it.
    """

    day: date
    o: float
    h: float
    l: float
    c: float
    v: float | None = None

    def __post_init__(self) -> None:
        if not (self.l <= self.o <= self.h and self.l <= self.c <= self.h):
            raise ValueError(
                f"{self.day}: OHLC is inconsistent "
                f"(o={self.o} h={self.h} l={self.l} c={self.c}); refusing to backtest on it"
            )
