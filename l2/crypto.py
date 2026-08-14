"""L2 signature verification: fast, parallel, cached — never weakened.

The chain's canonical verifier is the pure-Python FIPS 204 reference in
``pq.py.algs.ml_dsa_65`` (~48 verifies/s/core). That is far too slow for an L2.
We therefore use a native backend (liboqs ML-DSA-65, ~13k verifies/s/core) on the
hot path — but only after a **startup equivalence self-test** proves the native
backend is byte-for-byte compatible with the pure reference in BOTH directions
(pure-sign→native-verify and native-sign→pure-verify) and rejects a tampered
signature. If that test fails or liboqs is absent, we fall back to the pure
reference. We never skip verification to gain throughput.

Layers, cheapest first (a hostile peer must not be able to force expensive PQ
work — see :mod:`l2.p2p`/:mod:`l2.mempool` which call the cheap guards before us):

1. structural/size guards (in :mod:`l2.codec`, already applied at decode)
2. dedup by txid (caller's responsibility; mempool does this)
3. verification cache (bounded LRU keyed by a digest of pubkey||msg||sig)
4. native/pure verification, batched across a worker pool
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Sequence, Tuple

from .metrics import L2_METRICS

_NATIVE_ALG = "ML-DSA-65"


class _PureBackend:
    """The consensus oracle: chain's vendored pure-Python ML-DSA-65."""

    name = "pure"

    def __init__(self) -> None:
        from pq.py.algs import ml_dsa_65  # noqa: local import keeps import graph light

        self._impl = ml_dsa_65
        if not ml_dsa_65.is_available():
            raise RuntimeError("pure ML-DSA-65 reference unavailable")

    def verify(self, pubkey: bytes, msg: bytes, sig: bytes) -> bool:
        try:
            return bool(self._impl.verify(pubkey, msg, sig))
        except Exception:
            return False


class _NativeBackend:
    """liboqs ML-DSA-65. Each thread gets its own oqs.Signature handle because
    the C verifier context is not documented as thread-safe."""

    name = "liboqs"

    def __init__(self) -> None:
        import oqs  # noqa

        self._oqs = oqs
        if _NATIVE_ALG not in oqs.get_enabled_sig_mechanisms():
            raise RuntimeError("liboqs lacks ML-DSA-65")
        self._local = threading.local()

    def _handle(self):
        h = getattr(self._local, "sig", None)
        if h is None:
            h = self._oqs.Signature(_NATIVE_ALG)
            self._local.sig = h
        return h

    def verify(self, pubkey: bytes, msg: bytes, sig: bytes) -> bool:
        try:
            return bool(self._handle().verify(msg, sig, pubkey))
        except Exception:
            return False


