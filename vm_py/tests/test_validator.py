import textwrap

import pytest

from vm_py import validate as vm_validate
from vm_py.errors import ForbiddenImport, ValidationError


def validate_ok(src: str) -> None:
    """Helper: validate a source string and assert it passes."""
    vm_validate.validate_source(textwrap.dedent(src))


def validate_bad(src: str, exc=ValidationError, match: str | None = None) -> None:
    """Helper: validate a source string and assert it fails with the given error."""
    with pytest.raises(exc) as ei:
        vm_validate.validate_source(textwrap.dedent(src))
    if match is not None:
        assert match in str(ei.value)


# --- Forbidden imports -------------------------------------------------------


@pytest.mark.parametrize(
    "stmt",
    [
        "import os",
        "import sys",
        "import subprocess",
        "import socket",
        "import time",
        "import random",
        "from os import path",
        "from sys import exit",
        "from subprocess import Popen",
        "import builtins",  # should not be accessible directly
        "import pathlib",
        "import threading",
    ],
)
def test_forbidden_imports(stmt: str) -> None:
    validate_bad(stmt, exc=ForbiddenImport)


def test_import_with_alias() -> None:
    validate_bad("import os as o", exc=ForbiddenImport)


# --- Forbidden builtins / side-effecting calls -------------------------------


@pytest.mark.parametrize(
    "call",
    [
        "eval('1+1')",
        "exec('x=1')",
        "open('/etc/hosts', 'r')",
        "input('> ')",
        "__import__('os')",
        "print('hello')",  # side-effecting I/O should be banned in strict mode
    ],
)
def test_forbidden_builtins(call: str) -> None:
    src = f"""
    def main():
        {call}
    """
    validate_bad(src, match=call.split("(")[0])


# --- Disallowed attributes that are proxies to dangerous ops -----------------


def test_builtins_attribute_hack() -> None:
    # Attempt to reach forbidden builtin via attribute access
    src = """
    def main():
        b = __builtins__
        if isinstance(b, dict):
            f = b.get('open')
        else:
            f = getattr(b, 'open', None)
        return f is None
    """
    validate_bad(src, match="__builtins__")


# --- Structural / AST node checks --------------------------------------------


@pytest.mark.parametrize(
    "snippet",
    [
        # Dynamic code exec via compile/exec
        "code = compile('1+1', '<x>', 'eval')\n    eval(code)",
        # Context managers likely disallowed to keep sandbox tiny & deterministic
        "with (1):\n        pass",
        # Try/except may be disallowed to keep IR simple (validator should catch if so)
        "try:\n        x=1\n    except Exception:\n        x=2",
        # Lambdas often disallowed in minimal IR
        "f = lambda x: x+1",
    ],
)
def test_disallowed_ast_nodes(snippet: str) -> None:
    src = f"""
    def main():
        {snippet}
    """
    # We don't assume a specific subclass — validator may raise a generic ValidationError
    validate_bad(src)


# --- Recursion / depth limits (synthetic) ------------------------------------


def test_self_recursion_hint() -> None:
    # Even if recursion is not strictly forbidden, validator may flag direct self recursion.
    src = """
    def f(n: int) -> int:
        return f(n-1)
    """
    validate_bad(src)


# --- Allowed minimal program (should pass) -----------------------------------


def test_minimal_contract_like_code_passes() -> None:
    # A tiny, side-effect-free subset should be accepted.
    src = """
    # simple counter-style logic using only arithmetic, control flow and assignments
    VALUE = 0

    def inc_by(n: int) -> None:
        global VALUE
        VALUE = VALUE + int(n)

    def get() -> int:
        return VALUE

    def main():
        inc_by(1)
        assert get() >= 1
    """
    validate_ok(src)


# --- Idempotence: validating twice should behave the same --------------------


def test_validate_is_deterministic() -> None:
    src = "def ok():\n    return 42\n"
    validate_ok(src)
    validate_ok(src)
