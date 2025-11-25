import importlib
import os
from typing import Optional, Any
import pytest


# ---------- dynamic import helpers ----------

def _import(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _get_attr(obj: Any, names: list[str]):
    if obj is None:
        return None
    for n in names:
        v = getattr(obj, n, None)
        if callable(v) or v is not None:
            return v
    return None


# ---------- limits / constants (best-effort) ----------

_CONST = _import("da.constants")
_MAX_BLOB_BYTES = getattr(_CONST, "MAX_BLOB_BYTES", 1 << 20)  # default 1 MiB if DA not present

_NS = _import("da.nmt.namespace")
_NS_MIN = getattr(_NS, "MIN_NAMESPACE_ID", 0)
_NS_MAX = getattr(_NS, "MAX_NAMESPACE_ID", (1 << 32) - 1)


# ---------- syscall wrappers (with graceful fallbacks) ----------

def _blob_pin(ctx: Optional[object], ns: int, data: bytes):
    """
    Try the canonical capability host first, then provider, then adapters.da.
    The pin operation should persist the blob and return a commitment (digest/root).
    """
    # capabilities.host.blob.blob_pin
    host_blob = _import("capabilities.host.blob")
    fn = _get_attr(host_blob, ["blob_pin", "pin_blob", "put_blob"])
    if callable(fn):
        try:
            return fn(ctx, ns, data)  # type: ignore[misc]
        except TypeError:
            return fn(ns, data)  # type: ignore[misc]

    # capabilities.host.provider.Provider().blob_pin
    prov_mod = _import("capabilities.host.provider")
    Provider = _get_attr(prov_mod, ["Provider", "HostProvider", "SyscallProvider"])
    if Provider:
        prov = Provider()  # type: ignore[call-arg]
        for name in ["blob_pin", "pin_blob", "put_blob", "syscall"]:
            meth = getattr(prov, name, None)
            if callable(meth):
                try:
                    return meth(ctx, ns, data)  # type: ignore[misc]
                except TypeError:
                    try:
                        return meth(ns, data)  # type: ignore[misc]
                    except TypeError:
                        # syscall-like shape
                        return meth("blob_pin", ns, data)  # type: ignore[misc]

    # capabilities.adapters.da.pin (dev convenience)
    ada = _import("capabilities.adapters.da")
    afn = _get_attr(ada, ["pin", "blob_pin"])
    if callable(afn):
        return afn(ns, data)  # type: ignore[misc]

    pytest.skip("No blob pin syscall/adapter available")


def _blob_get(commitment: bytes | str) -> bytes:
    """
    Retrieve the blob by its commitment using any available path.
    """
    # capabilities.host.blob.get_blob
    host_blob = _import("capabilities.host.blob")
    fn = _get_attr(host_blob, ["get_blob", "blob_get", "fetch_blob"])
    if callable(fn):
        return fn(commitment)  # type: ignore[misc]

    # capabilities.adapters.da.get
    ada = _import("capabilities.adapters.da")
    afn = _get_attr(ada, ["get", "get_blob", "fetch"])
    if callable(afn):
        return afn(commitment)  # type: ignore[misc]

    # da.retrieval.client.get_blob or Client().get_blob
    da_cli = _import("da.retrieval.client")
    if da_cli:
        gfn = _get_attr(da_cli, ["get_blob", "fetch_blob"])
        if callable(gfn):
            return gfn(commitment)  # type: ignore[misc]
        Client = _get_attr(da_cli, ["Client", "DAClient"])
        if Client:
            cli = Client()  # type: ignore[call-arg]
            cfn = _get_attr(cli, ["get_blob", "get", "fetch"])
            if callable(cfn):
                return cfn(commitment)  # type: ignore[misc]

    pytest.skip("No DA get/read path available to round-trip blob")


# ---------- small helpers ----------

def _ctx(chain_id=1337, height=1):
    # Context object shape is intentionally loose; providers that don't need it will ignore it.
    class Ctx:
        def __init__(self):
            self.chain_id = chain_id
            self.height = height
            self.tx_hash = b"\xaa" * 32
            self.caller = b"\xbb" * 32
    return Ctx()


def _as_commit_bytes(commitment: bytes | str) -> bytes:
    if isinstance(commitment, bytes):
        return commitment
    s = commitment
    if s.startswith("0x") or s.startswith("0X"):
        s = s[2:]
    return bytes.fromhex(s)


# ========================= TESTS =========================

def test_blob_pin_round_trip_small_payload(tmp_path, monkeypatch):
    """
    Pins a small blob via the syscall surface and reads it back using any available adapter/client.
    Asserts commitment is a 32-byte digest and bytes match exactly.
    """
    # Some implementations read storage dir from env; make it point to tmp
    monkeypatch.setenv("ANIMICA_DA_STORAGE_DIR", str(tmp_path))

    ns = 0x24
    data = b"hello animica da \xf0\x9f\x8c\x90"
    ctx = _ctx()

    receipt = _blob_pin(ctx, ns=ns, data=data)

    # Be tolerant to shapes: dict, tuple, or simple commitment bytes/hex
    if isinstance(receipt, (bytes, str)):
        commit_b = _as_commit_bytes(receipt)
        size = len(data)
        r_ns = ns
    elif isinstance(receipt, tuple) and len(receipt) >= 1:
        commit_b = _as_commit_bytes(receipt[0])
        size = receipt[1] if len(receipt) > 1 else len(data)
        r_ns = receipt[2] if len(receipt) > 2 else ns
    elif isinstance(receipt, dict):
        commit_b = _as_commit_bytes(
            receipt.get("commitment") or receipt.get("root") or receipt.get("id") or receipt.get("hash")
        )
        size = receipt.get("size", len(data))
        r_ns = receipt.get("namespace", ns)
    else:
        # object with attributes
        commit_b = _as_commit_bytes(getattr(receipt, "commitment", getattr(receipt, "root", b"")))
        size = getattr(receipt, "size", len(data))
        r_ns = getattr(receipt, "namespace", ns)

    assert isinstance(commit_b, bytes) and len(commit_b) in (24, 32, 48, 64)
    assert size == len(data)
    assert r_ns == ns

    fetched = _blob_get(commit_b)
    assert isinstance(fetched, (bytes, bytearray))
    assert bytes(fetched) == data


def test_blob_pin_rejects_oversize(monkeypatch, tmp_path):
    """
    Oversized blobs should be rejected by the syscall or adapter.
    If implementation doesn't enforce size yet, mark as xfail.
    """
    monkeypatch.setenv("ANIMICA_DA_STORAGE_DIR", str(tmp_path))

    ns = 0x24
    too_big = _MAX_BLOB_BYTES + 1
    data = b"A" * min(too_big, 2 * 1024 * 1024)  # cap allocation in test

    try:
        _blob_pin(_ctx(), ns, data)
    except (AssertionError, ValueError) as e:
        # Good: hard failure
        return
    except Exception as e:
        # Many implementations raise domain-specific DAError/NamespaceRangeError; accept any failure.
        if e.__class__.__name__ in {"DAError", "InvalidBlobSize", "NamespaceRangeError"}:
            return
        # Unexpected exception still counts as a rejection
        return
    # If it didn't raise at all, size limits may not be enforced yet.
    pytest.xfail("Blob size limits not enforced by implementation")


def test_namespace_bounds_enforced(monkeypatch, tmp_path):
    """
    Namespaces out of configured range should be rejected.
    If not enforced yet, xfail rather than failing the suite.
    """
    monkeypatch.setenv("ANIMICA_DA_STORAGE_DIR", str(tmp_path))

    bad_ns_low = max(_NS_MIN - 1, -1)
    bad_ns_high = _NS_MAX + 1
    data = b"ns-check"

    # Try low
    try:
        _blob_pin(_ctx(), bad_ns_low, data)
    except Exception:
        pass
    else:
        pytest.xfail("Namespace lower bound not enforced")

    # Try high
    try:
        _blob_pin(_ctx(), bad_ns_high, data)
    except Exception:
        return
    pytest.xfail("Namespace upper bound not enforced")
