# CLAUDE.md — tradecore

Read this before changing anything here. It is short because this repo is small and its rules
are few, but the first one is load-bearing in a way the others are not.

## This repo is PUBLIC

Everything committed here is on the internet, permanently. Making the repo private later
retracts nothing already fetched, cloned or cached — it is a **soft one-way door**, fully
reversible only for content *not yet pushed*.

So the audit happens **before the push**, every time, not after. Read the diff and ask: does
any of this describe a *system* rather than a *mechanism*?

## Share mechanism, never policy

The whole reason this package exists, and the only thing that can really go wrong in it.

| Belongs here (mechanism) | Never here (policy) |
|---|---|
| Message contracts, bar types | Risk limits, position sizing, portfolio caps |
| Broker adapters, connection plumbing | Which instruments a system trades, and its filters |
| Cost *arithmetic* — which fees exist, which side they attach to, the order they combine in | Cost **rates** — any broker's actual numbers |
| Error-code tables, paper-account guards | Strategies, signals, parameter grids |
| Metrics and walk-forward machinery | The threshold values a system gates on |

**The failure signal:** an `if project == ...` branch anywhere in this package means policy has
leaked in. Delete the branch and move the code back to the consumer. Since the repo is public,
that branch is not merely in the wrong module — it is the branch that publishes a limit, a
sizing rule or a universe filter.

**A subtler leak, worth naming because it has already been caught twice:** prose. A docstring
that illustrates an argument with a real account size, a real position notional or a real
strategy's numbers leaks exactly as effectively as a constant, and reads as harmless while
doing it. Write examples with round invented figures, or as ratios.

## No default rates, ever

`costs.CostSchedule` requires every field and has no defaults. That is deliberate and should
not be "improved".

A fixed per-order term's materiality is a pure function of position size — a floor that is a
rounding error at institutional notionals is the dominant cost two orders of magnitude down.
Both consuming systems shipped a defaulted rate that was wrong for the account trading it, in
opposite directions, and neither noticed until someone computed a number that looked odd. A
missing-argument `TypeError` arrives at the only moment the mistake is still cheap.

Zero is always available. It just has to be *stated*, because an omitted fee and a zero fee are
the same number and very different claims.

### The general rule, arrived at three times

Rates were the first case, not the only one. **The parameter that varies per consumer never
gets a default in this package.** So far:

| Module | Required, never defaulted | What a default would have done |
|---|---|---|
| `costs` | every rate in `CostSchedule` | charge one account's fee schedule to another |
| `metrics` | `warn`, the `WarningPolicy` | apply one system's validation gate to another's result |
| `data.databento_cost` | `dataset` | quote a plausible dollar figure **for the wrong market** |

Note what those three failures have in common: **none of them is a crash.** Each returns a
confident, well-formed, wrong answer that reads exactly like a right one. That is the whole
argument — a default here does not fail loudly in the consumer that forgot it, so the type
signature has to do the work that review will not.

This is the "share mechanism, never policy" line expressed as a function signature instead of a
review comment, and it is the only form of it that survives someone not reading this file.

## Nobody works on this repo for its own sake

There is no roadmap here and there should not be one. This package changes only because a
consumer needs something, and every such change belongs to a numbered wave with a named owner.

**The extraction plan is not in this repo** — it lives in the consuming systems' docs
(`docs/07_SHARED_CORE.md`), where one copy is canonical and the other is a verified replica.
It deliberately does not live here, and the reason is not that it happens to name things that
could be redacted: **it is a planning artifact about the consuming systems' roadmaps**, which
is not shared mechanism under the rule above whatever it names. A redacted copy would not
serve the purpose that makes anyone want it here. **Do not sync it in.** It is the single most
likely file to arrive here by reflex.

In practice this means work starts in a consumer's session, where the reason for the change is
known, and reaches this repo from there.

## Gate

Everything must pass before a push, and CI runs the same four:

```bash
ruff check . && ruff format --check . && mypy src && pytest
```

`tests/test_import_isolation.py` is the structural one: this package must import nothing from
any consumer, and must run on pydantic alone. It cannot detect a policy leak — that is a
judgement call and no import graph shows it — but it catches anyone reaching sideways into a
consumer, which would otherwise surface days later as a broken install somewhere else.

## Releasing

Consumers pin a **tag**, never a branch, via a PEP 508 direct reference. A backtest whose
result goes in a results table has to name the version it ran against, and a moving `main`
makes that unprovable.

So: commit, `git tag -a vX.Y.Z`, push both, then bump the consumer's pin. The version in
`pyproject.toml` and the tag must agree.

**Never publish to PyPI.** Consumers resolve by direct git reference, which never touches the
index.

## Dependencies

**pydantic is the only runtime dependency and it should stay that way.** A dependency added
here is added to every consumer of this package, in every environment, forever. The bar is not
"is it useful" — it is "would every consumer accept this in their install".

## Known cleanup, not yet done

`contracts.py` and `bars.py` moved here verbatim from their origin repo, which was the right
call — a byte-identical move is checkable by `cmp` rather than by reading. The cost is that
their docstrings still reference the origin system's internal docs and decision IDs by name.
None of it is a credential, a limit or an edge, so none of it blocked the push. It should be
cleaned up as its own change, not folded into an unrelated one.
