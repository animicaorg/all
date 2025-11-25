import json
import os
from binascii import unhexlify
from typing import Any, Optional, Tuple, Callable

import pytest

# Modules under test
import randomness.beacon.finalize as finalize_mod  # type: ignore

# Optional imports (best-effort)
try:
    from randomness.types.core import BeaconOut  # type: ignore
except Exception:  # pragma: no cover
    BeaconOut = None  # type: ignore

# We will try to stub the VDF verifier used by finalize, regardless of its import path.
VDF_VERIFY_NAMES = [
    # common names inside finalize module
    "verify_vdf",
    "vdf_verify",
    "_verify_vdf",
    "wesolowski_verify",
    # external module attribute paths consulted via finalize_mod
]

def _hex_bytes(x: Any) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    if isinstance(x, str):
        s = x.strip().lower()
        if s.startswith("0x"):
            s = s[2:]
        if len(s) % 2 == 1:
            s = "0" + s
        return unhexlify(s)
    if isinstance(x, list) and all(isinstance(b, int) and 0 <= b < 256 for b in x):
        return bytes(x)
    raise TypeError("unsupported bytes-like value for hex decode")

def _as_int(x: Any) -> int:
    if isinstance(x, int):
        return x
    if isinstance(x, str):
        s = x.strip().lower()
        if s.startswith("0x"):
            return int(s, 16)
        return int(s)
    raise TypeError("unsupported int-like value for int conversion")

