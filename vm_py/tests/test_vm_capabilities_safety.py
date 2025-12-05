from __future__ import annotations

import json
import os
import random
import socket
import time
from pathlib import Path

import pytest

from stdlib import events as std_events
from stdlib import syscalls
from vm_py.examples.blob_pinner import contract as blob_pinner
from vm_py.runtime import syscalls_api
from vm_py.tests.test_forbidden_imports import _assert_forbidden

HERE = Path(__file__).resolve()
VM_PY_ROOT = HERE.parents[1]
BLOB_PINNER_MANIFEST = VM_PY_ROOT / "examples" / "blob_pinner" / "manifest.json"


# --- Safety: contracts must not reach non-deterministic sources ---------------


@pytest.mark.parametrize(
    "snippet",
    [
        "import time\n_ = time.time()",
        "import random\n_ = random.random()",
        "import socket\nsocket.socket()",
        "from urllib import request\nrequest.urlopen('http://example.com')",
    ],
)
def test_capability_contract_cannot_import_nondeterminism(snippet: str) -> None:
    src = (
        "from stdlib import syscalls\n"
        f"{snippet}\n"
        "def run():\n"
        "    return syscalls.blob_pin(1, b'demo')\n"
    )
    _assert_forbidden(src)


# --- Capability modules should remain deterministic ---------------------------


def test_syscall_stubs_do_not_touch_host_nondeterminism(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_: object, **__: object) -> None:  # pragma: no cover - raised if touched
        raise AssertionError("host nondeterminism should not be reachable")

    monkeypatch.setattr(time, "time", _boom)
    monkeypatch.setattr(random, "random", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(os, "urandom", _boom, raising=False)

    blob_result = syscalls.blob_pin(7, b"capability demo")
    assert isinstance(blob_result, dict)
    commitment = blob_result["commitment"]
    assert commitment == syscalls.blob_pin(7, b"capability demo")["commitment"]

    ai_task = syscalls.ai_enqueue(b"demo/model", b"hello")
    quantum_task = syscalls.quantum_enqueue(b"demo circuit", shots=4)
    assert ai_task == syscalls.ai_enqueue(b"demo/model", b"hello")
    assert quantum_task == syscalls.quantum_enqueue(b"demo circuit", shots=4)


# --- Integration: BlobPinner example -----------------------------------------


def _load_manifest() -> dict:
    assert (
        BLOB_PINNER_MANIFEST.is_file()
    ), f"missing BlobPinner manifest at {BLOB_PINNER_MANIFEST}"
    with BLOB_PINNER_MANIFEST.open("r", encoding="utf-8") as f:
        return json.load(f)


def test_blob_pinner_manifest_declares_blob_capability() -> None:
    manifest = _load_manifest()
    resources = manifest.get("resources", {}) or {}
    caps = set(resources.get("caps") or [])
    assert "blob.pin" in caps

    limits = resources.get("limits", {}) or {}
    assert limits.get("max_blob_bytes", 0) >= len(b"demo payload")


def test_blob_pinner_end_to_end_uses_da_capability() -> None:
    std_events.clear_events()
    provider = syscalls_api.get_provider()

    payload = b"hello capability demo"
    try:
        commitment = blob_pinner.pin(payload)
        assert isinstance(commitment, (bytes, bytearray, memoryview))

        expected = syscalls_api.blob_pin(blob_pinner.DEFAULT_NAMESPACE, payload)[
            "commitment"
        ]
        assert commitment == expected

        events = std_events.get_events()
        assert events, "capability call should emit an event"
        ev = events[0]
        assert ev.name == b"Pinner.BlobPinned"
        assert ev.args["ns"] == blob_pinner.DEFAULT_NAMESPACE
        assert ev.args["size"] == len(payload)
    finally:
        syscalls_api.set_provider(provider)


def test_blob_pinner_explicit_namespace_path() -> None:
    std_events.clear_events()
    provider = syscalls_api.get_provider()

    payload = b"hello override"
    namespace = 42
    try:
        commitment = blob_pinner.pin_with_namespace(namespace, payload)
        expected = syscalls_api.blob_pin(namespace, payload)["commitment"]
        assert commitment == expected

        ev = std_events.get_events()[0]
        assert ev.args["ns"] == namespace
        assert ev.args["size"] == len(payload)
    finally:
        syscalls_api.set_provider(provider)
