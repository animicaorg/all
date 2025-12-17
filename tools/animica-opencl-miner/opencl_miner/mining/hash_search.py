from __future__ import annotations

"""
CPU hash scanning loop for HashShare proofs.

Design goals
------------
- Pure-Python, portable, and correct by construction.
- Fast enough for devnet/local mining; optionally benefits from PyPy or pypy3.
- Deterministic domain binding: the *prefix* must already match the nonce/mixSeed
  domain rules defined in `mining/nonce_domain.py` & `proofs/utils/keccak_stream.py`.
- Branchless-ish hot path, low-allocation loop, prehashed prefix for speed.
- No floating-point in the hot predicate: we compare a 256-bit integer digest
  against a precomputed 256-bit target derived from the µ-nats threshold.

Usage sketch
------------
    from mining.hash_search import HashScanner, FoundShare, micro_threshold_to_target256

    scanner = HashScanner(algo="sha3_256")
    # prefix = canonical header SignBytes up to (but excluding) 8-byte LE nonce
    for share in scanner.scan(prefix, t_share_micro, start_nonce=0, max_nonce=1<<32):
        print("found", share)

Interfaces
----------
- `scan(...)` is an iterator; optionally pass `on_found` callback to push into a queue.
- `scan_batch(...)` returns a list of shares within [nonce, nonce+count).
- Utility helpers:
    * micro_threshold_to_target256(t_micro)
    * digest_to_int256(digest)
    * h_micro_from_digest(digest)
    * pr_share_from_threshold(t_micro)  # e^{-t}
"""

import hashlib
import math
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Optional

# Constants
UINT256_MAX = (1 << 256) - 1
MICRO = 1_000_000


@dataclass(frozen=True)
class FoundShare:
    """Result of a successful share trial."""

    nonce: int
    digest: bytes  # sha3_256(header_prefix || nonce_le8)
    h_micro: int  # H(u) in µ-nats (=-ln(u) * 1e6), computed for telemetry
    target256: int  # 256-bit target used for predicate
    theta_micro: Optional[int]  # Optional Θ used to compute d_ratio
    d_ratio: Optional[float]  # H/Θ if theta provided; else None
    ts: float  # discovery timestamp (monotonic_seconds)


# ─────────────────────────────────────────────────────────────────────────────
# Numeric helpers (µ-nats thresholds ↔ 256-bit targets)
# ─────────────────────────────────────────────────────────────────────────────


def micro_to_nats(micro: int) -> float:
    return micro / MICRO


def nats_to_micro(n: float) -> int:
    return int(round(n * MICRO))


def pr_share_from_threshold(t_micro: int) -> float:
    """Return p = e^{-t} (probability that u ≤ p) for threshold t (µ-nats)."""
    return math.exp(-micro_to_nats(t_micro))


def micro_threshold_to_target256(t_micro: int) -> int:
    """
    Convert a µ-nats threshold t into a 256-bit integer target T such that:
      accept(digest)  iff  int256(digest) ≤ T

    We set:
        u* = e^{-t}  and  X ~ Uniform{0..2^256-1}
        accept iff X / 2^256 ≤ u*  ⇒  X ≤ floor(u* * 2^256) - 1

    The -1 is optional (boundary); we keep it inside floor by scaling with (2^256 - 1).
    """
    p = pr_share_from_threshold(t_micro)  # in (0, 1]
    if p >= 1.0:
        return UINT256_MAX
    if p <= 0.0:
        return 0
    return int(p * UINT256_MAX)


def digest_to_int256(digest: bytes) -> int:
    """Interpret a 32-byte digest as a big-endian unsigned 256-bit integer."""
    if len(digest) != 32:
        raise ValueError("digest must be 32 bytes for int256 conversion")
    return int.from_bytes(digest, "big", signed=False)


def h_micro_from_digest(digest: bytes) -> int:
    """
    Compute H(u) = -ln(u) in µ-nats from a digest, with a tiny epsilon to avoid ln(0).
    Let X = int256(digest); u ≈ (X + 0.5) / 2^256  (center of bin), improves edge stability.
    """
    x = digest_to_int256(digest)
    u = (x + 0.5) / (UINT256_MAX + 1.0)  # in [~0, 1)
    # Guard against pathological zero
    if u <= 0.0:
        return nats_to_micro(float("inf"))
    return nats_to_micro(-math.log(u))


# ─────────────────────────────────────────────────────────────────────────────
# Hashing utilities
# ─────────────────────────────────────────────────────────────────────────────


