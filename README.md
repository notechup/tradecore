# tradecore

Shared mechanism for systematic trading systems: message contracts, bar types, and the
plumbing that more than one system can agree on.

**Governing rule: share mechanism, never policy.** Adapters, contracts, cost models, metrics
and plumbing belong here. Risk limits, position sizing, strategies, universe definitions and
instrument specs never do — they stay in the system that owns them. An `if project == ...`
branch anywhere in this package means policy has leaked in: delete the branch and move the
code back out.

That rule is why this repo is public. Everything in it is by construction the part with no
secrets and no edge.

## Install

```bash
pip install "tradecore @ git+https://github.com/notechup/tradecore@v0.1.0"
```

Consumers pin a **tag**, not a branch. A backtest whose result goes in a results table has to
be able to name the `tradecore` version it ran against, and a moving `main` makes that
impossible.

For local development against a consumer repo, install the consumer normally and then
override with an editable checkout:

```bash
pip install -e ../tradecore
```

## Contents

| Module | What it is |
|---|---|
| `tradecore.contracts` | `TargetPosition`, `OrderIntent`, `Fill`, `RiskEvent`, `Side`, `OrderType`, `RiskEventKind` — pydantic boundary messages |
| `tradecore.bars` | `DailyBar` (frozen, slotted), `PriceAdjustment` |

## Development

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy src && pytest
```

`tests/test_import_isolation.py` asserts that this package imports nothing from a consumer
and needs nothing beyond pydantic at runtime. It cannot detect a policy leak — that is a
judgement call — but it catches the version where someone reaches back into a consumer by
accident.
