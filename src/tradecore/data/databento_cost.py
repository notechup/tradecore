"""Price a Databento historical pull BEFORE buying it. Metadata only — downloads nothing.

Databento meters historical data by bytes delivered and exposes `metadata.get_cost`, so a
request can be quoted in dollars before a single byte is transferred. This module is that call
and the two metadata calls around it, and nothing else.

**There is deliberately no download function here, and a test enforces it.** A module whose job
is "find out what this would cost" must not be one import away from spending the money it is
measuring. The metadata endpoints quote; the timeseries and batch families transfer and bill,
and this module reaches none of them. A loader that actually pulls bars is a separate thing and
belongs in the consumer, using the official `databento` client rather than this hand-rolled
HTTP.

(That test is a plain text scan of this file, so it cannot tell prose from code: writing the
billing endpoint names here in their dotted form fails it. That is a fair price — a scan strict
enough to be worth trusting is strict enough to be tripped by a docstring, and the alternative
is a scan that reasons about context and can be argued with.)

Note that the objection is **proximity**, not configuration: no change to this file dissolves
it, because a download function reachable from the pricing module is the hazard regardless of
how it is guarded. That is why the rule is structural and not a flag.

**No dataset default.** Every entry point takes `dataset` explicitly. The dataset is the single
parameter that decides what a consumer is buying and what it is charged, and a package shared
across systems that trade different asset classes has no defensible default for it — the same
reasoning that gives `tradecore.costs` no default rates and `tradecore.metrics` no default
thresholds. A missing argument is a `TypeError` at the only moment the mistake is still free.

**Vendor errors are surfaced verbatim, never re-interpreted.** Databento does not just say
"no": a 422 on a symbology string says which spelling was wrong, and a range complaint names
the real bound. `estimate_cost` reads that bound out of the rejection and retries once, rather
than handing the operator an HTTP status to translate by hand.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from dataclasses import dataclass

__all__ = [
    "ENV_KEY",
    "GATEWAY",
    "TIMEOUT",
    "CostProbe",
    "DatabentoError",
    "dataset_condition",
    "dataset_range",
    "estimate_cost",
]

#: Databento's historical REST gateway. Basic auth: API key as the username, empty password.
GATEWAY = "https://hist.databento.com/v0"

#: The environment variable the key is read from. The name is shared; the key never is, and
#: nothing here reads the environment — a caller passes the key in.
ENV_KEY = "DATABENTO_API_KEY"

#: Long enough that a hung request is obvious, short enough not to wedge a terminal.
TIMEOUT = 30.0


@dataclass(frozen=True)
class CostProbe:
    """One priced question. `symbols`/`stype_in` are the pair most likely to be wrong.

    `why` carries the consumer's reason for wanting this pull. It is free text and this module
    never reads it — it exists so a printed quote can say what the money would buy.
    """

    label: str
    symbols: str
    stype_in: str
    schema: str
    why: str


class DatabentoError(RuntimeError):
    """A failed metadata call, carrying Databento's parsed body when there was one.

    The parsed body matters: Databento says what the bound actually is, and `estimate_cost`
    reads that rather than making the operator translate an error into a parameter by hand.
    """

    def __init__(self, message: str, detail: object = None) -> None:
        super().__init__(message)
        self.detail = detail

    def available_end(self) -> str | None:
        """The schema's real end bound, if this error was a range complaint."""
        detail = self.detail
        if isinstance(detail, dict):
            inner = detail.get("detail")
            if isinstance(inner, dict):
                payload = inner.get("payload")
                if isinstance(payload, dict):
                    end = payload.get("available_end")
                    if isinstance(end, str):
                        return end
        return None


def _auth_header(key: str) -> str:
    return "Basic " + b64encode(f"{key}:".encode()).decode("ascii")


