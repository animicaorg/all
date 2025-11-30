from __future__ import annotations

import inspect
from typing import Any, Callable, Dict, Tuple

import pytest

import proofs.cbor as pcbor
from proofs import nullifiers as pnull
from proofs import types as ptypes

# ---------- helpers: locate a generic nullifier function & CBOR codec ----------


def find_encode_decode(mod) -> Tuple[Callable[[Any], bytes], Callable[[bytes], Any]]:
    cands = [
        ("encode", "decode"),
        ("dumps", "loads"),
        ("cbor_encode", "cbor_decode"),
        ("to_bytes", "from_bytes"),
    ]
    for en, de in cands:
        if hasattr(mod, en) and hasattr(mod, de):
            return getattr(mod, en), getattr(mod, de)
    raise AssertionError("proofs.cbor must expose encode/decode (or equivalent)")


ENCODE, DECODE = find_encode_decode(pcbor)


def find_nullifier_fn(mod) -> Callable[[int, Any], bytes]:
    """
    Prefer a generic function taking (type_id:int, body:dict|bytes).
    Fall back to per-kind functions if necessary.
    """
    # Generic candidates
    generic_names = [
        "compute_nullifier",
        "nullifier_for",
        "derive_nullifier",
        "compute",
        "get_nullifier",
        "nullifier",
    ]
    for name in generic_names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return _wrap_generic(fn)

    # Per-kind candidates: hashshare/ai/quantum/storage/vdf
    per_kind = {}
    for n in dir(mod):
        low = n.lower()
        if "nullifier" in low:
            per_kind[low] = getattr(mod, n)

    def call_per_kind(type_id: int, body: Any) -> bytes:
        key_map = {
            int(ptypes.ProofType.HASHSHARE): (
                "hashshare_nullifier",
                "nullifier_hashshare",
            ),
            int(ptypes.ProofType.AI): ("ai_nullifier", "nullifier_ai"),
            int(ptypes.ProofType.QUANTUM): ("quantum_nullifier", "nullifier_quantum"),
            int(ptypes.ProofType.STORAGE): ("storage_nullifier", "nullifier_storage"),
            int(ptypes.ProofType.VDF): ("vdf_nullifier", "nullifier_vdf"),
        }
        for cand in key_map[type_id]:
            for k, fn in per_kind.items():
                if cand in k and callable(fn):
                    return _invoke_nullifier(fn, body)
        raise AssertionError(
            f"No suitable nullifier function found for type_id={type_id}"
        )

    return call_per_kind


def _wrap_generic(fn: Callable[..., bytes]) -> Callable[[int, Any], bytes]:
    def wrapper(type_id: int, body: Any) -> bytes:
        # Try as (type_id, body)
        try:
            return _invoke_nullifier(lambda b: fn(type_id, b), body)
        except TypeError:
            # Some variants might take (body, type_id)
            return _invoke_nullifier(lambda b: fn(b, type_id), body)

    return wrapper


def _invoke_nullifier(fn1arg: Callable[[Any], bytes], body: Any) -> bytes:
    """
    Call the function with a dict; if it expects bytes, encode body via canonical CBOR first.
    """
    try:
        out = fn1arg(body)
        if not isinstance(out, (bytes, bytearray)):
            raise AssertionError("nullifier function must return bytes")
        return bytes(out)
    except TypeError:
        # Maybe expects bytes
        cb = ENCODE(body)
        out = fn1arg(cb)
        if not isinstance(out, (bytes, bytearray)):
            raise AssertionError("nullifier function must return bytes")
        return bytes(out)


NULLIFIER = find_nullifier_fn(pnull)


# ------------------------------- fixtures ------------------------------------


def sample_body(t: ptypes.ProofType) -> Dict[str, Any]:
    if t is ptypes.ProofType.HASHSHARE:
        return {
            "header_hash": b"\x11" * 32,
            "nonce": 123456,
            "mix_seed": b"\x22" * 32,
            "share_target": 10_000_000,
        }
    if t is ptypes.ProofType.AI:
        return {
            "provider_id": "prov.ai.001",
            "output_digest": b"\x33" * 32,
            "tee_quote": b"QUOTE\x00",
            "traps": {"pass": 12, "fail": 0},
            "qos": 0.97,
        }
    if t is ptypes.ProofType.QUANTUM:
        return {
            "provider_id": "prov.qpu.001",
            "circuit_hash": b"\x44" * 32,
            "shots": 256,
            "trap_ratio": 0.93,
        }
    if t is ptypes.ProofType.STORAGE:
        return {
            "provider_id": "prov.store.001",
            "sector": 17,
            "window": 5,
            "proof_digest": b"\x55" * 32,
        }
    if t is ptypes.ProofType.VDF:
        return {
            "input": b"\xaa" * 64,
            "y": b"\xbb" * 64,
            "pi": b"\xcc" * 64,
            "iterations": 2_000_000,
        }
    raise AssertionError("Unknown proof type")


