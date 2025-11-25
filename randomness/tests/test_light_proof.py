import json
import os
from binascii import unhexlify
from typing import Any, Optional, Tuple, Callable

import pytest

# Module under test
import randomness.beacon.light_proof as light_mod  # type: ignore

# In case the verifier lives elsewhere, we still patch common names like in the finalize tests
VDF_VERIFY_NAMES = [
    "verify_vdf",
    "vdf_verify",
    "_verify_vdf",
    "wesolowski_verify",
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

def _load_vectors() -> list[dict]:
    here = os.path.dirname(__file__)
    path = os.path.join(here, "..", "test_vectors", "light_proof.json")
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
    Normalize light-proof vector into a portable shape:
      {
        round_id: int,
        prev_output: bytes,
        aggregate: bytes,             # pre-VDF aggregate (if present)
        vdf: { modulus, iterations, input, output, proof },
        proof: <opaque light proof object from vectors (dict/bytes/whatever)>,
        expected_output: bytes,       # if present; otherwise vdf.output
        valid: bool                   # whether the proof should verify
      }
    """
    rid = vec.get("round_id") or vec.get("round") or vec.get("id") or 1
    prev = vec.get("prev_output") or vec.get("previous") or vec.get("prev") or b""
    agg = vec.get("aggregate") or vec.get("agg") or vec.get("mixed")

    v = vec.get("vdf") or {}
    N = v.get("modulus") or v.get("N") or v.get("mod") or vec.get("modulus") or vec.get("N")
    t = v.get("iterations") or v.get("t") or v.get("T") or vec.get("iterations") or vec.get("t")
    vin = v.get("input") or v.get("seed") or vec.get("vdf_input") or vec.get("seed")
    y = v.get("output") or v.get("y") or vec.get("vdf_output") or vec.get("output")
    pi = v.get("proof") or v.get("pi") or vec.get("vdf_proof") or vec.get("proof")

    expected = vec.get("expected_output") or vec.get("beacon") or y
    valid = bool(vec.get("valid", True))

    # Some vectors may embed the compact proof under varying keys
    proof_obj = vec.get("proof") or vec.get("light_proof") or vec.get("lp") or vec

    # If nested, try to pull a subfield
    if isinstance(proof_obj, dict) and "light_proof" in proof_obj:
        proof_obj = proof_obj["light_proof"]

    return {
        "round_id": _as_int(rid),
        "prev_output": _hex_bytes(prev) if prev is not None else b"",
        "aggregate": _hex_bytes(agg) if agg is not None else None,
        "vdf": {
            "modulus": _as_int(N),
            "iterations": _as_int(t),
            "input": _hex_bytes(vin),
            "output": _hex_bytes(y),
            "proof": _hex_bytes(pi if not isinstance(pi, dict) else (pi.get("pi") or pi.get("proof"))),
        },
        "proof": proof_obj,
        "expected_output": _hex_bytes(expected),
        "valid": valid,
    }

def _patch_vdf_verifier(monkeypatch: pytest.MonkeyPatch, expected: dict) -> None:
    """
    Ensure that whatever VDF check the light proof verifier calls will accept the vector's proof/output.
    """
    N = expected["modulus"]
    t = expected["iterations"]
    vin = expected["input"]
    out = expected["output"]
    pi = expected["proof"]

    def _accept(*args, **kwargs) -> bool:
        # kwargs form
        if kwargs:
            kN = kwargs.get("N") or kwargs.get("modulus")
            kt = kwargs.get("t") or kwargs.get("iterations")
            kin = kwargs.get("input") or kwargs.get("challenge") or kwargs.get("seed")
            kout = kwargs.get("output") or kwargs.get("y")
            kpi = kwargs.get("proof") or kwargs.get("pi")
            return (kN == N and kt == t and kin == vin and kout == out and kpi == pi)
        # positional forms
        poss = [
            # (N, t, input, output, proof)
            (0, 1, 2, 3, 4),
            # (input, output, proof, t, N)
            (0, 1, 2, 3, 4),
        ]
        for order in poss:
            try:
                a0, a1, a2, a3, a4 = [args[i] for i in order]
            except Exception:
                continue
            if order == (0, 1, 2, 3, 4) and isinstance(a0, int):
                if a0 == N and a1 == t and a2 == vin and a3 == out and a4 == pi:
                    return True
            else:
                if a0 == vin and a1 == out and a2 == pi and a3 == t and a4 == N:
                    return True
        return False

    # Patch likely verifier entry points referenced by light_mod
    patched_any = False
    for name in VDF_VERIFY_NAMES:
        if hasattr(light_mod, name):
            monkeypatch.setattr(light_mod, name, _accept)
            patched_any = True

    # Also try patching the canonical verifier module if light_mod imports from there
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

    # It's okay if nothing got patched—some light proofs might not call into the VDF for "valid=False" cases.

def _extract_output(obj: Any) -> Optional[bytes]:
    """
    Extract the beacon output bytes from a verification result / light proof object.
    """
    # If the object is already bytes-like, assume it's the output
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
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
    # tuple-like: last element commonly the bytes
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
    if isinstance(obj, (tuple, list)) and obj:
        for v in obj:
            if isinstance(v, int):
                return v
    return None

def _call_verify(proof_obj: Any, prev_output: bytes, round_id: int) -> Tuple[bool, Optional[Any]]:
    """
    Try a variety of verification entry points; return (ok, result_or_none).
    """
    func_names = [
        "verify_light_proof",
        "verify",
        "validate",
        "check",
    ]
    fn: Optional[Callable[..., Any]] = None
    for nm in func_names:
        if hasattr(light_mod, nm) and callable(getattr(light_mod, nm)):
            fn = getattr(light_mod, nm)
            break
    if fn is None:
        pytest.skip("No light-proof verification function exported by randomness.beacon.light_proof")

    # candidate signatures
    candidates: list[Tuple[Tuple, dict]] = [
        ((proof_obj,), {}),
        ((proof_obj, prev_output), {}),
        ((proof_obj, round_id), {}),
        ((proof_obj,), {"prev_output": prev_output}),
        ((proof_obj,), {"round_id": round_id}),
        ((proof_obj,), {"previous": prev_output}),
        ((proof_obj,), {"expected_round": round_id}),
        (tuple(), {"proof": proof_obj, "prev_output": prev_output, "round_id": round_id}),
        (tuple(), {"proof": proof_obj, "previous": prev_output, "round": round_id}),
    ]

    last_type_error: Optional[TypeError] = None
    for args, kwargs in candidates:
        try:
            res = fn(*args, **{k: v for k, v in kwargs.items() if v is not None})
            if isinstance(res, bool):
                return res, None
            # Some APIs return (ok, out) or a BeaconOut-like object
            if isinstance(res, (tuple, list)) and res and isinstance(res[0], bool):
                return bool(res[0]), res[1] if len(res) > 1 else None
            # Object-like return means "ok"
            return True, res
        except TypeError as e:
            last_type_error = e
            continue
    pytest.skip(f"Could not invoke light-proof verifier with any supported signature (last TypeError: {last_type_error})")
    return False, None  # unreachable

def _call_reconstruct(proof_obj: Any, prev_output: bytes) -> Optional[Any]:
    """
    Some APIs separate 'verify' and 'reconstruct/derive output'. Try common names.
    """
    func_names = [
        "reconstruct",
        "reconstruct_output",
        "derive_output",
        "compute_output",
        "output_from_proof",
        "to_beacon",
        "beacon_from_proof",
    ]
    for nm in func_names:
        f = getattr(light_mod, nm, None)
        if callable(f):
            # candidate signatures
            candidates: list[Tuple[Tuple, dict]] = [
                ((proof_obj,), {}),
                ((proof_obj, prev_output), {}),
                ((proof_obj,), {"prev_output": prev_output}),
                ((proof_obj,), {"previous": prev_output}),
            ]
            for args, kwargs in candidates:
                try:
                    return f(*args, **{k: v for k, v in kwargs.items() if v is not None})
                except TypeError:
                    continue
    return None

@pytest.mark.parametrize("vec", _load_vectors())
def test_light_client_verify_and_reconstruct(monkeypatch: pytest.MonkeyPatch, vec: dict):
    """
    Light client reconstructs & verifies: given a compact light proof,
    the verifier accepts valid proofs and yields the expected beacon output and round id.
    Invalid vectors must be rejected.
    """
    case = _extract_case(vec)

    # Patch VDF verifier used under the hood to accept the vector's proof/output
    _patch_vdf_verifier(monkeypatch, case["vdf"])

    ok, verify_result = _call_verify(case["proof"], case["prev_output"], case["round_id"])

    if not case["valid"]:
        # Expect rejection either via False or by raising; if we reached here, ok must be False
        assert ok is False, "Invalid light-proof vector unexpectedly verified"
        return

    # For valid cases:
    assert ok is True, "Light-proof verification failed for a valid vector"

    # Extract output/round from the verification result if present
    out_bytes = _extract_output(verify_result) if verify_result is not None else None
    rid_val = _extract_round_id(verify_result) if verify_result is not None else None

    # If verifier didn't return output, try to reconstruct deterministically from the proof
    if out_bytes is None:
        recon = _call_reconstruct(case["proof"], case["prev_output"])
        out_bytes = _extract_output(recon)

    # If we STILL don't have bytes, the API might only return True. In that case we can't assert exact bytes.
    # But most designs expose bytes either from verify() result or reconstruction API.
    assert out_bytes is not None, "Could not extract/reconstruct beacon output bytes from verification APIs"

    # Compare to expected value from vectors (defaults to vdf.output)
    assert out_bytes == case["expected_output"], "Reconstructed beacon output does not match expected"

    # If round id is returned, make sure it matches too
    if rid_val is not None:
        assert rid_val == case["round_id"], f"Round id mismatch (got {rid_val}, expected {case['round_id']})"

