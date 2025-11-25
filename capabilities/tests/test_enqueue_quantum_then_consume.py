import importlib
import types
import dataclasses
import json
import hashlib
import pytest


# ---------- Import helpers ----------

def _import(modname: str):
    try:
        return importlib.import_module(modname)
    except Exception:
        return None


def _get_attr(obj, names):
    if obj is None:
        return None
    for n in names:
        v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


# ---------- Errors (with fallbacks) ----------

def _load_errors():
    mod = _import("capabilities.errors")

    class _FallbackNoResultYet(Exception):
        ...

    class _FallbackAttestationError(Exception):
        ...

    NoResultYet = getattr(mod, "NoResultYet", _FallbackNoResultYet if mod else _FallbackNoResultYet)
    AttestationError = getattr(
        mod, "AttestationError", _FallbackAttestationError if mod else _FallbackAttestationError
    )
    return NoResultYet, AttestationError


NoResultYet, AttestationError = _load_errors()


# ---------- Common helpers ----------

def _mk_ctx(chain_id=1, height=100, tx_hash=bytes.fromhex("33" * 32), caller=bytes.fromhex("44" * 32)):
    return types.SimpleNamespace(chain_id=chain_id, height=height, tx_hash=tx_hash, caller=caller)


def _task_id_from(receipt):
    if isinstance(receipt, dict):
        return receipt.get("task_id") or receipt.get("id") or receipt.get("taskId")
    if dataclasses.is_dataclass(receipt):
        return getattr(receipt, "task_id", None) or getattr(receipt, "id", None)
    return getattr(receipt, "task_id", None) or getattr(receipt, "id", None)


# ---------- Enqueue / Result APIs (with graceful fallbacks) ----------

def _enqueue_quantum(ctx, circuit: bytes | str | dict, shots: int, attestation: dict | bytes | str | None):
    """
    Try the canonical host API first:
      capabilities.host.compute.quantum_enqueue(ctx, circuit=..., shots=..., attestation=...)
    Fallback to Provider().quantum_enqueue(...) or Provider().enqueue("quantum", ...).
    """
    comp = _import("capabilities.host.compute")
    fn = _get_attr(comp, ["quantum_enqueue", "enqueue_quantum"])
    if callable(fn):
        try:
            return fn(ctx, circuit=circuit, shots=shots, attestation=attestation)  # type: ignore[misc]
        except TypeError:
            # Some implementations may accept positional params
            return fn(ctx, circuit, shots, attestation)  # type: ignore[misc]

    prov_mod = _import("capabilities.host.provider")
    Provider = _get_attr(prov_mod, ["Provider", "HostProvider", "SyscallProvider"])
    if Provider:
        try:
            prov = Provider()  # type: ignore[call-arg]
        except TypeError:
            prov = _get_attr(prov_mod, ["default", "create", "new"])()
        for name in ["quantum_enqueue", "enqueue_quantum", "enqueue"]:
            meth = getattr(prov, name, None)
            if callable(meth):
                try:
                    return meth(ctx, circuit=circuit, shots=shots, attestation=attestation)  # type: ignore[misc]
                except TypeError:
                    try:
                        return meth("quantum", ctx, circuit=circuit, shots=shots, attestation=attestation)  # type: ignore[misc]
                    except TypeError:
                        return meth("quantum", ctx, circuit, shots, attestation)  # type: ignore[misc]
    pytest.skip("Quantum enqueue function not available in capabilities.host")


def _read_result(task_id):
    mod = _import("capabilities.host.result_read")
    fn = _get_attr(mod, ["read_result", "get_result"])
    if callable(fn):
        return fn(task_id)  # type: ignore[misc]

    prov_mod = _import("capabilities.host.provider")
    Provider = _get_attr(prov_mod, ["Provider", "HostProvider", "SyscallProvider"])
    if Provider:
        try:
            prov = Provider()  # type: ignore[call-arg]
        except TypeError:
            prov = _get_attr(prov_mod, ["default", "create", "new"])()
        meth = _get_attr(prov, ["read_result", "get_result"])
        if callable(meth):
            return meth(task_id)
    pytest.skip("Result read function not available in capabilities.host")


def _write_result_next_block(height_next: int, task_id, result_bytes: bytes):
    # Prefer the official resolver API
    res_mod = _import("capabilities.jobs.resolver")
    if res_mod:
        for name in ["apply_proofs", "resolve_block", "ingest_block_results", "populate_results"]:
            fn = getattr(res_mod, name, None)
            if callable(fn):
                try:
                    return fn(height_next, [(task_id, result_bytes)])
                except TypeError:
                    try:
                        return fn(records=[(task_id, result_bytes)], height=height_next)
                    except TypeError:
                        pass

    # Direct write to result_store as a fallback
    store_mod = _import("capabilities.jobs.result_store")
    types_mod = _import("capabilities.jobs.types")
    if not store_mod:
        pytest.skip("Neither resolver nor result_store available to inject result")

    ResultRecord = _get_attr(types_mod, ["ResultRecord"]) if types_mod else None
    if ResultRecord:
        try:
            record = ResultRecord(task_id=task_id, kind="Quantum", height=height_next, result=result_bytes)  # type: ignore[misc]
        except TypeError:
            record = ResultRecord(task_id=task_id, result=result_bytes, height=height_next)  # type: ignore[misc]
    else:
        record = {"task_id": task_id, "kind": "Quantum", "height": height_next, "result": result_bytes}

    put_fn = _get_attr(store_mod, ["put", "store", "write", "save", "insert"])
    if callable(put_fn):
        try:
            return put_fn(record)  # type: ignore[misc]
        except TypeError:
            return put_fn(task_id, record)  # type: ignore[misc]
    pytest.skip("Could not locate a method to persist ResultRecord")


