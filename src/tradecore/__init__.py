"""Shared mechanism for systematic trading systems.

Import the modules directly (`from tradecore.contracts import OrderIntent`). This package
deliberately re-exports nothing: a convenience surface here would be a second place for the
API to be defined, and the modules are the API.

**Share mechanism, never policy.** Anything in this package must be true for every consumer.
Risk limits, sizing rules, strategies, universe definitions and instrument specs are policy
and belong to the system that owns them. An `if project == ...` branch anywhere in here means
policy has leaked in.
"""

__all__: list[str] = []
