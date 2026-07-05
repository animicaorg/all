"""ANM-C05/C06: the raw exec() contract path (vm_py.runtime.loader.run_call) is
fail-closed.

Contract source was exec()'d with full CPython builtins, no AST validation and no
gas metering — an RCE + unbounded-loop DoS on every validating node whenever the
VM was enabled. run_call now refuses by default; execution.runtime.contracts
catches the refusal and deterministically REVERTs, so mainnet stays a no-op even
if ANIMICA_ENABLE_VM_PY is set. Enabling the exec path requires an explicit,
documented-dangerous ANIMICA_VM_ALLOW_UNSAFE_EXEC=1 (local dev only).
"""
import pytest


def test_run_call_exec_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ANIMICA_VM_ALLOW_UNSAFE_EXEC", raising=False)
    from vm_py.runtime.loader import run_call

    # A contract that would execute attacker code if exec() fired.
    manifest = {"inline": "import os\ndef pwn(x):\n    return os.getpid()"}
    with pytest.raises(Exception) as ei:
        run_call(manifest, "pwn", [1])
    msg = str(ei.value).lower()
    assert "disabled" in msg or "forbidden" in msg, msg


def test_guard_fires_before_source_is_touched(monkeypatch):
    # The guard raises before the manifest is even resolved, so no attacker
    # source is ever handed to exec().
    monkeypatch.delenv("ANIMICA_VM_ALLOW_UNSAFE_EXEC", raising=False)
    from vm_py.runtime.loader import run_call

    with pytest.raises(Exception):
        run_call({"inline": "raise RuntimeError('should never exec')"}, "x")