def _call(path: str, key: str, params: dict[str, str], *, post: bool) -> object:
    """One metadata call. Raises `DatabentoError` carrying Databento's own message on failure."""
    url = f"{GATEWAY}/{path}"
    body = urllib.parse.urlencode(params).encode() if post else None
    if not post:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST" if post else "GET",
        headers={"Authorization": _auth_header(key), "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace").strip()
        # Report what the vendor said rather than what the status code implies — a 422 on a
        # symbology string means "that spelling is wrong", and its body says which.
        try:
            parsed: object = json.loads(raw)
        except ValueError:
            parsed = None
        raise DatabentoError(f"HTTP {exc.code} from {path}: {raw or exc.reason}", parsed) from exc
    except urllib.error.URLError as exc:
        raise DatabentoError(f"could not reach {GATEWAY}: {exc.reason}") from exc


def dataset_range(key: str, dataset: str) -> tuple[str, str]:
    """(start, end) of the history Databento will actually serve, from the API itself.

    Prefer this over any published figure: the API is the primary source, and if a vendor page
    and this call disagree, this call wins.
    """
    payload = _call("metadata.get_dataset_range", key, {"dataset": dataset}, post=False)
    if not isinstance(payload, dict):
        # DatabentoError (a RuntimeError), not TypeError: nothing is wrong with the CALLER's
        # arguments — the remote sent a shape this module does not understand, and a CLI that
        # catches RuntimeError is precisely how the vendor's own words reach the operator.
        raise DatabentoError(f"unexpected dataset-range payload: {payload!r}")
    start, end = payload.get("start"), payload.get("end")
    if not (isinstance(start, str) and isinstance(end, str)):
        # Newer responses nest per-schema ranges; surface the whole thing rather than guess.
        # Also a remote payload problem rather than a caller mistake — see above.
        raise DatabentoError(f"could not read start/end from {json.dumps(payload)[:400]}")
    return start, end


def dataset_condition(
    key: str, start_date: str, end_date: str, dataset: str
) -> list[dict[str, object]]:
    """Per-day data quality for a dataset: `{date, condition, last_modified_date}`.

    Free, like every other metadata call, which is the point — quality information should never
    be something a run skips to save money. `condition` is returned verbatim rather than mapped
    to a local vocabulary: Databento owns those words ('available', 'degraded', and others this
    code has not met), and translating them here would mean inventing a meaning for a value it
    has never seen.
    """
    payload = _call(
        "metadata.get_dataset_condition",
        key,
        {"dataset": dataset, "start_date": start_date, "end_date": end_date},
        post=False,
    )
    if not isinstance(payload, list):
        raise DatabentoError(f"unexpected dataset-condition payload: {json.dumps(payload)[:400]}")
    return [row for row in payload if isinstance(row, dict)]


def estimate_cost(
    key: str, probe: CostProbe, start: str, end: str, dataset: str
) -> tuple[float, str]:
    """(US dollars, the end actually priced) for `probe` over [start, end).

    Transfers no market data. Per-schema coverage does not all end at the same instant — the
    dataset range runs to the current moment while a daily schema stops at the last completed
    session — so an end taken from `dataset_range` overshoots for daily bars. Rather than
    hardcode that relationship, one retry is made at whatever bound Databento names in the
    rejection, and the end actually used is returned so a caller cannot mistake a clamped quote
    for a full-range one.
    """
    try:
        return _cost_once(key, probe, start, end, dataset), end
    except DatabentoError as exc:
        clamped = exc.available_end()
        if clamped is None or clamped >= end:
            raise
    return _cost_once(key, probe, start, clamped, dataset), clamped


def _cost_once(key: str, probe: CostProbe, start: str, end: str, dataset: str) -> float:
    payload = _call(
        "metadata.get_cost",
        key,
        {
            "dataset": dataset,
            "start": start,
            "end": end,
            "symbols": probe.symbols,
            "schema": probe.schema,
            "stype_in": probe.stype_in,
            "stype_out": "instrument_id",
        },
        post=True,
    )
    if isinstance(payload, int | float):
        return float(payload)
    if isinstance(payload, dict):
        for field in ("cost", "total_cost", "usd"):
            value = payload.get(field)
            if isinstance(value, int | float):
                return float(value)
    # Returning 0.0 here would read as "free" — the most expensive possible bug in a module
    # whose entire job is to say what something costs.
    raise DatabentoError(f"could not read a cost from {json.dumps(payload)[:400]}")
