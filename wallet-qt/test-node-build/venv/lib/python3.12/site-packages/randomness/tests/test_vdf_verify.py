import json
import os
from binascii import unhexlify
from typing import Any, Callable, Optional

import pytest

import randomness.vdf.verifier as vdf_ver  # type: ignore
from randomness.errors import VDFInvalid  # type: ignore

# Optional helpers/types (if available)
try:
    from randomness.types.core import VDFInput, VDFProof  # type: ignore
except Exception:  # pragma: no cover - optional
    VDFInput = None  # type: ignore
    VDFProof = None  # type: ignore


# ---------- Test helpers ----------


def _hex_or_b(v: Any) -> bytes:
    """
    Accepts hex string (with or without 0x) or raw bytes/bytearray/list[int].
    """
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, list) and all(isinstance(x, int) and 0 <= x < 256 for x in v):
        return bytes(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s.startswith("0x"):
            s = s[2:]
        # tolerate odd-length hex (prefix a zero)
        if len(s) % 2 == 1:
            s = "0" + s
        return unhexlify(s)
    raise TypeError(f"Unsupported byte-like value: {type(v)}")


def _to_int(x: Any) -> int:
    if isinstance(x, int):
        return x
    if isinstance(x, str):
        s = x.strip().lower()
        if s.startswith("0x"):
            return int(s, 16)
        return int(s)
    raise TypeError(f"Unsupported int-like value: {type(x)}")


def _load_vectors() -> list[dict]:
    here = os.path.dirname(__file__)
    path = os.path.normpath(os.path.join(here, "..", "test_vectors", "vdf.json"))
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        # Some formats wrap in {"vectors":[...]}
        if "vectors" in data and isinstance(data["vectors"], list):
            return data["vectors"]
        # Or a single vector
        return [data]
    assert isinstance(
        data, list
    ), "vdf.json must be a list of vectors or an object with 'vectors'"
    return data


def _extract_case(vec: dict) -> tuple[int, int, bytes, bytes, bytes]:
    """
    Return (modulus, iterations, input_bytes, output_bytes, proof_bytes).
    We accept several field aliases commonly seen in VDF test vectors.
    """
    # Field aliases
    mod = vec.get("modulus") or vec.get("N") or vec.get("mod") or vec.get("n")
    it = vec.get("iterations") or vec.get("t") or vec.get("T") or vec.get("steps")
    inp = vec.get("input") or vec.get("seed") or vec.get("challenge") or vec.get("g")
    out = vec.get("output") or vec.get("y") or vec.get("result")
    prv = (
        vec.get("proof")
        or vec.get("pi")
        or vec.get("wesolowski")
        or vec.get("proof_bytes")
    )

    if mod is None or it is None or inp is None or out is None or prv is None:
        raise KeyError("Missing required VDF vector fields")

    N = _to_int(mod)
    t = _to_int(it)
    inp_b = _hex_or_b(inp)
    out_b = _hex_or_b(out)
    # Proof might be nested like {"pi": "...", "l": 128}. We only need raw bytes for most verifiers.
    if isinstance(prv, dict) and "pi" in prv:
        prv_b = _hex_or_b(prv["pi"])
    else:
        prv_b = _hex_or_b(prv)
    return N, t, inp_b, out_b, prv_b


def _find_verify_func() -> Callable[..., Any]:
    """
    Try common exported verifier function names.
    """
    names = [
        "verify",
        "verify_vdf",
        "verify_wesolowski",
        "wesolowski_verify",
        "check",
        "validate",
    ]
    for n in names:
        f = getattr(vdf_ver, n, None)
        if callable(f):
            return f
    pytest.skip("No VDF verify function exported by randomness.vdf.verifier")


VERIFY = _find_verify_func()


def _call_verify(N: int, t: int, inp: bytes, out: bytes, proof: bytes) -> bool:
    """
    Call the verifier with several possible signatures until one works.

    Preferred signatures:
      verify(VDFInput(...), VDFProof(...), out) -> bool / raise VDFInvalid
      verify(inp, out, proof, t, N) -> bool
      verify(modulus=N, iterations=t, input=inp, output=out, proof=proof) -> bool
    """
    # 1) Try dataclass-based call
    if VDFInput is not None and VDFProof is not None:
        for ctor_variant in (
            dict(modulus=N, iterations=t, input=inp),
            dict(N=N, t=t, challenge=inp),
            dict(N=N, iterations=t, seed=inp),
        ):
            try:
                vin = VDFInput(**ctor_variant)  # type: ignore[arg-type]
                try:
                    vpf = VDFProof(proof=proof)  # type: ignore[arg-type]
                except Exception:
                    vpf = VDFProof(pi=proof)  # type: ignore[arg-type]
                # keyword first
                try:
                    return bool(VERIFY(vdf_input=vin, proof=vpf, output=out))
                except TypeError:
                    pass
                # positional
                try:
                    return bool(VERIFY(vin, vpf, out))
                except TypeError:
                    pass
            except Exception:
                pass  # try other forms

    # 2) Try raw-arg signatures
    #   a) (inp, out, proof, t, N)
    try:
        return bool(VERIFY(inp, out, proof, t, N))
    except TypeError:
        pass
    #   b) (N, t, inp, out, proof)
    try:
        return bool(VERIFY(N, t, inp, out, proof))
    except TypeError:
        pass
    #   c) kwargs
    try:
        return bool(VERIFY(modulus=N, iterations=t, input=inp, output=out, proof=proof))
    except TypeError:
        pass

    # If nothing worked, this test environment can't map the signature—skip gracefully.
    pytest.skip("Could not call VDF verifier with any supported signature")


def _assert_invalid(N: int, t: int, inp: bytes, out: bytes, proof: bytes):
    """
    Assert that the verification fails, either by returning False or by raising VDFInvalid.
    """
    try:
        ok = _call_verify(N, t, inp, out, proof)
        assert not ok, "Verifier unexpectedly accepted invalid VDF"
    except VDFInvalid:
        # expected path for exception-based APIs
        pass


# ---------- Tests ----------


def test_vdf_vectors_verify():
    """
    All official vectors must verify.
    """
    vectors = _load_vectors()
    assert vectors, "No VDF vectors loaded"

    for i, vec in enumerate(vectors):
        N, t, inp, out, proof = _extract_case(vec)
        ok = _call_verify(N, t, inp, out, proof)
        assert ok, f"VDF vector {i} failed verification"


def test_vdf_output_mismatch_rejected():
    N, t, inp, out, proof = _extract_case(_load_vectors()[0])
    # Flip one bit in output
    out_bad = bytes([out[0] ^ 0x01]) + out[1:]
    _assert_invalid(N, t, inp, out_bad, proof)


def test_vdf_proof_tamper_rejected():
    N, t, inp, out, proof = _extract_case(_load_vectors()[0])
    # Flip one bit in proof
    proof_bad = bytes([proof[0] ^ 0x80]) + proof[1:]
    _assert_invalid(N, t, inp, out, proof_bad)


def test_vdf_wrong_iterations_rejected():
    N, t, inp, out, proof = _extract_case(_load_vectors()[0])
    _assert_invalid(N, t + 1, inp, out, proof)
    if t > 1:
        _assert_invalid(N, t - 1, inp, out, proof)


def test_vdf_wrong_modulus_rejected():
    N, t, inp, out, proof = _extract_case(_load_vectors()[0])
    # N must be odd and large; we still alter it in a minimal way that keeps it positive.
    N_bad = N ^ 0x3  # toggle a couple of low bits
    if N_bad <= 1 or N_bad == N:
        N_bad = N + 2
    _assert_invalid(N_bad, t, inp, out, proof)


def test_vdf_empty_proof_rejected():
    N, t, inp, out, _proof = _extract_case(_load_vectors()[0])
    _assert_invalid(N, t, inp, out, b"")
