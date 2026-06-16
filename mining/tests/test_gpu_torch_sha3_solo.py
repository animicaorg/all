"""Correctness tests for the torch-backed solo PoW scanner.

These guard the consensus-critical invariant that the GPU scanner's digest
equals ``Header.hash()`` — exactly what ``rpc/methods/miner.py::miner_submit_work``
recomputes and checks against the block target. The torch Keccak uses only
integer ops, so passing on CPU guarantees identical results on CUDA/MPS.
"""

import hashlib
import secrets
from dataclasses import replace

import pytest

torch = pytest.importorskip("torch")

from core.types.header import Header  # noqa: E402
from mining.gpu_torch_sha3 import (  # noqa: E402
    NONCE_BAND_HI,
    NONCE_BAND_LO,
    _pad101,
    derive_prefix_suffix,
    scan_solo,
    sha3_256_batch,
)


def _mkhdr(work_type=0, extra=b"", height=12345):
    tok = secrets.token_bytes
    return Header(
        v=1, chainId=1, height=height, parentHash=tok(32), timestamp=1_700_000_000,
        stateRoot=tok(32), txsRoot=tok(32), receiptsRoot=tok(32), proofsRoot=tok(32),
        daRoot=tok(32), mixSeed=tok(32), poiesPolicyRoot=tok(32), pqAlgPolicyRoot=tok(32),
        thetaMicro=16_000_000, workType=work_type, nonce=0, extra=extra,
    )


@pytest.mark.parametrize("length", [1, 31, 135, 136, 137, 200, 531, 600])
def test_keccak_matches_hashlib(length):
    msg = secrets.token_bytes(length)
    t = torch.tensor(list(_pad101(msg)), dtype=torch.uint8).unsqueeze(0)
    got = bytes(sha3_256_batch(t)[0].tolist())
    assert got == hashlib.sha3_256(msg).digest()


@pytest.mark.parametrize("work_type,extra", [(0, b""), (7, b"animica"), (0, b"x" * 40)])
def test_splice_digest_equals_header_hash(work_type, extra):
    hdr = _mkhdr(work_type=work_type, extra=extra)
    prefix, suffix, off = derive_prefix_suffix(hdr)
    base = _pad101(prefix + b"\x00\x00\x00\x00" + suffix)
    for _ in range(200):
        n = secrets.randbelow(NONCE_BAND_HI - NONCE_BAND_LO) + NONCE_BAND_LO
        m = bytearray(base)
        m[off:off + 4] = n.to_bytes(4, "big")
        t = torch.tensor(list(m), dtype=torch.uint8).unsqueeze(0)
        got = bytes(sha3_256_batch(t)[0].tolist())
        assert got == replace(hdr, nonce=n).hash()


def test_scan_solo_returns_node_acceptable_nonce():
    hdr = _mkhdr()
    # Easy target: every nonce qualifies, so the scan returns its first
    # candidate, which must still re-verify against the canonical digest.
    nonce, digest = scan_solo(
        hdr, (1 << 256) - 1, start_nonce=NONCE_BAND_LO, iterations=4096,
        device="cpu", batch_size=4096,
    )
    assert nonce is not None
    assert NONCE_BAND_LO <= nonce < NONCE_BAND_HI
    assert digest == replace(hdr, nonce=nonce).hash()


def test_scan_solo_hard_target_filters():
    hdr = _mkhdr()
    # Top digest byte must be zero (~1/256 of nonces); a 200k window finds one.
    hard = int.from_bytes(bytes([0x00]) + b"\xff" * 31, "big")
    nonce, digest = scan_solo(
        hdr, hard, start_nonce=NONCE_BAND_LO, iterations=200_000,
        device="cpu", batch_size=8192,
    )
    assert nonce is not None
    assert digest[0] == 0x00
    assert int.from_bytes(digest, "big") <= hard
    assert digest == replace(hdr, nonce=nonce).hash()