ALL_TYPES = [
    ptypes.ProofType.HASHSHARE,
    ptypes.ProofType.AI,
    ptypes.ProofType.QUANTUM,
    ptypes.ProofType.STORAGE,
    ptypes.ProofType.VDF,
]


# ------------------------------- tests ---------------------------------------


@pytest.mark.parametrize("t", ALL_TYPES)
def test_deterministic_per_type(t: ptypes.ProofType) -> None:
    body = sample_body(t)
    n1 = NULLIFIER(int(t), body)
    n2 = NULLIFIER(int(t), body)
    assert isinstance(n1, (bytes, bytearray)) and isinstance(n2, (bytes, bytearray))
    n1, n2 = bytes(n1), bytes(n2)
    assert n1 == n2, "nullifier must be deterministic for identical inputs"
    # Reasonable length (most implementations use 32-byte SHA3-256)
    assert len(n1) in (16, 24, 32, 48, 64)
    # Re-encoding the same logical body (different insertion order) must not change nullifier
    if isinstance(body, dict):
        shuffled = dict(reversed(list(body.items())))
        assert NULLIFIER(int(t), shuffled) == n1


def test_domain_separation_across_types() -> None:
    # Use similarly-shaped fields where possible; nullifiers MUST differ across types.
    pairs = []
    for t in ALL_TYPES:
        pairs.append((t, sample_body(t)))
    vals = [NULLIFIER(int(t), b) for (t, b) in pairs]
    # Ensure pairwise uniqueness
    assert len(set(vals)) == len(
        vals
    ), "domain separation: different proof types must not collide"


@pytest.mark.parametrize(
    "t,mut",
    [
        (ptypes.ProofType.HASHSHARE, lambda b: b.__setitem__("nonce", b["nonce"] + 1)),
        (
            ptypes.ProofType.AI,
            lambda b: b.__setitem__(
                "output_digest",
                (int.from_bytes(b["output_digest"], "big") ^ 1).to_bytes(
                    len(b["output_digest"]), "big"
                ),
            ),
        ),
        (ptypes.ProofType.QUANTUM, lambda b: b.__setitem__("shots", b["shots"] + 1)),
        (ptypes.ProofType.STORAGE, lambda b: b.__setitem__("sector", b["sector"] + 1)),
        (
            ptypes.ProofType.VDF,
            lambda b: b.__setitem__("iterations", b["iterations"] + 1),
        ),
    ],
)
def test_field_sensitivity(
    t: ptypes.ProofType, mut: Callable[[Dict[str, Any]], None]
) -> None:
    body = sample_body(t)
    n_before = NULLIFIER(int(t), body)
    # mutate a single salient field
    mut(body)
    n_after = NULLIFIER(int(t), body)
    assert n_before != n_after, "changing salient field must change nullifier"


def test_bytes_input_equivalence_if_supported() -> None:
    """
    If the nullifier function accepts bytes (CBOR) as body, then passing dict vs CBOR must be equivalent.
    """
    t = ptypes.ProofType.HASHSHARE
    body = sample_body(t)
    n_dict = NULLIFIER(int(t), body)
    # Try CBOR path explicitly
    cb = ENCODE(body)
    try:
        n_cbor = NULLIFIER(int(t), cb)  # type: ignore[arg-type]
    except Exception:
        pytest.skip("nullifier implementation does not accept CBOR bytes directly")
    else:
        assert n_dict == n_cbor, "dict vs CBOR bytes must yield identical nullifier"


def test_nullifier_is_pure_function_no_hidden_state() -> None:
    """
    Call many times in a row; ensure no hidden counters or RNG affect output.
    """
    t = ptypes.ProofType.QUANTUM
    body = sample_body(t)
    ref = NULLIFIER(int(t), body)
    for _ in range(50):
        assert NULLIFIER(int(t), body) == ref
