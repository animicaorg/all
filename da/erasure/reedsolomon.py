"""
Animica • DA • Erasure — Reed–Solomon (GF(2^8)) reference encoder/decoder.

This module provides a *systematic* RS(k, n) code over GF(256) suitable for
blob erasure-coding. It is dependency-free and focuses on correctness and
clarity, while being reasonably fast via small lookup tables.

Design
------
• Field: GF(2^8) with primitive polynomial 0x11D and generator α = 0x02.
• Code is systematic:
    - Data shard rows are the identity I_k.
    - Parity rows are a Vandermonde matrix V of shape (n-k)×k where the r-th
      row is [1, x, x^2, …, x^{k-1}] with x = α^r, r = 0..(n-k-1).
  So the full generator matrix G is:
        G = [ I_k ]
            [  V  ]      (shape n×k)

• Encoding (data → parity): P = V · D, where D is k×B (B = bytes per shard).
• Decoding (any k shards → data): Given a selection S of k distinct rows from G
  and the corresponding k shards C_sel (k×B), reconstruct D via:
        D = (S · G)^{-1} · C_sel
  We invert a k×k matrix once (over GF(256)), then apply it to all B columns.

API
---
- rs_encode(data_shards, params) -> List[bytes]           # returns parity shards
- rs_decode(shards_map, params) -> List[bytes]             # reconstruct data shards (0..k-1)
- RSCodec(params).encode(...) / .decode(...)

Notes
-----
- All shards must be exactly `params.share_bytes` long.
- `rs_decode` accepts a dict {shard_index: shard_bytes} with at least k entries,
  where shard_index ∈ [0, n). Indices 0..k-1 are data rows; k..n-1 are parity.
- Inversion failure is extremely unlikely with the chosen construction; if it
  happens, a different selection of k shards would succeed.

This implementation is intended for reference and test/bench usage; production
paths can replace it with a vectorized backend transparently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .params import ErasureParams

# =============================================================================
# GF(256) arithmetic (poly 0x11D, generator 0x02)
# =============================================================================

_PRIMITIVE_POLY = 0x11D
_ALPHA = 0x02
_GF_EXP: List[int] = [0] * 512  # exp table (repeat to avoid mod 255 on lookups)
_GF_LOG: List[int] = [0] * 256  # log table (log(0) unused)


def _gf_init() -> None:
    x = 1
    for i in range(255):
        _GF_EXP[i] = x
        _GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= _PRIMITIVE_POLY
    # duplicate the first 255 entries to avoid mod 255 in hot paths
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]


_gf_mul_table: List[bytes] = []  # 256 rows, each row is 256 pre-multiplied bytes


def _gf_build_mul_table() -> None:
    # Row 0 and 1 can be fast-paths; we still build all rows for uniformity.
    global _gf_mul_table
    rows: List[bytes] = []
    for a in range(256):
        row = bytearray(256)
        for b in range(256):
            row[b] = gf_mul(a, b)
        rows.append(bytes(row))
    _gf_mul_table = rows


def gf_add(a: int, b: int) -> int:
    return a ^ b


def gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def gf_inv(a: int) -> int:
    if a == 0:
        raise ZeroDivisionError("inverse of zero")
    return _GF_EXP[255 - _GF_LOG[a]]


def gf_pow(a: int, e: int) -> int:
    if e == 0:
        return 1
    if a == 0:
        return 0
    # a^e = exp(log(a) * e mod 255)
    return _GF_EXP[(_GF_LOG[a] * e) % 255]


def _vec_mul_scalar(buf: bytes, coeff: int) -> bytes:
    if coeff == 0:
        return bytes(len(buf))
    if coeff == 1:
        return bytes(buf)  # return a copy for consistency
    row = _gf_mul_table[coeff]
    # Element-wise multiply via lookup
    out = bytearray(len(buf))
    # local vars for speed
    rb = row
    for i, b in enumerate(buf):
        out[i] = rb[b]
    return bytes(out)


def _vec_xor_inplace(dst: bytearray, src: bytes) -> None:
    for i, b in enumerate(src):
        dst[i] ^= b


# Initialize tables at module import
_gf_init()
_gf_build_mul_table()


# =============================================================================
# Generator matrix helpers
# =============================================================================


def _generator_row(row_index: int, k: int) -> List[int]:
    """
    Return the `row_index`-th row of the generator matrix G (length k).
    • 0..k-1  -> identity rows
    • k..     -> Vandermonde rows with x = α^(row_index - k)
    """
    if row_index < k:
        row = [0] * k
        row[row_index] = 1
        return row
    r = row_index - k
    x = _GF_EXP[r]  # α^r
    # row = [1, x, x^2, ..., x^(k-1)]
    vals = [0] * k
    v = 1
    for i in range(k):
        vals[i] = v
        v = gf_mul(v, x)
    return vals


def _select_rows(indices: Sequence[int], k: int) -> List[List[int]]:
    return [_generator_row(i, k) for i in indices]


# =============================================================================
# Matrix ops over GF(256)
# =============================================================================


def _mat_identity(k: int) -> List[List[int]]:
    m = [[0] * k for _ in range(k)]
    for i in range(k):
        m[i][i] = 1
    return m


def _mat_inv(a: List[List[int]]) -> List[List[int]]:
    """
    Invert a k×k matrix over GF(256) using Gauss–Jordan elimination.
    Mutates a copy; leaves input `a` untouched.
    """
    k = len(a)
    # Make augmented [A | I]
    A = [row[:] for row in a]
    I = _mat_identity(k)

    for col in range(k):
        # Find pivot
        pivot = col
        while pivot < k and A[pivot][col] == 0:
            pivot += 1
        if pivot == k:
            raise ValueError("singular matrix in RS decode (bad shard selection)")
        # Swap rows if needed
        if pivot != col:
            A[col], A[pivot] = A[pivot], A[col]
            I[col], I[pivot] = I[pivot], I[col]
        # Normalize pivot row
        piv = A[col][col]
        inv_piv = gf_inv(piv)
        for j in range(k):
            A[col][j] = gf_mul(A[col][j], inv_piv)
            I[col][j] = gf_mul(I[col][j], inv_piv)
        # Eliminate other rows
        for r in range(k):
            if r == col:
                continue
            factor = A[r][col]
            if factor == 0:
                continue
            for j in range(k):
                A[r][j] = gf_add(A[r][j], gf_mul(factor, A[col][j]))
                I[r][j] = gf_add(I[r][j], gf_mul(factor, I[col][j]))
    # Now A == I; return the transformed identity, which is A^{-1}
    return I


def _mat_mul_bytes(mat: List[List[int]], rows: Sequence[bytes]) -> List[bytes]:
    """
    Multiply a (k×k) matrix by a (k×B) "byte matrix" where each row is a bytes object
    of length B. Returns a list of k byte rows.
    """
    k = len(mat)
    if len(rows) != k:
        raise ValueError("row count mismatch in mat×bytes multiply")
    if k == 0:
        return []
    B = len(rows[0])
    for r in rows:
        if len(r) != B:
            raise ValueError("inconsistent row lengths")

    out: List[bytes] = []
    for i in range(k):
        acc = bytearray(B)  # zero
        # acc = Σ_r mat[i][r] * rows[r]
        mrow = mat[i]
        for r in range(k):
            coeff = mrow[r]
            if coeff == 0:
                continue
            prod = _vec_mul_scalar(rows[r], coeff)
            _vec_xor_inplace(acc, prod)
        out.append(bytes(acc))
    return out


# =============================================================================
# Public API
# =============================================================================


def _rs_encode_from_shards(
    data_shards: Sequence[bytes], params: ErasureParams
) -> List[bytes]:
    """
    Core encoder operating on pre-split data shards.
    """
    k = params.data_shards
    n = params.total_shards
    B = params.share_bytes
    p = params.parity_shards

    if len(data_shards) != k:
        raise ValueError(f"expected {k} data shards, got {len(data_shards)}")
    for s in data_shards:
        if len(s) != B:
            raise ValueError("data shard has wrong length")

    if p == 0:
        return []

    # Build Vandermonde rows for parity (no need to materialize I_k)
    parity_rows = []
    for r in range(p):
        x = _GF_EXP[r]  # α^r
        coeffs = [0] * k
        v = 1
        for i in range(k):
            coeffs[i] = v
            v = gf_mul(v, x)
        parity_rows.append(coeffs)

    # Compute parity = row · data for each row
    parity_out: List[bytes] = []
    for row in parity_rows:
        acc = bytearray(B)
        for i in range(k):
            coeff = row[i]
            if coeff == 0:
                continue
            prod = _vec_mul_scalar(data_shards[i], coeff)
            _vec_xor_inplace(acc, prod)
        parity_out.append(bytes(acc))

    return parity_out


def rs_encode(
    data: Sequence[bytes] | bytes,
    params_or_k: ErasureParams | int,
    n: int | None = None,
    share_bytes: int | None = None,
) -> List[bytes]:
    """
    Flexible RS encoder supporting both shard-based and byte-buffer entrypoints.

    Accepted call forms:
        rs_encode(data_shards, params)
        rs_encode(data_bytes, k, n, share_bytes)
    """
    # Shard-based form: (Sequence[bytes], ErasureParams)
    if isinstance(params_or_k, ErasureParams):
        return _rs_encode_from_shards(data, params_or_k)  # type: ignore[arg-type]

    # Byte-buffer form: (bytes, k, n, share_bytes)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError(
            "rs_encode(data, k, n, share_bytes) expects a bytes-like data buffer"
        )
    if n is None or share_bytes is None:
        raise TypeError(
            "rs_encode(data, k, n, share_bytes) requires k, n, and share_bytes"
        )

    k = int(params_or_k)
    n_int = int(n)
    share = int(share_bytes)
    buf = bytes(data)
    expected = k * share
    if len(buf) != expected:
        raise ValueError(
            f"data length must be exactly k*share_bytes={expected}, got {len(buf)}"
        )
    params = ErasureParams(k, n_int, share)
    shards = [buf[i * share : (i + 1) * share] for i in range(k)]
    parity = _rs_encode_from_shards(shards, params)
    return shards + parity


def rs_decode(
    shards: Dict[int, bytes],
    params: ErasureParams,
) -> List[bytes]:
    """
    Reconstruct the original `k` data shards given a mapping {shard_index: bytes}
    with at least k entries. Shard indices are in [0, n). Returns data shards in
    canonical order index 0..k-1.

    Example:
        # Provide any k shards (data and/or parity) of equal length:
        data = rs_decode({0: d0, 2: d2, 5: p0, 7: p2, ...}, params)
    """
    k = params.data_shards
    n = params.total_shards
    B = params.share_bytes

    if len(shards) < k:
        raise ValueError("need at least k shards to decode")
    # Basic validation and normalize to a deterministic selection (first k indices)
    for idx, buf in shards.items():
        if not (0 <= idx < n):
            raise ValueError(f"shard index out of range: {idx}")
        if len(buf) != B:
            raise ValueError("shard length mismatch")

    sel = sorted(shards.keys())[:k]  # pick first k deterministically
    A = _select_rows(sel, k)  # k×k
    try:
        A_inv = _mat_inv(A)
    except ValueError as e:
        # Extremely unlikely; user can supply a different selection if this hits.
        raise

    # Build C_sel (k×B) rows matching `sel`
    rows = [shards[i] for i in sel]

    # D = A^{-1} · C_sel   => k rows, each length B
    data_rows = _mat_mul_bytes(A_inv, rows)

    # data_rows are in canonical order (row 0 = data shard 0, ... row k-1 = shard k-1)
    return data_rows


# -----------------------------------------------------------------------------
# Convenience wrappers for common call signatures used in tests
# -----------------------------------------------------------------------------


def encode(
    data: bytes, k: int, n: int, share_bytes: int
) -> List[bytes]:  # pragma: no cover - light wrapper
    return rs_encode(data, k, n, share_bytes)


def encode_bytes(
    data: bytes, k: int, n: int, share_bytes: int
) -> List[bytes]:  # pragma: no cover - alias
    return encode(data, k, n, share_bytes)


def encode_shards(
    data_shards: Sequence[bytes], k: int, n: int, share_bytes: int
) -> List[bytes]:
    params = ErasureParams(k, n, share_bytes)
    if len(data_shards) != k:
        raise ValueError(f"expected {k} data shards, got {len(data_shards)}")
    for s in data_shards:
        if len(s) != share_bytes:
            raise ValueError("data shard has wrong length")
    parity = _rs_encode_from_shards(data_shards, params)
    return list(data_shards) + parity


def reconstruct(
    shards: Sequence[Optional[bytes]], k: int, n: int, share_bytes: int
) -> List[bytes]:
    params = ErasureParams(k, n, share_bytes)
    provided: Dict[int, bytes] = {i: s for i, s in enumerate(shards) if s is not None}
    if len(provided) < k:
        raise ValueError("insufficient shards to reconstruct")
    data = rs_decode(provided, params)
    parity = _rs_encode_from_shards(data, params)
    full: List[bytes] = []
    for idx in range(n):
        if idx < k:
            full.append(data[idx])
        else:
            full.append(parity[idx - k])
    return full


def decode_shards(
    shards: Sequence[Optional[bytes]], k: int, n: int, share_bytes: int
) -> List[bytes]:  # pragma: no cover - thin wrapper
    return reconstruct(shards, k, n, share_bytes)


def decode(
    data_shards: Sequence[Optional[bytes]], k: int, n: int, share_bytes: int
) -> bytes:
    full = reconstruct(data_shards, k, n, share_bytes)
    return b"".join(full[:k])


def decode_bytes(
    data_shards: Sequence[Optional[bytes]], k: int, n: int, share_bytes: int
) -> bytes:  # pragma: no cover - alias
    return decode(data_shards, k, n, share_bytes)


def verify(shards: Sequence[bytes], k: int, n: int, share_bytes: int) -> bool:
    if len(shards) != n:
        return False
    for s in shards:
        if not isinstance(s, (bytes, bytearray)) or len(s) != share_bytes:
            return False
    data = list(shards[:k])
    params = ErasureParams(k, n, share_bytes)
    parity_expected = _rs_encode_from_shards(data, params)
    return list(shards[k:]) == parity_expected


# -----------------------------------------------------------------------------
# Convenience OO wrapper
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class RSCodec:
    params: ErasureParams

    @property
    def k(self) -> int:
        return self.params.data_shards

    @property
    def n(self) -> int:
        return self.params.total_shards

    @property
    def parity(self) -> int:
        return self.params.parity_shards

    def encode(self, data_shards: Sequence[bytes]) -> List[bytes]:
        return rs_encode(data_shards, self.params)

    def decode(self, shards: Dict[int, bytes]) -> List[bytes]:
        return rs_decode(shards, self.params)


__all__ = [
    "rs_encode",
    "rs_decode",
    "RSCodec",
]
