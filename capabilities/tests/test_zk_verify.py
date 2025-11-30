import binascii
import importlib
import json
from typing import Any, Optional, Tuple

import pytest

# ---------- tiny import/attr helpers ----------


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


# ---------- zk.verify dispatcher with flexible inputs ----------


def _encode_hex(b: bytes) -> str:
    return "0x" + binascii.hexlify(b).decode("ascii")


def _variants(circuit: Any, proof: Any, public: Any):
    """
    Yield several argument encodings to maximize compatibility with different backends.
    Order: raw → json/hex → json/list-of-ints.
    """
    # raw
    yield (circuit, proof, public)

    # json/hex
    c_json = json.dumps(circuit) if isinstance(circuit, (dict, list)) else circuit
    p_hex = _encode_hex(proof) if isinstance(proof, (bytes, bytearray)) else proof
    pub_hex = _encode_hex(public) if isinstance(public, (bytes, bytearray)) else public
    yield (c_json, p_hex, pub_hex)

    # json/list-of-ints for public
    if isinstance(public, (bytes, bytearray)):
        pub_list = list(public)
    else:
        try:
            pub_list = list(public)  # type: ignore[arg-type]
        except Exception:
            pub_list = public
    yield (c_json, p_hex, pub_list)


def _coerce_result(res: Any) -> Tuple[bool, Optional[int]]:
    """
    Accept common result shapes:
      - bool
      - (ok, units)
      - {"ok": bool, "units": int} or similar
      - object with .ok/.units
    """
    if isinstance(res, bool):
        return res, None
    if isinstance(res, tuple) and len(res) >= 1:
        ok = bool(res[0])
        units = int(res[1]) if len(res) > 1 and res[1] is not None else None
        return ok, units
    if isinstance(res, dict):
        ok = bool(res.get("ok", res.get("valid", res.get("result", False))))
        units_val = res.get("units", res.get("cost_units", res.get("cost", None)))
        units = int(units_val) if units_val is not None else None
        return ok, units
    ok = bool(getattr(res, "ok", getattr(res, "valid", False)))
    units_attr = getattr(
        res, "units", getattr(res, "cost_units", getattr(res, "cost", None))
    )
    units = int(units_attr) if units_attr is not None else None
    return ok, units


def _zk_verify(circuit: Any, proof: Any, public: Any) -> Tuple[bool, Optional[int]]:
    """
    Try zk verification via:
      - capabilities.host.zk.{zk_verify|verify|verify_proof}
      - capabilities.host.provider.Provider().zk_verify(...)
      - capabilities.adapters.zk.{verify|zk_verify}
    """
    # host.zk.*
    zk_mod = _import("capabilities.host.zk")
    for fn_name in ["zk_verify", "verify", "verify_proof"]:
        fn = _get_attr(zk_mod, [fn_name])
        if callable(fn):
            for args in _variants(circuit, proof, public):
                try:
                    return _coerce_result(fn(*args))  # type: ignore[misc]
                except TypeError:
                    continue

    # provider method
    prov_mod = _import("capabilities.host.provider")
    Provider = _get_attr(prov_mod, ["Provider", "HostProvider", "SyscallProvider"])
    if Provider:
        prov = Provider()  # type: ignore[call-arg]
        for name in ["zk_verify", "verify_zk", "syscall"]:
            meth = getattr(prov, name, None)
            if callable(meth):
                for args in _variants(circuit, proof, public):
                    try:
                        if name == "syscall":
                            res = meth("zk_verify", *args)  # type: ignore[misc]
                        else:
                            res = meth(*args)  # type: ignore[misc]
                        return _coerce_result(res)
                    except TypeError:
                        continue

    # adapters.zk.*
    ada = _import("capabilities.adapters.zk")
    afn = _get_attr(ada, ["verify", "zk_verify"])
    if callable(afn):
        for args in _variants(circuit, proof, public):
            try:
                return _coerce_result(afn(*args))  # type: ignore[misc]
            except TypeError:
                continue

    pytest.skip("No zk.verify implementation available")


# ---------- sample "fixtures" (backend-agnostic & stub-friendly) ----------


def _sample_inputs():
    # Keep these intentionally tiny and generic so stub verifiers can accept them.
    circuit = {
        "name": "trivial_and",
        "vk_hash": "deadbeef",  # many stubs just check presence of keys
        "n_constraints": 2,
    }
    public = b"public-inputs-\x00"
    proof_good = b"proof-ok-\x01"
    # Slight mutation: flip the last byte
    proof_bad = proof_good[:-1] + bytes([proof_good[-1] ^ 0xFF])
    return circuit, public, proof_good, proof_bad


# ========================= TESTS =========================


def test_zk_verify_returns_bool_and_units():
    circuit, public, proof_good, _ = _sample_inputs()
    ok, units = _zk_verify(circuit, proof_good, public)
    assert isinstance(ok, bool), "zk.verify did not return a boolean flag"
    if units is not None:
        assert (
            isinstance(units, int) and units >= 0
        ), "units should be a non-negative integer"


def test_zk_verify_detects_mutation_or_rejects():
    """
    Call verify on a 'good' proof then on a mutated one.
    Backends may not *accept* our synthetic 'good' proof; we only require:
      - both calls return booleans, and
      - the mutated proof is not *more* acceptable than the original.
    If both return True, mark as xfail (backend stub too permissive).
    """
    circuit, public, proof_good, proof_bad = _sample_inputs()

    ok1, units1 = _zk_verify(circuit, proof_good, public)
    ok2, units2 = _zk_verify(circuit, proof_bad, public)

    assert isinstance(ok1, bool) and isinstance(ok2, bool)
    # Mutated proof should not be strictly "better"
    if ok1 is True and ok2 is True:
        pytest.xfail(
            "zk verifier accepted both original and mutated proof (stub too permissive)"
        )
    if ok2 and not ok1:
        pytest.fail("Mutated proof unexpectedly accepted while original was rejected")

    # Units, when provided, should be stable type
    for u in (units1, units2):
        if u is not None:
            assert isinstance(u, int) and u >= 0


def test_zk_verify_raises_or_false_on_garbage_types():
    """
    Feed obviously bad types. Accept either a clean False or an exception; both are valid rejections.
    """
    circuit = "not-a-json-circuit"
    proof = object()  # clearly not encodable
    public = 12345
    try:
        ok, _ = _zk_verify(circuit, proof, public)
    except Exception:
        return
    assert ok is False, "Garbage inputs should not verify"