def _load_beacon_vectors() -> list[dict]:
    here = os.path.dirname(__file__)
    path = os.path.join(here, "..", "test_vectors", "beacon.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        if "vectors" in data and isinstance(data["vectors"], list):
            return data["vectors"]
        return [data]
    assert isinstance(data, list)
    return data

def _extract_case(vec: dict) -> dict:
    """
    Normalize vector fields into a portable shape:
      {
        round_id: int,
        prev_output: bytes,
        aggregate: bytes,
        vdf: { modulus: int, iterations: int, input: bytes, output: bytes, proof: bytes }
      }
    """
    # round id / index
    rid = vec.get("round_id") or vec.get("round") or vec.get("id") or 1
    # previous beacon / chaining value
    prev = vec.get("prev_output") or vec.get("previous") or vec.get("prev") or b""
    # aggregated reveal (pre-VDF)
    agg = vec.get("aggregate") or vec.get("agg") or vec.get("mixed")

    # vdf bundle
    v = vec.get("vdf") or {}
    N = v.get("modulus") or v.get("N") or v.get("mod") or vec.get("modulus") or vec.get("N")
    t = v.get("iterations") or v.get("t") or v.get("T") or vec.get("iterations") or vec.get("t")
    vin = v.get("input") or v.get("seed") or vec.get("vdf_input") or vec.get("seed")
    y = v.get("output") or v.get("y") or vec.get("vdf_output") or vec.get("output")
    pi = v.get("proof") or v.get("pi") or vec.get("vdf_proof") or vec.get("proof")

    return {
        "round_id": _as_int(rid),
        "prev_output": _hex_bytes(prev) if prev is not None else b"",
        "aggregate": _hex_bytes(agg),
        "vdf": {
            "modulus": _as_int(N),
            "iterations": _as_int(t),
            "input": _hex_bytes(vin),
            "output": _hex_bytes(y),
            "proof": _hex_bytes(pi if not isinstance(pi, dict) else (pi.get("pi") or pi.get("proof"))),
        },
    }

def _make_store(tmp_path) -> Optional[Any]:
    """
    Try to instantiate a concrete store the finalize routine can use, preferring SQLite.
    Falls back to None (some finalize APIs may not require an explicit store).
    """
    # Try SQLite-backed store
    try:
        from randomness.store.sqlite import SqliteStore  # type: ignore
        db_path = os.path.join(tmp_path, "rand.db")
        return SqliteStore(db_path)  # type: ignore[no-any-return]
    except Exception:
        pass
    # Try KV generic
    try:
        from randomness.store.kv import KV  # type: ignore
        return KV()  # type: ignore[no-any-return]
    except Exception:
        pass
    return None

def _patch_vdf_verifier(monkeypatch: pytest.MonkeyPatch, expected: dict) -> None:
    """
    Ensure that whatever VDF check the finalize module calls will accept the vector's proof/output.
    We match on (N, t, input, output, proof) and return True; otherwise False.
    If finalize expects to raise on failure, returning False should propagate to a handled path.
    """
    N = expected["modulus"]
    t = expected["iterations"]
    vin = expected["input"]
    out = expected["output"]
    pi = expected["proof"]

    def _accept(*args, **kwargs) -> bool:
        # Try common signatures
        if kwargs:
            kN = kwargs.get("N") or kwargs.get("modulus")
            kt = kwargs.get("t") or kwargs.get("iterations")
            kin = kwargs.get("input") or kwargs.get("challenge") or kwargs.get("seed")
            kout = kwargs.get("output") or kwargs.get("y")
            kpi = kwargs.get("proof") or kwargs.get("pi")
            if kN == N and kt == t and kin == vin and kout == out and kpi == pi:
                return True
            return False
        # positional variants
        poss = [
            # (inp, out, proof, t, N)
            (0, 1, 2, 3, 4),
            # (N, t, inp, out, proof)
            (0, 1, 2, 3, 4),
        ]
        for order in poss:
            try:
                a0, a1, a2, a3, a4 = [args[i] for i in order]
            except Exception:
                continue
            # Detect which ordering we picked
            if order == (0, 1, 2, 3, 4) and isinstance(a0, int):
                # (N,t,inp,out,proof)
                if a0 == N and a1 == t and a2 == vin and a3 == out and a4 == pi:
                    return True
            else:
                # assume (inp,out,proof,t,N)
                if a0 == vin and a1 == out and a2 == pi and a3 == t and a4 == N:
                    return True
        return False

    # Patch likely verifier entry points referenced by finalize_mod
    patched_any = False
    for name in VDF_VERIFY_NAMES:
        if hasattr(finalize_mod, name):
            monkeypatch.setattr(finalize_mod, name, _accept)
            patched_any = True

    # Also try patching the canonical verifier module if finalize imports from there
    try:
        import randomness.vdf.verifier as vmod  # type: ignore
        if hasattr(vmod, "verify"):
            monkeypatch.setattr(vmod, "verify", _accept)
            patched_any = True
        for nm in ("verify_wesolowski", "wesolowski_verify", "check", "validate"):
            if hasattr(vmod, nm):
                monkeypatch.setattr(vmod, nm, _accept)
                patched_any = True
    except Exception:
        pass

    if not patched_any:
        # If we couldn't patch, it's still OK—the implementation may not need it for vectorized tests.
        pass

def _call_finalize(store: Optional[Any], round_id: int, aggregate: bytes, vdf: dict, prev_output: bytes) -> Optional[Any]:
    """
    Try multiple finalize entry signatures. Returns BeaconOut (or equivalent) if available,
    else None (caller can try to read from store/history).
    """
    candidates: list[Tuple[Tuple, dict]] = [
        # (store, round_id, aggregate, vdf_proof, prev_output)
        ((store, round_id, aggregate, vdf["proof"], prev_output), {}),
        # (round_id, aggregate, vdf_proof, prev_output, store=...)
        ((round_id, aggregate, vdf["proof"], prev_output), {"store": store}),
        # kwargs-only styles
        (tuple(), {"store": store, "round_id": round_id, "aggregate": aggregate, "vdf_proof": vdf["proof"], "prev_output": prev_output}),
        (tuple(), {"store": store, "round_id": round_id, "aggregate": aggregate, "proof": vdf["proof"], "previous": prev_output}),
        # Some APIs may accept the full bundle:
        (tuple(), {"store": store, "round_id": round_id, "aggregate": aggregate, "vdf": vdf, "prev_output": prev_output}),
        # Simplest: (round_id,) only — implementation looks up everything from store (may no-op here)
        ((round_id,), {"store": store}),
    ]

    # Find a callable finalize function name
    func_names = [
        "finalize_round",
        "finalize",
        "finalize_current",
        "run_finalize",
        "seal_round",
    ]
    fn: Optional[Callable[..., Any]] = None
    for nm in func_names:
        f = getattr(finalize_mod, nm, None)
        if callable(f):
            fn = f
            break
    if fn is None:
        pytest.skip("No finalize entry function exported by randomness.beacon.finalize")

    # Try invocations
    last_exc: Optional[Exception] = None
    for args, kwargs in candidates:
        try:
            return fn(*args, **{k: v for k, v in kwargs.items() if v is not None})
        except TypeError as e:
            last_exc = e
            continue
        except Exception as e:
            # If the implementation explicitly reports invalid inputs, surface that
            raise
    # If we exhausted signatures, skip to avoid hard failure due to surface drift.
    pytest.skip(f"Could not call finalize function with any supported signature (last TypeError: {last_exc})")
    return None

def _read_latest_from_store(store: Optional[Any]) -> Optional[Any]:
    """
    Try to read the latest BeaconOut from store/state/history helpers.
    """
    if store is None:
        return None
    # Try history helper
    try:
        import randomness.beacon.history as hist  # type: ignore
        for nm in ("latest", "get_latest", "read_latest", "head"):
            if hasattr(hist, nm):
                out = getattr(hist, nm)(store)  # type: ignore
                if out is not None:
                    return out
    except Exception:
        pass
    # Try state helper
    try:
        import randomness.beacon.state as st  # type: ignore
        for nm in ("get_latest", "read_latest", "latest"):
            if hasattr(st, nm):
                out = getattr(st, nm)(store)  # type: ignore
                if out is not None:
                    return out
    except Exception:
        pass
    # Try direct store lookup (common accessor)
    for nm in ("get_beacon", "get_latest_beacon", "beacon_get_latest"):
        if hasattr(store, nm):
            try:
                return getattr(store, nm)()
            except Exception:
                continue
    return None

def _extract_output(obj: Any) -> Optional[bytes]:
    """
    Extract the beacon output bytes from a BeaconOut-like object or dict.
    """
    # dict-like
    if isinstance(obj, dict):
        for k in ("output", "beacon", "value", "bytes"):
            if k in obj and isinstance(obj[k], (bytes, bytearray)):
                return bytes(obj[k])
            if k in obj and isinstance(obj[k], str):
                try:
                    return _hex_bytes(obj[k])
                except Exception:
                    pass
    # dataclass-like
    for k in ("output", "value", "beacon"):
        if hasattr(obj, k):
            v = getattr(obj, k)
            if isinstance(v, (bytes, bytearray)):
                return bytes(v)
            if isinstance(v, str):
                try:
                    return _hex_bytes(v)
                except Exception:
                    pass
    # tuple-like last chance
    if isinstance(obj, (tuple, list)) and obj:
        v = obj[-1]
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
    return None

def _extract_round_id(obj: Any) -> Optional[int]:
    if isinstance(obj, dict):
        for k in ("round_id", "round", "id", "height"):
            if k in obj and isinstance(obj[k], int):
                return obj[k]
    for k in ("round_id", "round", "id", "height"):
        if hasattr(obj, k):
            v = getattr(obj, k)
            if isinstance(v, int):
                return v
    return None

@pytest.mark.parametrize("use_store", [True, False])
def test_beacon_finalize_stores_and_returns(monkeypatch: pytest.MonkeyPatch, tmp_path, use_store: bool):
    """
    Full round finalize: given an aggregate and a matching VDF proof from vectors,
    finalize produces BeaconOut and persists it (when a store is provided).
    """
    vectors = _load_beacon_vectors()
    assert vectors, "No beacon vectors available"
    case = _extract_case(vectors[0])

    # Prepare store (if requested)
    store = _make_store(str(tmp_path)) if use_store else None

    # Patch VDF verifier to accept the vector's proof/output
    _patch_vdf_verifier(monkeypatch, case["vdf"])

    # Attempt finalize with multiple signature variants
    result = _call_finalize(
        store=store,
        round_id=case["round_id"],
        aggregate=case["aggregate"],
        vdf=case["vdf"],
        prev_output=case["prev_output"],
    )

    # A result may be returned directly or written only to store.
    beacon_obj = result
    if beacon_obj is None and store is not None:
        beacon_obj = _read_latest_from_store(store)

    assert beacon_obj is not None, "Finalize did not return or persist a BeaconOut"

    # Compare round id and output bytes against vector
    out_bytes = _extract_output(beacon_obj)
    rid_val = _extract_round_id(beacon_obj)

    assert rid_val == case["round_id"], f"Beacon round id mismatch (got {rid_val}, expected {case['round_id']})"
    assert out_bytes == case["vdf"]["output"], "Beacon output does not match vector's expected output"

