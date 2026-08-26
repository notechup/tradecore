"""The Databento cost probe, offline.

Every test here fakes the transport. That is not only for speed: this module's whole promise is
that it spends nothing, so its test suite must not be able to reach a billing endpoint either.
Nothing in here opens a socket.

The probes and datasets below are invented — an equities dataset, a fictional ticker. That is
deliberate: a shared package tested exclusively against one consumer's universe grows defaults
shaped like that universe without anyone deciding to.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.parse
from typing import Any, Self

import pytest

from tradecore.data import databento_cost as dc

#: An equities dataset and an ordinary equity pull. Nothing here is any consumer's real config.
EQUITIES = "XNAS.ITCH"
PROBE = dc.CostProbe(
    label="daily bars, one ticker",
    symbols="ABCD",
    stype_in="raw_symbol",
    schema="ohlcv-1d",
    why="a plain daily-bar pull, which is the shape most consumers ask for first",
)


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class Recorder:
    """Records outgoing requests and answers each from a queued payload."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.queue: list[object] = []

    def __call__(self, request: Any, timeout: float | None = None) -> FakeResponse:
        self.calls.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "body": request.data.decode() if request.data else None,
                "auth": request.headers.get("Authorization"),
                "timeout": timeout,
            }
        )
        return FakeResponse(self.queue.pop(0))