def _make_hasher(algo: str, prefix: bytes) -> hashlib._hashlib.HASH:
    """
    Create a new hashlib object initialized with `prefix`.
    Only sha3_256 is guaranteed present in stdlib. Optionally 'blake3' if installed.
    """
    if algo == "sha3_256":
        h = hashlib.sha3_256()
        h.update(prefix)
        return h
    elif algo == "blake2s":  # portable fast hash (32-bytes)
        h = hashlib.blake2s()
        h.update(prefix)
        return h
    else:
        # Try dynamic
        try:
            h = hashlib.new(algo)
            h.update(prefix)
            return h
        except Exception as e:
            raise ValueError(f"Unsupported hash algo '{algo}': {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Scanner
# ─────────────────────────────────────────────────────────────────────────────


class HashScanner:
    """
    CPU scanner that iterates nonces, hashes (prefix || nonce_le8), and checks
    the digest against a target derived from a µ-nats threshold.

    Performance notes:
      - We prehash `prefix` once and then use hasher.copy() per trial.
      - We avoid allocating a new nonce buffer each time (struct.pack_into).
      - No floating point in the hot predicate: pure int compare vs target256.
    """

    def __init__(self, *, algo: str = "sha3_256") -> None:
        self.algo = algo

    # Generator mode
    def scan(
        self,
        prefix: bytes,
        t_share_micro: int,
        *,
        start_nonce: int = 0,
        max_nonce: Optional[int] = None,
        theta_micro: Optional[int] = None,
        on_found: Optional[Callable[[FoundShare], None]] = None,
        stop_event: Optional[threading.Event] = None,
        report_every: int = 1 << 16,
    ) -> Iterator[FoundShare]:
        """
        Iterate over nonces and yield FoundShare whenever digest <= target.
        If `on_found` is provided, the share is pushed there and not yielded.

        Args:
          prefix: bytes to be hashed *before* appending the 8-byte LE nonce.
          t_share_micro: share threshold in µ-nats.
          start_nonce: starting 64-bit nonce (wraps at 2^64).
          max_nonce: if set, scan at most this many nonces (window size).
          theta_micro: optional Θ for computing d_ratio in result.
          on_found: callback(FoundShare) for push-mode; if None, generator yields shares.
          stop_event: external signal to stop scanning promptly.
          report_every: log-like heartbeat interval (#trials) for internal rate calc.

        Yields:
          FoundShare objects if on_found is None, else nothing.
        """
        T = micro_threshold_to_target256(t_share_micro)
        hasher_base = _make_hasher(self.algo, prefix)

        nonce = start_nonce & 0xFFFFFFFFFFFFFFFF
        trials_done = 0
        t0 = time.perf_counter()

        # stack-local bindings for speed
        _UINT64_MASK = 0xFFFFFFFFFFFFFFFF
        _digest_to_int256 = digest_to_int256
        _h_micro = h_micro_from_digest
        _time = time.perf_counter
        _on_found = on_found
        _theta = theta_micro

        limit = None if max_nonce is None else start_nonce + max_nonce

        while True:
            if stop_event is not None and stop_event.is_set():
                break
            if limit is not None and nonce >= limit:
                break

            # Compute digest(prefix || nonce_le8) with prehashed prefix
            h = hasher_base.copy()
            nonce_bytes = struct.pack("<Q", nonce)
            h.update(nonce_bytes)
            digest = h.digest()

            # Hot-path predicate: int256(digest) ≤ T
            if _digest_to_int256(digest) <= T:
                hµ = _h_micro(digest)
                dR = (
                    (hµ / float(_theta))
                    if (_theta is not None and _theta > 0)
                    else None
                )
                share = FoundShare(
                    nonce=nonce,
                    digest=digest,
                    h_micro=hµ,
                    target256=T,
                    theta_micro=_theta,
                    d_ratio=dR,
                    ts=_time(),
                )
                if _on_found is not None:
                    _on_found(share)
                else:
                    yield share

            # Next nonce
            nonce = (nonce + 1) & _UINT64_MASK
            trials_done += 1

            # Heartbeat (optional; currently no logging to avoid noisy stdout)
            if report_every and (trials_done % report_every == 0):
                # Consumers may add their own logging around scan() if desired.
                # We intentionally avoid printing here to keep miner quiet.
                pass

        # End of scan — return from generator cleanly
        return

    # Batch convenience (non-generator)
    def scan_batch(
        self,
        prefix: bytes,
        t_share_micro: int,
        *,
        nonce_start: int,
        nonce_count: int,
        theta_micro: Optional[int] = None,
    ) -> list[FoundShare]:
        """
        Scan a fixed nonce window and return all shares found.
        Suitable for tests/benches or when a caller fans out ranges across threads.
        """
        results: list[FoundShare] = []

        def _collect(s: FoundShare) -> None:
            results.append(s)

        # Reuse the generator with an on_found callback
        stop_evt = threading.Event()
        for _ in self.scan(
            prefix,
            t_share_micro,
            start_nonce=nonce_start,
            max_nonce=nonce_count,
            theta_micro=theta_micro,
            on_found=_collect,
            stop_event=stop_evt,
            report_every=0,
        ):
            # We should not reach here because on_found consumes results
            pass

        return results


# ─────────────────────────────────────────────────────────────────────────────
# Self-test / smoke
# ─────────────────────────────────────────────────────────────────────────────


def _smoke() -> None:  # pragma: no cover
    import os

    # Fake header prefix (random), emulate domain-correct SignBytes w/o the 8B nonce.
    prefix = b"animica:header:signbytes:v1:" + os.urandom(48)

    # Choose a very low threshold so we actually find shares in a tiny window.
    # e^{-t} = p. Let p = 2^{-20} ⇒ t ≈ ln(2^20) ≈ 13.8629 nats ⇒ 13_862_944 µ-nats
    t_nats = 20.0 * math.log(2.0)
    t_micro = nats_to_micro(t_nats)

    theta_micro = nats_to_micro(24.0 * math.log(2.0))  # arbitrary Θ for d_ratio display

    scanner = HashScanner()
    shares = scanner.scan_batch(
        prefix, t_micro, nonce_start=0, nonce_count=2_000_000, theta_micro=theta_micro
    )
    print(
        f"Found {len(shares)} shares in 2M trials (expected ≈ {2_000_000 * pr_share_from_threshold(t_micro):.1f})"
    )
    if shares:
        s0 = shares[0]
        print(
            "Example share:",
            dict(
                nonce=s0.nonce,
                h_micro=s0.h_micro,
                d_ratio=s0.d_ratio,
                digest=s0.digest.hex()[:16] + "…",
            ),
        )


if __name__ == "__main__":  # pragma: no cover
    _smoke()
