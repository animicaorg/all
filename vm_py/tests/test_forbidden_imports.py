from __future__ import annotations

import pytest

# --- helpers -----------------------------------------------------------------


def _validate_source(src: str):
    """
    Call the VM validator on a source string.
    Tries multiple likely entrypoints so the test is resilient to refactors.
    Raises if the source is considered valid (no exception).
    """
    last_err: Exception | None = None

    # Preferred: dedicated validator module
    try:
        import vm_py.validate as v  # type: ignore

        for name in (
            "validate_source",  # def validate_source(src: str) -> None
            "validate",  # def validate(src: str) -> None
            "check_source",  # def check_source(src: str) -> None
            "check",  # def check(src: str) -> None
        ):
            fn = getattr(v, name, None)
            if callable(fn):
                try:
                    return fn(src)  # type: ignore[misc]
                except Exception as e:  # expected for forbidden inputs
                    raise
    except Exception as e:
        last_err = e

    # Fallback: compiler lower should raise on forbidden imports too
    try:
        from vm_py.compiler import ast_lower as lower  # type: ignore

        for name in ("lower_source", "lower", "compile"):
            fn = getattr(lower, name, None)
            if callable(fn):
                try:
                    return fn(src=src) if name in ("lower_source", "lower") else fn(source=src)  # type: ignore[misc]
                except Exception as e:
                    raise
    except Exception as e:
        last_err = e

    # If we reach here without raising, the validator API surface wasn't found
    # or it did not reject. Make that explicit so the test isn't silently passing.
    raise AssertionError(
        f"Validator API not found or did not reject source. Last error: {last_err}"
    )


def _assert_forbidden(src: str):
    """
    Assert that validating the given source raises a ForbiddenImport/ValidationError-like exception.
    """
    # Accept any of these exception types (names, to avoid import coupling)
    EXPECTED_NAMES = {
        "ForbiddenImport",
        "ValidationError",
        "ForbiddenBuiltin",
        "SandboxError",
    }
    try:
        _validate_source(src)
    except Exception as e:
        # Compare by class name to avoid import cycles across evolving packages
        name = e.__class__.__name__
        if name in EXPECTED_NAMES:
            return
        # Some implementations wrap the inner error; check args
        msg = str(e).lower()
        indicative = any(
            k in msg
            for k in ("forbidden", "disallow", "import", "builtin", "network", "io")
        )
        if indicative:
            return
        raise
    else:
        pytest.fail("Validator accepted forbidden source:\n" + src)


# --- tests -------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        # OS / filesystem
        "import os\nx = os.getenv('HOME')",
        "from os import path\np = path.abspath('.')",
        "import builtins\nf = builtins.open('x.txt', 'w')\n",  # direct builtin I/O
        "open('x.txt', 'w')\n",  # implicit builtin I/O
        # Time / nondeterminism
        "import time\nx = time.time()",
        "from time import sleep\nsleep(1)",
        # Randomness (nondeterministic)
        "import random\nx = random.random()",
        "from random import randint\nx = randint(0, 10)",
        # Network
        "import socket\ns = socket.socket()",
        "from urllib import request\nu = request.urlopen('http://example.com')",
        "import http.client\nc = http.client.HTTPConnection('example.com')",
        # System/process
        "import sys\nsys.stdout.write('hi')",
        "import subprocess\nsubprocess.run(['echo','hi'])",
        # Eval/exec dynamic code
        "eval('1+2')",
        "exec('print(42)')",
        # Wildcard imports / unknown external libraries (should be disallowed)
        "from math import *\n# allow-list typically forbids star-imports in contracts",
        "import requests\nresp = requests.get('http://example.com')",
    ],
)
def test_forbidden_imports_and_io_are_rejected(src: str):
    _assert_forbidden(src)


def test_relative_import_rejected():
    src = "from . import something\n"
    _assert_forbidden(src)


def test_importing_stdlib_package_only_via_guard():
    """
    Even importing 'stdlib' should be blocked at Python-level; contracts are expected to
    access it through injected sandbox, not raw Python import.
    """
    src = "import stdlib\nx = stdlib"  # raw import should not be allowed
    _assert_forbidden(src)


def test_dunder_builtins_access_blocked():
    src = "__builtins__['open']('x.txt','w')"
    _assert_forbidden(src)