@pytest.fixture
def capture(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(dc.urllib.request, "urlopen", recorder)
    return recorder


class TestAuth:
    def test_api_key_is_the_username_and_the_password_is_empty(self) -> None:
        # Databento's own client does `HTTPBasicAuth(username=key, password="")`; getting this
        # backwards fails as a 401 that reads like a bad key rather than a bad header.
        assert dc._auth_header("mykey") == "Basic bXlrZXk6"

    def test_the_key_is_never_placed_in_a_url(self, capture: Recorder) -> None:
        capture.queue.append({"start": "2018-01-01", "end": "2026-08-21"})
        dc.dataset_range("secret-key", EQUITIES)
        assert "secret-key" not in capture.calls[0]["url"]
        assert capture.calls[0]["auth"] == dc._auth_header("secret-key")


class TestDatasetRange:
    def test_range_is_a_get_with_the_dataset_in_the_query(self, capture: Recorder) -> None:
        capture.queue.append({"start": "2018-01-01", "end": "2026-08-21"})
        assert dc.dataset_range("k", EQUITIES) == ("2018-01-01", "2026-08-21")
        assert capture.calls[0]["method"] == "GET"
        assert capture.calls[0]["url"].startswith(f"{dc.GATEWAY}/metadata.get_dataset_range?")
        assert f"dataset={EQUITIES}" in capture.calls[0]["url"]
        assert capture.calls[0]["body"] is None

    def test_an_unreadable_payload_is_surfaced_not_guessed_at(self, capture: Recorder) -> None:
        capture.queue.append({"schema": {"ohlcv-1d": {"start": "2018-01-01"}}})
        with pytest.raises(RuntimeError, match="could not read start/end"):
            dc.dataset_range("k", EQUITIES)


class TestCost:
    def test_a_us_equities_quote_returns_a_number(self, capture: Recorder) -> None:
        """Wave 2's gate: quote an equities dataset, and carry it through to the wire."""
        capture.queue.append(0.42)
        usd, priced_to = dc.estimate_cost("k", PROBE, "2018-01-01", "2026-08-21", EQUITIES)
        assert usd == pytest.approx(0.42)
        assert priced_to == "2026-08-21"
        call = capture.calls[0]
        assert call["method"] == "POST"
        assert call["url"] == f"{dc.GATEWAY}/metadata.get_cost"
        body = urllib.parse.parse_qs(call["body"])
        assert body["dataset"] == [EQUITIES]
        assert body["symbols"] == [PROBE.symbols]
        assert body["schema"] == [PROBE.schema]
        assert body["stype_in"] == [PROBE.stype_in]

    @pytest.mark.parametrize("payload", [1.5, {"cost": 1.5}, {"total_cost": 1.5}, {"usd": 1.5}])
    def test_a_cost_is_read_from_any_of_the_shapes_the_api_has_used(
        self, capture: Recorder, payload: object
    ) -> None:
        capture.queue.append(payload)
        assert dc.estimate_cost("k", PROBE, "a", "b", EQUITIES)[0] == pytest.approx(1.5)

    def test_an_unrecognised_payload_raises_rather_than_returning_zero(
        self, capture: Recorder
    ) -> None:
        # Returning 0.0 here would read as "free" — the most expensive possible bug in a module
        # whose entire job is to say what something costs.
        capture.queue.append({"quantity": 12})
        with pytest.raises(RuntimeError, match="could not read a cost"):
            dc.estimate_cost("k", PROBE, "a", "b", EQUITIES)


class TestNoDatasetDefault:
    """No entry point may acquire a dataset default. Asserted, not left to review."""

    @pytest.mark.parametrize(
        "call",
        [
            lambda: dc.dataset_range("k"),
            lambda: dc.estimate_cost("k", PROBE, "a", "b"),
            lambda: dc.dataset_condition("k", "a", "b"),
        ],
        ids=["dataset_range", "estimate_cost", "dataset_condition"],
    )
    def test_every_entry_point_requires_a_dataset(self, call: Any) -> None:
        """A forgotten dataset is a TypeError here, not another system's market quietly.

        The failure this prevents is not a crash — it is a plausible quote for the wrong
        market, which reads as a real answer.
        """
        with pytest.raises(TypeError):
            call()


class TestSchemaBoundRetry:
    """The dataset range runs to now; a daily schema stops at the last completed session.

    Databento rejects the overshoot AND names the real bound. Reading the bound out of the
    rejection is the difference between a tool that works and one that hands its operator a 422
    to translate by hand.
    """

    def _rejection(self, available_end: str) -> urllib.error.HTTPError:
        body = json.dumps(
            {"detail": {"payload": {"available_end": available_end}}, "case": "range"}
        ).encode()
        return urllib.error.HTTPError(
            url=f"{dc.GATEWAY}/metadata.get_cost",
            code=422,
            msg="Unprocessable Entity",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(body),
        )

    def test_it_retries_once_at_the_bound_the_vendor_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        attempts: list[str] = []

        def fake(request: Any, timeout: float | None = None) -> FakeResponse:
            end = urllib.parse.parse_qs(request.data.decode())["end"][0]
            attempts.append(end)
            if end == "2026-08-27":
                raise self._rejection("2026-08-26")
            return FakeResponse(3.25)

        monkeypatch.setattr(dc.urllib.request, "urlopen", fake)
        usd, priced_to = dc.estimate_cost("k", PROBE, "2018-01-01", "2026-08-27", EQUITIES)
        assert usd == pytest.approx(3.25)
        assert priced_to == "2026-08-26"
        assert attempts == ["2026-08-27", "2026-08-26"]

    def test_it_does_not_retry_forever_on_a_bound_that_does_not_help(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A bound at or past the end asked for cannot make progress, so it must raise."""
        attempts: list[str] = []

        def fake(request: Any, timeout: float | None = None) -> FakeResponse:
            attempts.append(urllib.parse.parse_qs(request.data.decode())["end"][0])
            raise self._rejection("2026-08-27")

        monkeypatch.setattr(dc.urllib.request, "urlopen", fake)
        with pytest.raises(dc.DatabentoError):
            dc.estimate_cost("k", PROBE, "2018-01-01", "2026-08-27", EQUITIES)
        assert len(attempts) == 1


class TestErrorsAreTheVendorsOwnWords:
    def test_an_http_error_body_is_carried_through_verbatim(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = json.dumps({"detail": "symbology: unknown stype_in 'continuous'"}).encode()

        def fake(request: Any, timeout: float | None = None) -> FakeResponse:
            raise urllib.error.HTTPError(
                url=f"{dc.GATEWAY}/metadata.get_cost",
                code=422,
                msg="Unprocessable Entity",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(body),
            )

        monkeypatch.setattr(dc.urllib.request, "urlopen", fake)
        with pytest.raises(dc.DatabentoError, match="unknown stype_in"):
            dc.estimate_cost("k", PROBE, "a", "b", EQUITIES)

    def test_an_unreachable_gateway_says_so_plainly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake(request: Any, timeout: float | None = None) -> FakeResponse:
            raise urllib.error.URLError("name or service not known")

        monkeypatch.setattr(dc.urllib.request, "urlopen", fake)
        with pytest.raises(dc.DatabentoError, match="could not reach"):
            dc.dataset_range("k", EQUITIES)


class TestTheModuleCannotSpendMoney:
    def test_it_exposes_no_way_to_download_data(self) -> None:
        """The safety property, asserted rather than trusted to a docstring.

        `metadata.*` endpoints quote; `timeseries.*` and `batch.*` transfer and bill. If a later
        edit adds one, this fails and the author has to argue for it.
        """
        source = dc.__file__ or ""
        assert source.endswith("databento_cost.py")
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        assert "timeseries." not in text
        assert "batch." not in text
        # And every endpoint it does call is a metadata one.
        assert text.count('_call("') == text.count('_call("metadata.')
