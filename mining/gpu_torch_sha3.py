from __future__ import annotations

"""
GPU-capable solo Proof-of-Work scanner for Animica `miner mine-blocks`.

Why this exists
---------------
The hand-written CUDA/OpenCL/Metal kernels in ``mining/gpu_*.py`` compute the
*pool/PoIES share* digest ``sha3_256(signBytes || mixSeed || nonce_le8)`` with a
u-draw predicate.  Solo block work is validated by the node
(``rpc/methods/miner.py::miner_submit_work``) with a *different* rule::

    header = _header_from_cached_job(job, nonce=nonce_int)
    digest = header.hash()                         # sha3_256(CBOR(full header))
    accepted = int.from_bytes(digest, "big") <= block_target

i.e. the nonce is a CBOR ``uint`` embedded *inside* the canonical header map, not
a trailing little-endian field, so the existing kernels cannot accelerate it.

This module hashes the *real* solo preimage on the GPU using a batched Keccak-f
implemented entirely with PyTorch int64 tensor ops.  The same code runs
bit-identically on CPU and CUDA (integer math is deterministic across devices),
so its correctness is verifiable without a GPU and it dispatches to the GPU when
one is present.  Every candidate the scanner returns is additionally re-checked
against ``Header.hash()`` on the host, so a GPU bug can never surface an invalid
block — at worst it finds nothing and the caller falls back to the CPU miner.

Nonce band
----------
We constrain the searched nonce to ``[2**16, 2**32)`` so its canonical CBOR
encoding is always the fixed 5-byte form ``0x1a`` + 4 big-endian bytes.  That
keeps the header byte-layout constant except for 4 nonce bytes at a known
offset, which is what makes a clean batched kernel possible.  ~4.29e9 nonces per
template is ample for PoW search.
"""

import hashlib
from dataclasses import replace
from typing import Optional, Tuple

import torch

NONCE_BAND_LO = 1 << 16
NONCE_BAND_HI = 1 << 32  # exclusive

_RATE_BYTES = 136  # SHA3-256 rate r = 1088 bits
_RATE_LANES = _RATE_BYTES // 8  # 17

# Keccak-f[1600] rho rotation offsets, flattened as index = x + 5*y.
_RHO = [
    0, 1, 62, 28, 27,
    36, 44, 6, 55, 20,
    3, 10, 43, 25, 39,
    41, 45, 15, 21, 8,
    18, 2, 61, 56, 14,
]


def _s64(v: int) -> int:
    """Map an unsigned 64-bit value to its signed two's-complement int64."""
    return v - (1 << 64) if v >= (1 << 63) else v


_RC = [
    _s64(v) for v in (
        0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
        0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
        0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
        0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
        0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
        0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
        0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
        0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
    )
]


def _rotl(x: torch.Tensor, r: int) -> torch.Tensor:
    """Rotate-left a 64-bit lane tensor.  Uses signed int64 storage; the right
    half is masked so the arithmetic ``>>`` sign-extension is discarded."""
    if r == 0:
        return x
    left = x << r
    right = (x >> (64 - r)) & ((1 << r) - 1)
    return left | right


