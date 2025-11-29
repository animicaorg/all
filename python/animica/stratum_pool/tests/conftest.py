"""Test helpers for the stratum pool package.

Ensures the in-repo ``animica`` and ``mining`` packages are importable when the
package is not installed in editable mode. Also provides a minimal asyncio
runner so tests marked with ``@pytest.mark.asyncio`` can execute without
external plugins.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Iterable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
PYTHON_ROOT = REPO_ROOT / "python"


def _prepend_unique(paths: Iterable[Path]) -> None:
    for path in paths:
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)


_prepend_unique((PYTHON_ROOT, REPO_ROOT))


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:  # pragma: no cover - plugin hook
    config.addinivalue_line("markers", "asyncio: mark test as requiring asyncio event loop")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem: pytest.Function) -> bool | None:  # pragma: no cover - plugin hook
    if pyfuncitem.get_closest_marker("asyncio") is None:
        return None

    test_func = pyfuncitem.obj
    if asyncio.iscoroutinefunction(test_func):
        argnames = getattr(pyfuncitem, "_fixtureinfo", None)
        wanted = set(getattr(argnames, "argnames", []) or [])
        kwargs = {k: v for k, v in pyfuncitem.funcargs.items() if k in wanted}
        asyncio.run(test_func(**kwargs))
        return True
    return None
