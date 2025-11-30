"""
Animica zk.verifiers.poseidon
============================

Poseidon hash over BN254 (altbn128) scalar field (Fr).

This module implements a production-ready Poseidon permutation and sponge,
but **keeps parameters external** so that your verifier uses *exactly the
same parameters as your circuits*. Register your circuit's parameter set at
startup (either programmatically or by loading a JSON file that includes the
MDS matrix and round constants).

Why externalized?
-----------------
Different circuits/projects pick different Poseidon parameterizations
(e.g., width `t`, number of full/partial rounds `R_F`/`R_P`, round constants
and MDS). Hardcoding the wrong set leads to silent verification failures.
By loading the parameters used by your circuits, you guarantee matching
behavior.

Field
-----
We work in the BN254 scalar field Fr, using the curve order as the modulus.

Public API
----------
- PoseidonParams(t, R_F, R_P, alpha, mds, rc)
- register_params(name, params)
- load_params_json(path, name=None)  # JSON schema documented below
- get_params(name="bn254_t3")
- poseidon_permute(state, params)
- poseidon_hash(inputs, *, params_name="bn254_t3")  # sponge (capacity=1)
- poseidon_hash_many(inputs, *, t, params_name)     # for custom rate

JSON schema (example)
---------------------
{
  "field": "bn254:fr",
  "alpha": 5,
  "t": 3,
  "R_F": 8,
  "R_P": 57,
  "mds": [[...t ints...], [...], [...]],
  "rc":  [[...t ints...], ... R_F+R_P rows ...]
}

All integers are encoded as decimal strings or JSON numbers mod Fr.

Notes
-----
- S-Box exponent is fixed to alpha=5 (the most common choice); the code allows
  any odd alpha >= 3 if your circuits use a different one.
- The sponge uses a *capacity of 1 word* (t-1 rate). We absorb inputs in
  chunks of `t-1`, permuting after each chunk; then finalize with one extra
  permutation and return `state[0]` as the hash output (the common convention).

License: MIT
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# Use the BN254 scalar field modulus via pairing module (keeps a single source of truth)
try:
    from .pairing_bn254 import curve_order  # type: ignore
except Exception:  # pragma: no cover
    # Fallback literal (BN254 group order r) to avoid import cycles during bootstraps.
    # Keeping this here guards against early import ordering; the canonical source is pairing_bn254.
    curve_order = lambda: int(
        21888242871839275222246405745257275088548364400416034343698204186575808495617
    )  # noqa: E731


# ---------------------------
# Field arithmetic (mod Fr)
# ---------------------------

_MOD = int(curve_order())


def _fadd(a: int, b: int) -> int:
    return (int(a) + int(b)) % _MOD


def _fsub(a: int, b: int) -> int:
    return (int(a) - int(b)) % _MOD


def _fmul(a: int, b: int) -> int:
    return (int(a) * int(b)) % _MOD


def _fexp(a: int, e: int) -> int:
    return pow(int(a) % _MOD, int(e), _MOD)


def _fpow_alpha(x: int, alpha: int) -> int:
    # Fast path for alpha=5 (x^5 = x * x^2 * x^2)
    if alpha == 5:
        x2 = _fmul(x, x)
        x4 = _fmul(x2, x2)
        return _fmul(x, x4)
    return _fexp(x, alpha)


# ---------------------------
# Parameters & registry
# ---------------------------


@dataclass(frozen=True)
class PoseidonParams:
    t: int  # state width
    R_F: int  # number of full rounds
    R_P: int  # number of partial rounds
    alpha: int  # S-box exponent (odd >= 3, commonly 5)
    mds: List[List[int]]  # MDS matrix, shape t x t
    rc: List[List[int]]  # round constants, shape (R_F + R_P) x t

    def validate(self) -> None:
        if self.t < 2:
            raise ValueError("t must be >= 2")
        if self.R_F % 2 != 0:
            raise ValueError(
                "R_F must be even (split half-before/after partial rounds)"
            )
        if self.alpha < 3 or self.alpha % 2 == 0:
            raise ValueError("alpha must be an odd integer >= 3")
        if len(self.mds) != self.t or any(len(row) != self.t for row in self.mds):
            raise ValueError("mds must be t x t")
        expected_rounds = self.R_F + self.R_P
        if len(self.rc) != expected_rounds or any(
            len(row) != self.t for row in self.rc
        ):
            raise ValueError(f"rc must be (R_F+R_P) x t = {expected_rounds} x {self.t}")


# Global registry keyed by a short name (e.g., "bn254_t3")
_PARAMS_REGISTRY: Dict[str, PoseidonParams] = {}


def register_params(name: str, params: PoseidonParams) -> None:
    """
    Register a Poseidon parameter set under `name`.

    Call this at process startup with the exact params your circuits use.
    """
    if not name or not isinstance(name, str):
        raise ValueError("name must be a non-empty string")
    params.validate()
    _PARAMS_REGISTRY[name] = params


def get_params(name: str = "bn254_t3") -> PoseidonParams:
    if name not in _PARAMS_REGISTRY:
        raise KeyError(
            f"Poseidon params '{name}' are not registered. "
            "Load them with load_params_json(...) or register_params(...)."
        )
    return _PARAMS_REGISTRY[name]


def _to_int(x: Union[int, str]) -> int:
    if isinstance(x, int):
        return x % _MOD
    s = str(x).strip().lower()
    if s.startswith("0x"):
        return int(s, 16) % _MOD
    return int(s) % _MOD


def load_params_json(path: str, name: Optional[str] = None) -> PoseidonParams:
    """
    Load a Poseidon params JSON file and register it.

    If `name` is None, a name is derived from the filename (without extension).
    Returns the PoseidonParams object.
    """
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    t = int(raw["t"])
    R_F = int(raw["R_F"])
    R_P = int(raw["R_P"])
    alpha = int(raw.get("alpha", 5))

    mds = [[_to_int(v) for v in row] for row in raw["mds"]]
    rc = [[_to_int(v) for v in row] for row in raw["rc"]]

    params = PoseidonParams(t=t, R_F=R_F, R_P=R_P, alpha=alpha, mds=mds, rc=rc)
    params.validate()

    reg_name = name or os.path.splitext(os.path.basename(path))[0]
    register_params(reg_name, params)
    return params


# ---------------------------
# Permutation
# ---------------------------


def _apply_mds(state: List[int], mds: List[List[int]]) -> List[int]:
    t = len(state)
    out = [0] * t
    for i in range(t):
        acc = 0
        row = mds[i]
        # Manual unroll helps a tiny bit for small t; keep it simple & clear.
        for j in range(t):
            acc = _fadd(acc, _fmul(row[j], state[j]))
        out[i] = acc
    return out


def poseidon_permute(state: Sequence[int], params: PoseidonParams) -> List[int]:
    """
    Poseidon permutation (in-place logic implemented functionally).

    Round schedule:
      - First R_F/2 full rounds (S-box on all t elements)
      - R_P partial rounds (S-box on the *first* element only)
      - Last  R_F/2 full rounds

    Returns a new list with the permuted state.
    """
    t, R_F, R_P, alpha, mds, rc = (
        params.t,
        params.R_F,
        params.R_P,
        params.alpha,
        params.mds,
        params.rc,
    )
    if len(state) != t:
        raise ValueError(f"state length {len(state)} != t={t}")

    x = [int(v) % _MOD for v in state]
    r = 0  # round pointer

    # First half full rounds
    half = R_F // 2
    for _ in range(half):
        # add round constants
        for i in range(t):
            x[i] = _fadd(x[i], rc[r][i])
        # S-box on all elements
        for i in range(t):
            x[i] = _fpow_alpha(x[i], alpha)
        # MDS
        x = _apply_mds(x, mds)
        r += 1

    # Partial rounds
    for _ in range(R_P):
        for i in range(t):
            x[i] = _fadd(x[i], rc[r][i])
        # S-box only on first element
        x[0] = _fpow_alpha(x[0], alpha)
        x = _apply_mds(x, mds)
        r += 1

    # Second half full rounds
    for _ in range(half):
        for i in range(t):
            x[i] = _fadd(x[i], rc[r][i])
        for i in range(t):
            x[i] = _fpow_alpha(x[i], alpha)
        x = _apply_mds(x, mds)
        r += 1

    assert r == R_F + R_P, "round counter mismatch"
    return x


# ---------------------------
# Sponge / Hash interface
# ---------------------------


def poseidon_hash(
    inputs: Sequence[int],
    *,
    params_name: str = "bn254_t3",
) -> int:
    """
    Poseidon hash with capacity=1 (rate=t-1), returning the first state element.

    Absorb in chunks of size (t-1). After absorbing all chunks, do *one more*
    permutation and return state[0].

    This matches common circuit usage where `t=3` (rate=2) for 2-ary hashing,
    or other widths for Merkle arity > 2.
    """
    params = get_params(params_name)
    t = params.t
    rate = t - 1
    if rate <= 0:
        raise ValueError("t must be >= 2")

    # zero-initialized state
    state = [0] * t

    # absorb
    idx = 0
    n = len(inputs)
    while idx < n:
        # absorb up to `rate` elements
        for i in range(rate):
            if idx >= n:
                break
            state[i] = _fadd(state[i], int(inputs[idx]) % _MOD)
            idx += 1
        # permute after each chunk
        state = poseidon_permute(state, params)

    # final permutation before squeeze (standard convention)
    state = poseidon_permute(state, params)
    return int(state[0])


def poseidon_hash_many(
    inputs: Sequence[int],
    *,
    t: int,
    params_name: str,
) -> int:
    """
    Same as `poseidon_hash` but asserts that the registered params have the requested width `t`.
    Useful to catch accidental mismatches between circuits and verifier.
    """
    params = get_params(params_name)
    if params.t != t:
        raise ValueError(f"Params '{params_name}' have t={params.t}, expected t={t}")
    return poseidon_hash(inputs, params_name=params_name)


# ---------------------------
# Optional: built-in minimal default (safe but NOT your circuit params)
# ---------------------------
# We intentionally do NOT hardcode any real circuit parameter set here.
# However, for developer ergonomics, we register a placeholder that derives a
# trivially structured (invertible) MDS and round constants by hashing indices.
# This is **NOT** meant for production circuits; it merely allows smoke tests
# without external files. Replace it in real deployments by calling
# `load_params_json("path/to/your/circuit_params.json", name="bn254_t3")`.


def _derive_placeholder_params(name: str = "bn254_t3") -> None:
    import hashlib

    t = 3
    R_F = 8
    R_P = 57
    alpha = 5

    # Simple invertible MDS: a Vandermonde-like matrix over Fr with small bases.
    bases = [2, 3, 5]
    mds: List[List[int]] = []
    for i in range(t):
        row = []
        for j in range(t):
            row.append(_fexp(bases[j], i + 1))
        mds.append(row)

    # Round constants derived from SHA3-256 over a domain-separated seed.
    rc: List[List[int]] = []
    total_rounds = R_F + R_P
    for r in range(total_rounds):
        row = []
        for i in range(t):
            h = hashlib.sha3_256(
                f"poseidon/placeholder/bn254/t={t}/r={r}/i={i}".encode()
            ).digest()
            v = int.from_bytes(h, "big") % _MOD
            row.append(v)
        rc.append(row)

    register_params(
        name, PoseidonParams(t=t, R_F=R_F, R_P=R_P, alpha=alpha, mds=mds, rc=rc)
    )


# Register the placeholder so local smoke tests work immediately.
# Overwrite this by loading your *actual* circuit params at startup.
try:
    _derive_placeholder_params("bn254_t3")
except Exception:  # pragma: no cover
    pass


# ---------------------------
# Self-test (manual)
# ---------------------------

if __name__ == "__main__":  # pragma: no cover
    print("[poseidon] BN254 Fr modulus =", _MOD)
    p = get_params()  # placeholder unless you've loaded real params
    print(
        f"[poseidon] params name='bn254_t3' t={p.t} R_F={p.R_F} R_P={p.R_P} alpha={p.alpha}"
    )

    # Determinism & basic properties smoke test
    x = [1, 2]
    h1 = poseidon_hash(x)
    h2 = poseidon_hash(x)
    assert h1 == h2, "non-deterministic hash"

    # Changing one input changes the output
    assert poseidon_hash([1, 3]) != h1, "input sensitivity failed"

    # Multi-chunk absorb works
    long_inputs = list(range(10))
    h_long = poseidon_hash(long_inputs)
    print("hash([1,2])   =", h1)
    print("hash(range10) =", h_long)
    print("ok.")
