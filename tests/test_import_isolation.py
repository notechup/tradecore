"""The dependency direction is one-way, and this is what enforces it.

`tradecore` is imported *by* the trading systems; it must never import *from* one. The failure
this catches is not a subtle one — it is someone reaching back into a consumer for a constant
or a helper because it was there — but it is the failure that would otherwise be discovered by
a second consumer's install breaking, days later, for reasons that look like packaging.

It cannot detect a **policy** leak. Whether a value is mechanism or policy is a judgement call
and no import graph shows it; that is what code review and the `if project == ...` smell test
are for (docs in the consuming repos). What this file guarantees is narrower and mechanical:
nothing here reaches sideways, and nothing here needs more than pydantic to run.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "tradecore"

#: The systems that consume this package. An import of any of these is the leak.
CONSUMERS = frozenset({"aurum", "bellator"})

#: The only third-party name allowed at runtime. Keeping this list at one entry is the point:
#: a dependency added here is added to every consumer of the package.
RUNTIME_DEPENDENCIES = frozenset({"pydantic"})

MODULES = sorted(p for p in SRC.rglob("*.py"))


def _top_level_imports(path: pathlib.Path) -> set[str]:
    """Every top-level package name this file imports, from a static parse.

    Static, not runtime: an import guarded behind a function body or a `TYPE_CHECKING` block
    would not show up in `sys.modules` but is still a dependency of the source.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import — within tradecore by definition
                continue
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def test_there_are_modules_to_check() -> None:
    """Guard against the version of this file that passes because it found nothing."""
    assert len(MODULES) >= 3  # __init__, contracts, bars


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_module_imports_a_consumer(path: pathlib.Path) -> None:
    leaked = _top_level_imports(path) & CONSUMERS
    assert not leaked, (
        f"{path.name} imports {sorted(leaked)}. tradecore is imported BY the trading systems "
        f"and must never import from one; move whatever this needed back into the consumer."
    )


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_no_module_needs_more_than_pydantic(path: pathlib.Path) -> None:
    """Every import is stdlib, pydantic, or tradecore itself."""
    allowed = sys.stdlib_module_names | RUNTIME_DEPENDENCIES | {"tradecore"}
    extra = _top_level_imports(path) - allowed
    assert not extra, (
        f"{path.name} imports {sorted(extra)}, which is neither stdlib nor pydantic. "
        f"A runtime dependency added here is added to every consumer of this package."
    )


def test_importing_the_package_pulls_in_no_consumer() -> None:
    """The static check above misses a dependency reached at import time via a third module."""
    import tradecore
    import tradecore.bars
    import tradecore.contracts
    import tradecore.costs
    import tradecore.metrics  # noqa: F401

    loaded = {name.split(".")[0] for name in sys.modules}
    assert not (loaded & CONSUMERS), (
        f"Importing tradecore pulled in {sorted(loaded & CONSUMERS)}. "
        f"Something in the import chain reaches back into a consumer."
    )