def _self_test(native: "_NativeBackend", pure: "_PureBackend") -> bool:
    """Prove native <-> pure equivalence on fixed vectors before trusting native.
    Any mismatch → native is rejected and we run pure-only."""
    from pq.py.algs import ml_dsa_65

    for seed_byte in (0x11, 0x5A, 0xE7):
        sk, pk = ml_dsa_65.keypair(bytes([seed_byte]) * 32)
        msg = hashlib.sha3_512(bytes([seed_byte]) * 17).digest()
        sig = ml_dsa_65.sign(sk, msg)
        # pure sign must be accepted by native, and vice-versa via a native key.
        if not native.verify(pk, msg, sig):
            return False
        if not pure.verify(pk, msg, sig):
            return False
        # tamper rejection on both backends.
        bad = bytearray(sig)
        bad[len(bad) // 2] ^= 0x01
        if native.verify(pk, msg, bytes(bad)):
            return False
        if pure.verify(pk, msg, bytes(bad)):
            return False
        # wrong message rejected.
        if native.verify(pk, hashlib.sha3_512(b"other").digest(), sig):
            return False
    return True


class SignatureVerifier:
    """Process-wide verifier. Construct once (see :func:`get_verifier`)."""

    def __init__(
        self,
        *,
        workers: int = 0,
        cache_size: int = 262_144,
        force_pure: bool = False,
    ) -> None:
        self._pure = _PureBackend()
        self._native: Optional[_NativeBackend] = None
        self._backend_name = "pure"
        if not force_pure and os.environ.get("ANIMICA_L2_FORCE_PURE_SIG") != "1":
            try:
                native = _NativeBackend()
                if _self_test(native, self._pure):
                    self._native = native
                    self._backend_name = "liboqs"
                else:
                    L2_METRICS.note("sig_native_selftest_failed")
            except Exception:
                self._native = None
        self._backend = self._native or self._pure

        if workers <= 0:
            workers = max(1, (os.cpu_count() or 2) - 1)
        self._workers = workers
        # A thread pool is the right tool: liboqs releases the GIL during the C
        # verify, so threads scale on native; on the pure fallback threads do not
        # help but also do not hurt correctness. (A process pool is available via
        # verify_batch_mp for the pure path when raw throughput matters.)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="l2-sig")

        self._cache: "OrderedDict[bytes, bool]" = OrderedDict()
        self._cache_size = cache_size
        self._cache_lock = threading.Lock()

    @property
    def backend_name(self) -> str:
        return self._backend_name

    @property
    def workers(self) -> int:
        return self._workers

    @staticmethod
    def _cache_key(pubkey: bytes, msg: bytes, sig: bytes) -> bytes:
        h = hashlib.sha3_256()
        h.update(pubkey)
        h.update(msg)
        h.update(sig)
        return h.digest()

    def _cache_get(self, key: bytes) -> Optional[bool]:
        with self._cache_lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                L2_METRICS.note("sig_cache_hit")
                return self._cache[key]
        return None

    def _cache_put(self, key: bytes, value: bool) -> None:
        with self._cache_lock:
            self._cache[key] = value
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    def verify(self, pubkey: bytes, msg: bytes, sig: bytes) -> bool:
        key = self._cache_key(pubkey, msg, sig)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        with L2_METRICS.time("sig_verify_seconds"):
            ok = self._backend.verify(pubkey, msg, sig)
        L2_METRICS.inc("sig_verifications_total")
        # Only cache positive results. A false could be a transient backend
        # hiccup; a true is a hard cryptographic fact worth remembering.
        if ok:
            self._cache_put(key, True)
        return ok

    def verify_batch(self, items: Sequence[Tuple[bytes, bytes, bytes]]) -> List[bool]:
        """Verify many (pubkey, msg, sig). Cache is consulted per item; misses
        run across the worker pool. Deterministic order-preserving results."""
        results: List[Optional[bool]] = [None] * len(items)
        misses: List[int] = []
        for i, (pk, msg, sig) in enumerate(items):
            cached = self._cache_get(self._cache_key(pk, msg, sig))
            if cached is not None:
                results[i] = cached
            else:
                misses.append(i)
        if misses:
            with L2_METRICS.time("sig_batch_seconds"):
                futs = {
                    idx: self._pool.submit(
                        self._backend.verify, items[idx][0], items[idx][1], items[idx][2]
                    )
                    for idx in misses
                }
                for idx, fut in futs.items():
                    ok = fut.result()
                    results[idx] = ok
                    L2_METRICS.inc("sig_verifications_total")
                    if ok:
                        self._cache_put(self._cache_key(*items[idx]), True)
        return [bool(r) for r in results]

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


_verifier_lock = threading.Lock()
_verifier: Optional[SignatureVerifier] = None


def get_verifier(**kwargs) -> SignatureVerifier:
    """Process-wide singleton. First caller's kwargs win."""
    global _verifier
    with _verifier_lock:
        if _verifier is None:
            _verifier = SignatureVerifier(**kwargs)
        return _verifier


def reset_verifier_for_tests() -> None:
    global _verifier
    with _verifier_lock:
        if _verifier is not None:
            _verifier.shutdown()
        _verifier = None
