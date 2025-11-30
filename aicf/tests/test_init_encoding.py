from pathlib import Path

import pytest


def test_package_init_is_ascii():
    """Ensure package init file stays ASCII-compatible for Python 2 loaders."""

    package_init = Path(__file__).resolve().parent.parent / "__init__.py"
    data = package_init.read_bytes()

    try:
        data.decode("ascii")
    except UnicodeDecodeError as exc:  # pragma: no cover - fail with context
        pytest.fail(f"__init__.py contains non-ASCII characters: {exc}")