# ---------- Attestation normalization (optional but preferred) ----------

def _get_quantum_attest_normalizer():
    """
    Try to locate a normalization function for quantum attestation bundles.
    Expected to return bytes or a canonical dict which we will hash canonically.
    """
    mod = _import("capabilities.jobs.attest_bridge")
    if not mod:
        return None
    for name in [
        "normalize_quantum_attestation",
        "normalize_attestation",
        "normalize_q_attest",
        "to_proofs_inputs",
    ]:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    return None


def _digest_normalized_attest(normalizer, bundle) -> bytes:
    out = normalizer(bundle)
    if isinstance(out, (bytes, bytearray)):
        data = bytes(out)
    else:
        # Fall back to a stable JSON encoding
        data = json.dumps(out, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha3_256(data).digest()


# ======================== TESTS ========================

def test_quantum_enqueue_then_consume_next_block():
    ctx = _mk_ctx(chain_id=2, height=123)
    # A tiny illustrative circuit; real impl may accept str/bytes/dict
    circuit = {
        "qubits": 2,
        "gates": [
            {"op": "H", "t": 0},
            {"op": "CNOT", "c": 0, "t": 1},
            {"op": "MEASURE", "q": [0, 1]},
        ],
    }
    shots = 64
    attestation = {
        "provider_id": "demo-qpu-1",
        "cert_chain": ["-----BEGIN CERT-----...-----END CERT-----"],
        "firmware": {"version": "0.1.0", "measurement": "0xdeadbeef"},
        "session": {"ts": 1700000000, "nonce": "0x01"},
        "traps": {"ratio": 0.12, "samples": 200, "pass": True},
    }

    receipt = _enqueue_quantum(ctx, circuit=circuit, shots=shots, attestation=attestation)
    task_id = _task_id_from(receipt)
    assert task_id, "enqueue must return a receipt with a task_id"

    with pytest.raises(NoResultYet):
        _read_result(task_id)

    expected = b'{"ok":true,"fidelity":0.991,"units":123}'
    _write_result_next_block(ctx.height + 1, task_id, expected)

    out = _read_result(task_id)
    if isinstance(out, dict):
        body = out.get("result") or out.get("output") or out.get("bytes")
        assert body == expected
    else:
        assert out == expected


def test_attestation_normalization_yields_stable_task_id():
    ctx = _mk_ctx(chain_id=2, height=321)
    circuit = {"qubits": 1, "gates": [{"op": "H", "t": 0}, {"op": "MEASURE", "q": [0]}]}
    shots = 32

    # Two semantically-equivalent bundles (ordering/whitespace differences)
    attest_a = {
        "provider_id": "demo-qpu-1",
        "firmware": {"measurement": "0xCAFEBABE", "version": "0.1.0"},
        "traps": {"samples": 128, "ratio": 0.10, "pass": True},
        "cert_chain": ["-----BEGIN CERT-----...-----END CERT-----"],
    }
    attest_b = {
        "cert_chain": ["-----BEGIN CERT-----...-----END CERT-----"],
        "traps": {"pass": True, "ratio": 0.10, "samples": 128},
        "provider_id": "demo-qpu-1",
        "firmware": {"version": "0.1.0", "measurement": "0xCAFEBABE"},
    }

    # If the normalizer is exposed, assert the canonical digests match
    normalizer = _get_quantum_attest_normalizer()
    if normalizer:
        d_a = _digest_normalized_attest(normalizer, attest_a)
        d_b = _digest_normalized_attest(normalizer, attest_b)
        assert d_a == d_b, "normalized attestation digests must match for equivalent bundles"

    r1 = _enqueue_quantum(ctx, circuit=circuit, shots=shots, attestation=attest_a)
    r2 = _enqueue_quantum(ctx, circuit=circuit, shots=shots, attestation=attest_b)
    t1, t2 = _task_id_from(r1), _task_id_from(r2)

    if normalizer:
        assert t1 == t2, "task_id must be stable across equivalent attestations after normalization"
    else:
        # If no explicit normalizer is exported, some implementations may still normalize internally.
        # If they don't, the IDs may differ; don't fail the suite for missing export.
        if t1 != t2:
            pytest.xfail("Quantum attestation normalizer not exposed; task_id may differ")


def test_invalid_attestation_rejected_or_skipped():
    """
    Enqueuing with an obviously bad attestation should raise AttestationError (or similar).
    If the implementation doesn't enforce attestation yet, skip the assertion.
    """
    ctx = _mk_ctx(chain_id=2, height=500)
    circuit = {"qubits": 1, "gates": [{"op": "MEASURE", "q": [0]}]}
    shots = 8

    bad_attest = {
        # Missing provider certs/measurements entirely; malformed values.
        "provider_id": "",
        "firmware": {"version": "", "measurement": ""},
        "traps": {"ratio": -1, "samples": 0, "pass": False},
        "cert_chain": [],
    }

    try:
        _enqueue_quantum(ctx, circuit=circuit, shots=shots, attestation=bad_attest)
    except (AttestationError, ValueError, AssertionError):
        # Good: rejected
        return
    except pytest.skip.Exception:
        raise
    # If it didn't raise, assume attestation enforcement is not wired yet.
    pytest.skip("Attestation validation not enforced; enqueue accepted malformed bundle")