def _keccak_f1600(s: list[torch.Tensor]) -> list[torch.Tensor]:
    """In-place-ish Keccak-f[1600] over a list of 25 int64 lane tensors of shape
    [B].  Returns the mutated list.  Lane index = x + 5*y."""
    rc_dev = s[0].device
    for rnd in range(24):
        # theta
        c = [s[x] ^ s[x + 5] ^ s[x + 10] ^ s[x + 15] ^ s[x + 20] for x in range(5)]
        d = [c[(x + 4) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                s[x + 5 * y] = s[x + 5 * y] ^ d[x]
        # rho + pi
        b = [None] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl(s[x + 5 * y], _RHO[x + 5 * y])
        # chi
        for x in range(5):
            for y in range(5):
                s[x + 5 * y] = b[x + 5 * y] ^ (
                    (~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y]
                )
        # iota
        s[0] = s[0] ^ torch.tensor(_RC[rnd], dtype=torch.int64, device=rc_dev)
    return s


_SHIFTS8 = None


def _bytes_to_lanes(block: torch.Tensor) -> list[torch.Tensor]:
    """[B, 136] uint8 -> list of 17 int64 lane tensors (little-endian)."""
    B = block.shape[0]
    blk = block.view(B, _RATE_LANES, 8).to(torch.int64)
    lane = blk[:, :, 0].clone()
    for j in range(1, 8):
        lane = lane | (blk[:, :, j] << (8 * j))
    return [lane[:, i].contiguous() for i in range(_RATE_LANES)]


def sha3_256_batch(msgs: torch.Tensor) -> torch.Tensor:
    """Batched SHA3-256.

    ``msgs`` is a [B, L] uint8 tensor that is ALREADY padded (pad10*1 with the
    0x06 SHA3 domain byte) to a multiple of 136 bytes.  Returns [B, 32] uint8
    digest bytes (standard SHA3-256 output order).
    """
    B, L = msgs.shape
    assert L % _RATE_BYTES == 0, "message must be padded to a multiple of 136"
    dev = msgs.device
    state = [torch.zeros(B, dtype=torch.int64, device=dev) for _ in range(25)]
    nblocks = L // _RATE_BYTES
    for blk_i in range(nblocks):
        block = msgs[:, blk_i * _RATE_BYTES:(blk_i + 1) * _RATE_BYTES]
        lanes = _bytes_to_lanes(block)
        for i in range(_RATE_LANES):
            state[i] = state[i] ^ lanes[i]
        state = _keccak_f1600(state)
    # squeeze: first 32 bytes = lanes 0..3 little-endian
    out_cols = []
    for i in range(4):
        lane = state[i]
        for j in range(8):
            out_cols.append(((lane >> (8 * j)) & 0xFF).to(torch.uint8))
    return torch.stack(out_cols, dim=1)  # [B, 32]


def _pad101(msg: bytes) -> bytes:
    """SHA3 pad10*1 with the 0x06 domain suffix, to a multiple of 136."""
    out = bytearray(msg)
    out.append(0x06)
    while len(out) % _RATE_BYTES != 0:
        out.append(0x00)
    out[-1] |= 0x80
    return bytes(out)


def derive_prefix_suffix(header) -> Tuple[bytes, bytes, int]:
    """Return (prefix, suffix, offset) such that for any nonce n in the 4-byte
    band, ``sha3_256(prefix + n.to_bytes(4,'big') + suffix) == replace(header,
    nonce=n).hash()``.  Raises ValueError if the canonical CBOR layout does not
    match the expected fixed-width nonce (caller should fall back to CPU)."""
    na, nb = 0x11111111, 0x22222222
    ca = replace(header, nonce=na).to_cbor()
    cb = replace(header, nonce=nb).to_cbor()
    if len(ca) != len(cb):
        raise ValueError("nonce CBOR width is not fixed across the band")
    diff = [i for i in range(len(ca)) if ca[i] != cb[i]]
    if not diff or diff != list(range(diff[0], diff[0] + 4)):
        raise ValueError(f"unexpected nonce diff region: {diff}")
    off = diff[0]
    if ca[off - 1] != 0x1A or ca[off:off + 4] != na.to_bytes(4, "big"):
        raise ValueError("nonce is not a 4-byte big-endian CBOR uint")
    return ca[:off], ca[off + 4:], off


def solo_device_available(device: str) -> Optional[str]:
    """Return the concrete torch device string to use ('cuda'/'mps') if the
    requested GPU backend is usable, else None (caller uses the CPU miner)."""
    req = (device or "").strip().lower()
    if req in ("cuda", "rocm", "gpu", "auto"):
        if torch.cuda.is_available():
            return "cuda"
    if req in ("mps", "metal", "auto"):
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    return None


def scan_solo(
    header,
    target_int: int,
    *,
    start_nonce: int,
    iterations: int,
    device: str = "cuda",
    batch_size: int = 1 << 18,
    stats: Optional[dict] = None,
) -> Tuple[Optional[int], Optional[bytes]]:
    """Search nonces in ``[start_nonce, start_nonce+iterations)`` (clamped to the
    4-byte band) for one with ``Header(nonce).hash() <= target_int`` using the
    batched torch Keccak on ``device``.  Returns (nonce, digest) or (None, None).

    Every candidate is re-verified on the host with ``hashlib`` against the real
    target before being returned, so the result is always node-acceptable.
    """
    dev = torch.device(device)
    prefix, suffix, off = derive_prefix_suffix(header)
    base = bytearray(_pad101(prefix + b"\x00\x00\x00\x00" + suffix))
    L = len(base)
    base_t = torch.tensor(list(base), dtype=torch.uint8, device=dev)
    target_be = target_int.to_bytes(32, "big")
    target_np = torch.tensor(list(target_be), dtype=torch.int16)  # cpu compare

    lo = max(int(start_nonce), NONCE_BAND_LO)
    hi = min(int(start_nonce) + int(iterations), NONCE_BAND_HI)
    n = lo
    scanned = 0
    while n < hi:
        b = min(batch_size, hi - n)
        nonces = torch.arange(n, n + b, dtype=torch.int64, device=dev)
        msgs = base_t.unsqueeze(0).repeat(b, 1)
        be = torch.stack([
            ((nonces >> 24) & 0xFF),
            ((nonces >> 16) & 0xFF),
            ((nonces >> 8) & 0xFF),
            (nonces & 0xFF),
        ], dim=1).to(torch.uint8)
        msgs[:, off:off + 4] = be
        digests = sha3_256_batch(msgs)  # [b, 32]
        # Lexicographic (big-endian) compare digests <= target.
        d = digests.to(torch.int16).cpu()
        diff = d - target_np  # [b, 32]
        nz = diff != 0
        any_nz = nz.any(dim=1)
        first_idx = torch.argmax(nz.to(torch.int8), dim=1)
        firstval = diff[torch.arange(d.shape[0]), first_idx]
        le = (~any_nz) | (firstval < 0)
        hit_rows = torch.nonzero(le, as_tuple=False).flatten().tolist()
        scanned += b
        for row in hit_rows:
            cand = n + row
            # Host re-verification against the canonical node digest.
            digest = replace(header, nonce=cand).hash()
            if int.from_bytes(digest, "big") <= target_int:
                if stats is not None:
                    stats["hashes"] = stats.get("hashes", 0) + scanned
                return cand, digest
        n += b
    if stats is not None:
        stats["hashes"] = stats.get("hashes", 0) + scanned
    return None, None


def _selftest() -> None:  # pragma: no cover - invoked via __main__
    import secrets
    import sys
    sys.path.insert(0, "/root/animica")
    from core.types.header import Header

    dev = "cpu"
    # 1) Keccak bit-exactness vs hashlib for random-length messages.
    n_ok = 0
    for _ in range(300):
        L = secrets.randbelow(600) + 1
        m = secrets.token_bytes(L)
        padded = _pad101(m)
        t = torch.tensor(list(padded), dtype=torch.uint8, device=dev).unsqueeze(0)
        got = bytes(sha3_256_batch(t)[0].tolist())
        exp = hashlib.sha3_256(m).digest()
        assert got == exp, (L, got.hex(), exp.hex())
        n_ok += 1
    print(f"[1] keccak vs hashlib: {n_ok} random messages OK")

    # 2) Full splice digest == Header(nonce).hash() via the batch path.
    def mkhdr():
        tok = secrets.token_bytes
        return Header(
            v=1, chainId=1, height=987654, parentHash=tok(32),
            timestamp=1_700_123_456, stateRoot=tok(32), txsRoot=tok(32),
            receiptsRoot=tok(32), proofsRoot=tok(32), daRoot=tok(32),
            mixSeed=tok(32), poiesPolicyRoot=tok(32), pqAlgPolicyRoot=tok(32),
            thetaMicro=16_000_000, workType=0, nonce=0, extra=b"selftest",
        )

    hdr = mkhdr()
    prefix, suffix, off = derive_prefix_suffix(hdr)
    base = _pad101(prefix + b"\x00\x00\x00\x00" + suffix)
    n_ok = 0
    for _ in range(400):
        nn = secrets.randbelow(NONCE_BAND_HI - NONCE_BAND_LO) + NONCE_BAND_LO
        m = bytearray(base)
        m[off:off + 4] = nn.to_bytes(4, "big")
        t = torch.tensor(list(m), dtype=torch.uint8).unsqueeze(0)
        got = bytes(sha3_256_batch(t)[0].tolist())
        exp = replace(hdr, nonce=nn).hash()
        assert got == exp, (nn, got.hex(), exp.hex())
        n_ok += 1
    print(f"[2] splice digest == Header.hash(): {n_ok} nonces OK")

    # 3) scan_solo finds a planted easy solution and never returns a bad one.
    n0 = NONCE_BAND_LO + 5000
    planted = int.from_bytes(replace(hdr, nonce=n0).hash(), "big")
    nonce, digest = scan_solo(
        hdr, planted, start_nonce=NONCE_BAND_LO, iterations=20000,
        device="cpu", batch_size=4096,
    )
    assert nonce is not None, "scan failed to find planted solution"
    assert int.from_bytes(digest, "big") <= planted
    assert digest == replace(hdr, nonce=nonce).hash()
    print(f"[3] scan_solo found nonce={nonce} (<= planted target) OK")
    print("ALL SELFTESTS PASSED")


if __name__ == "__main__":
    _selftest()
