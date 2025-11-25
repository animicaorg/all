from __future__ import annotations

import types
import pytest

validate = pytest.importorskip("mempool.validate")
errors = pytest.importorskip("mempool.errors")

# Prefer specific error types if they exist; fall back to AdmissionError.
ERR_ADMISSION = getattr(errors, "AdmissionError", Exception)
ERR_OVERSIZE = getattr(errors, "Oversize", ERR_ADMISSION)


class FakeTx:
    """
    Minimal transaction stand-in for stateless checks.
    Adjusts to whatever fields your validator looks at.
    """

    def __init__(
        self,
        *,
        chain_id: int = 1,
        gas_limit: int = 21_000,
        size_bytes: int = 200,
        sign_domain: bytes = b"animica|tx|chain:1",
        alg_id: int = 0xD3,  # e.g., Dilithium3 (placeholder)
        pubkey: bytes = b"\x11" * 32,
        signature: bytes = b"\x22" * 64,
    ):
        self.chain_id = chain_id
        self.gas_limit = gas_limit
        self._bytes = b"\xAA" * size_bytes
        self.sign_domain = sign_domain
        self.alg_id = alg_id
        self.pubkey = pubkey
        self.signature = signature

    # If your code introspects for an encoder/bytes:
    def to_cbor(self) -> bytes:
        return self._bytes

    def __bytes__(self) -> bytes:  # convenient fallback
        return self._bytes


def _call_validator(tx: FakeTx, chain_id: int, limits: object, monkeypatch: pytest.MonkeyPatch):
    """
    Try a few common entrypoints/signatures, so tests remain stable
    while the implementation stabilizes.
    """
    # Help the validator compute size if it uses a helper
    for name in ("estimate_encoded_size", "encoded_size", "tx_encoded_size", "get_encoded_size"):
        if hasattr(validate, name):
            monkeypatch.setattr(validate, name, lambda _tx: len(bytes(_tx)), raising=True)

    # Pick an entrypoint
    for fname in ("validate_tx", "fast_stateless_check", "stateless_validate", "validate"):
        if hasattr(validate, fname):
            fn = getattr(validate, fname)
            break
    else:
        pytest.skip("No known stateless validator entrypoint found in mempool.validate")

    # Try calling with (tx, chain_id, limits) or variants
    tried = []
    for args in (
        (tx, chain_id, limits),
        (tx, chain_id),
        (tx, limits),
        (tx,),
    ):
        try:
            return fn(*args)  # type: ignore[misc]
        except TypeError as e:
            tried.append((args, e))
            continue
    # If we got here, signature didn't match any attempt
    raise RuntimeError(f"Could not call validator {fn.__name__}; tried arg shapes: {tried}")


def _make_limits(max_tx_bytes: int = 1024, max_gas: int = 10_000_000):
    """
    Build a limits object compatible with your implementation.
    - If mempool.config exposes MempoolLimits, use it.
    - Else return a simple types.SimpleNamespace with matching fields.
    """
    try:
        from mempool import config as mp_config  # type: ignore
    except Exception:
        mp_config = None

    # Try dataclass/typed config first
    for type_name in ("MempoolLimits", "Limits", "Config", "MempoolConfig"):
        if mp_config and hasattr(mp_config, type_name):
            LimitsT = getattr(mp_config, type_name)
            try:
                return LimitsT(max_tx_bytes=max_tx_bytes, max_gas=max_gas)  # type: ignore[call-arg]
            except Exception:
                # Fall through to SimpleNamespace
                break

    return types.SimpleNamespace(max_tx_bytes=max_tx_bytes, max_gas=max_gas)


def test_size_limit_rejects_big_tx(monkeypatch: pytest.MonkeyPatch):
    limits = _make_limits(max_tx_bytes=256)
    tx = FakeTx(size_bytes=512)  # too large

    # If a direct check exists, exercise it; else call the entrypoint.
    if hasattr(validate, "check_size"):
        with pytest.raises((ERR_OVERSIZE, ERR_ADMISSION)):
            validate.check_size(tx_bytes=bytes(tx), max_tx_bytes=limits.max_tx_bytes)  # type: ignore[attr-defined]
    else:
        with pytest.raises((ERR_OVERSIZE, ERR_ADMISSION)):
            _call_validator(tx, chain_id=1, limits=limits, monkeypatch=monkeypatch)


def test_chain_id_mismatch_raises(monkeypatch: pytest.MonkeyPatch):
    limits = _make_limits()
    tx = FakeTx(chain_id=2)  # expected 1

    if hasattr(validate, "check_chain_id"):
        with pytest.raises(ERR_ADMISSION):
            validate.check_chain_id(tx_chain_id=tx.chain_id, expected_chain_id=1)  # type: ignore[attr-defined]
    else:
        with pytest.raises(ERR_ADMISSION):
            _call_validator(tx, chain_id=1, limits=limits, monkeypatch=monkeypatch)


def test_gas_limit_exceeds_reject(monkeypatch: pytest.MonkeyPatch):
    limits = _make_limits(max_gas=50_000)
    tx = FakeTx(gas_limit=5_000_000)  # too high

    if hasattr(validate, "check_gas_limits"):
        with pytest.raises(ERR_ADMISSION):
            validate.check_gas_limits(gas_limit=tx.gas_limit, max_gas=limits.max_gas)  # type: ignore[attr-defined]
    else:
        with pytest.raises(ERR_ADMISSION):
            _call_validator(tx, chain_id=1, limits=limits, monkeypatch=monkeypatch)


def test_pq_sig_precheck_failure_rejects(monkeypatch: pytest.MonkeyPatch):
    """
    Force the PQ fast-path precheck to fail and assert the validator rejects.
    We patch whichever precheck hook your module exposes.
    """
    limits = _make_limits()
    tx = FakeTx()

    # Find a precheck hook to patch
    precheck_name = None
    for name in (
        "precheck_pq_signature",
        "pq_precheck_verify",
        "verify_pq_signature",
        "pq_verify",
    ):
        if hasattr(validate, name):
            precheck_name = name
            break

    if precheck_name is None:
        pytest.skip("No PQ precheck hook found in mempool.validate")

    # Patch to always return False (i.e., signature invalid)
    monkeypatch.setattr(validate, precheck_name, lambda *a, **k: False, raising=True)

    with pytest.raises(ERR_ADMISSION):
        _call_validator(tx, chain_id=1, limits=limits, monkeypatch=monkeypatch)


def test_pq_sig_precheck_passes_does_not_raise(monkeypatch: pytest.MonkeyPatch):
    """
    When the fast-path PQ precheck passes, the stateless validator should not
    reject solely on signature grounds. Other checks may still raise if
    constraints are violated.
    """
    limits = _make_limits()
    tx = FakeTx()

    precheck_name = None
    for name in (
        "precheck_pq_signature",
        "pq_precheck_verify",
        "verify_pq_signature",
        "pq_verify",
    ):
        if hasattr(validate, name):
            precheck_name = name
            break

    if precheck_name is None:
        pytest.skip("No PQ precheck hook found in mempool.validate")

    # Patch to always return True (i.e., signature looks OK)
    monkeypatch.setattr(validate, precheck_name, lambda *a, **k: True, raising=True)

    # Should not raise due to PQ precheck; if it raises, it must be for another reason
    try:
        _call_validator(tx, chain_id=1, limits=limits, monkeypatch=monkeypatch)
    except ERR_ADMISSION as e:  # pragma: no cover - allow unrelated failures to surface
        # Re-raise with context so failures are informative
        raise AssertionError(f"Validator rejected despite PQ precheck passing: {e}") from e
