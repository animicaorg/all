"""
VDF proofs bridge — ingestion & verification

Consumes VDF proofs produced by miners/workers and, if valid, persists them to
the randomness store so the beacon can finalize the round.

Responsibilities
----------------
* Normalize incoming envelopes (bytes/hex/dicts) into a canonical message.
* Fetch the expected VDF input for a given round (x, N, t) from a provider.
* Verify the proof (Wesolowski-style) via the chain's verifier.
* Dedupe repeated submissions per round.
* Persist accepted proofs and optionally invoke a callback (e.g., to trigger
  beacon finalization or notify other subsystems).

This adapter is transport-agnostic: callers decide how messages arrive
(RPC, P2P, miner sidecar, etc.) and call `ingest_proof(...)`.

Example
-------
    bridge = VdfProofsBridge(
        input_provider=my_input_provider,
        store=my_store,
        on_accept=my_on_accept_callback,   # optional
    )

    # Envelope fields can be bytes or 0x-hex strings.
    await bridge.ingest_proof(
        round_id=42,
        y="0x...deadbeef...",
        pi="0x...cafe...",
        worker_id="rig-17"
    )

Notes
-----
* Verification is the source of truth; we never accept a proof unless the
  verifier says "true".
* We avoid hard-coding exact type shapes by supporting flexible providers for
  VDF inputs and by trying multiple verifier call signatures (see _verify()).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)

# ---- Prometheus metrics (no-op if not installed) ----
try:  # pragma: no cover - optional
    from prometheus_client import Counter, Histogram

    VDF_PROOFS_SEEN = Counter(
        "rand_vdf_proofs_seen_total",
        "Incoming VDF proofs",
        ["result"],  # ok|bad|dupe|store_err|verify_err|input_missing
    )
    VDF_VERIFY_SECONDS = Histogram(
        "rand_vdf_verify_seconds", "Time spent verifying VDF proofs"
    )
except Exception:  # pragma: no cover - fallback
    class _Noop:
        def labels(self, *_, **__):
            return self

        def inc(self, *_: Any, **__: Any) -> None:
            pass

        def observe(self, *_: Any, **__: Any) -> None:
            pass

    VDF_PROOFS_SEEN = _Noop()
    VDF_VERIFY_SECONDS = _Noop()

# ---- Types & light helpers ----
def _b2h(b: bytes) -> str:
    return "0x" + b.hex()

def _h2b(h: str | bytes) -> bytes:
    if isinstance(h, bytes):
        return h
    if not isinstance(h, str) or not h.startswith("0x"):
        raise ValueError("expected 0x-prefixed hex string")
    return bytes.fromhex(h[2:])

@dataclass
class VdfProofMsg:
    """Canonical envelope we operate on."""
    round: int
    y: bytes     # output
    pi: bytes    # proof
    worker_id: Optional[str] = None
    ts: int = 0  # arrival timestamp (filled automatically if zero)

# ---- Protocols for integration points ----
class VdfInput:
    """
    Minimal duck-typed container for the verifier input.
    Expected attributes (names are resolved flexibly in _unpack_input()):
      - x / input / seed : bytes
      - N / modulus      : bytes or int (modulus)
      - t / iterations   : int
    """
    pass  # purely descriptive; concrete type provided by the chain

class VdfInputProvider(Protocol):
    async def get_vdf_input(self, round_id: int) -> VdfInput | None: ...

class VdfStore(Protocol):
    async def has_vdf_proof(self, round_id: int) -> bool: ...
    async def write_vdf_proof(
        self,
        round_id: int,
        y: bytes,
        pi: bytes,
        verified: bool,
        worker_id: Optional[str],
        ts: int,
    ) -> None: ...

class OnAcceptCallback(Protocol):
    async def __call__(self, round_id: int, y: bytes, pi: bytes, worker_id: Optional[str]) -> None: ...

# ---- Verifier glue (accept multiple signatures) ----
_verify_fn = None
try:  # pragma: no cover - primary path
    from randomness.vdf.verifier import verify as _verify_fn  # type: ignore[attr-defined]
except Exception:  # pragma: no cover - dev fallback to module-level helper below
    _verify_fn = None

def _unpack_input(inp: Any) -> tuple[bytes, Any, int]:
    """
    Extract (x, N, t) from an arbitrary VDFInput-like object or dict.
    N may be an int or bytes (implementation-dependent); we pass it through.
    """
    # Objects or dicts: resolve generous attribute names
    get = (lambda *names: next((getattr(inp, n, None) for n in names if getattr(inp, n, None) is not None), None)) \
          if not isinstance(inp, dict) else \
          (lambda *names: next((inp.get(n) for n in names if inp.get(n) is not None), None))

    x = get("x", "input", "seed", "X")
    N = get("N", "modulus", "mod", "M")
    t = get("t", "iterations", "T")

    if x is None or N is None or t is None:
        raise ValueError("incomplete VDF input (need x/N/t)")

    if isinstance(x, str):
        x = _h2b(x)
    if isinstance(N, str) and N.startswith("0x"):
        N = int(N, 16)  # modulus commonly expressed as integer
    if isinstance(t, str):
        t = int(t, 10)
    if not isinstance(x, (bytes, bytearray)) or not isinstance(t, int):
        raise TypeError("unexpected types for VDF input")

    return bytes(x), N, int(t)

def _verify(inp: Any, y: bytes, pi: bytes) -> bool:
    """
    Try verifier in several calling conventions to be resilient to minor API differences.
    """
    x, N, t = _unpack_input(inp)

    # Try (input, y, pi) style — if the implementation wraps N and t in 'inp'.
    if _verify_fn is not None:
        # Attempt 1: verify(inp, y, pi)
        try:
            ok = _verify_fn(inp, y, pi)  # type: ignore[misc]
            if isinstance(ok, bool):
                return ok
        except TypeError:
            pass
        # Attempt 2: verify(x, y, pi, N, t)
        try:
            ok = _verify_fn(x, y, pi, N, t)  # type: ignore[misc]
            if isinstance(ok, bool):
                return ok
        except TypeError:
            pass
        # Attempt 3: verify({"x":..., "N":..., "t":...}, {"y":..., "pi":...})
        try:
            ok = _verify_fn({"x": x, "N": N, "t": t}, {"y": y, "pi": pi})  # type: ignore[misc]
            if isinstance(ok, bool):
                return ok
        except TypeError:
            pass

    # Final fallback: try wesolowski module directly if present.
    try:  # pragma: no cover - fallback path
        from randomness.vdf.wesolowski import verify as wes_verify  # type: ignore
        ok = wes_verify(x, y, pi, N, t)  # type: ignore[misc]
        if isinstance(ok, bool):
            return ok
    except Exception:
        pass

    raise RuntimeError("no viable VDF verifier signature available")

# ---- Bridge implementation ----
class VdfProofsBridge:
    """
    Accepts VDF proofs for rounds, verifies, dedupes, and persists.

    Parameters
    ----------
    input_provider : VdfInputProvider
        Source of the expected VDF input (x, N, t) for a round.
    store : VdfStore
        Persistence backend.
    on_accept : Optional[OnAcceptCallback]
        If provided, invoked after a proof is accepted and stored.
    """

    def __init__(
        self,
        *,
        input_provider: VdfInputProvider,
        store: VdfStore,
        on_accept: Optional[OnAcceptCallback] = None,
    ) -> None:
        self._inputs = input_provider
        self._store = store
        self._on_accept = on_accept
        self._lock = asyncio.Lock()  # serialize same-round writes

    async def ingest_proof(
        self,
        *,
        round_id: int,
        y: bytes | str,
        pi: bytes | str,
        worker_id: Optional[str] = None,
        ts: Optional[int] = None,
    ) -> bool:
        """
        Ingest a single proof envelope. Returns True iff accepted.

        Deduplication:
          - If the store already has a verified proof recorded for the round,
            this call is a no-op and returns True (idempotent).
        """
        # Normalize/validate
        try:
            msg = VdfProofMsg(
                round=round_id,
                y=_h2b(y),
                pi=_h2b(pi),
                worker_id=worker_id,
                ts=int(ts if ts is not None else time.time()),
            )
        except Exception as e:
            VDF_PROOFS_SEEN.labels(result="bad").inc()
            logger.debug("reject VDF proof (normalize): %s", e)
            return False

        async with self._lock:
            # Idempotent fast-path
            try:
                if await self._store.has_vdf_proof(msg.round):
                    VDF_PROOFS_SEEN.labels(result="dupe").inc()
                    return True
            except Exception as e:
                # Store errors are surfaced, but we still try to proceed to not miss a valid submission
                logger.warning("store.has_vdf_proof failed: %s", e)

            # Fetch expected input
            try:
                vdf_input = await self._inputs.get_vdf_input(msg.round)
                if vdf_input is None:
                    VDF_PROOFS_SEEN.labels(result="input_missing").inc()
                    logger.debug("no VDF input available for round=%s", msg.round)
                    return False
            except Exception as e:
                VDF_PROOFS_SEEN.labels(result="input_missing").inc()
                logger.debug("failed to fetch VDF input (round=%s): %s", msg.round, e)
                return False

            # Verify
            try:
                with _Timer(VDF_VERIFY_SECONDS):
                    ok = _verify(vdf_input, msg.y, msg.pi)
            except Exception as e:
                VDF_PROOFS_SEEN.labels(result="verify_err").inc()
                logger.debug("verification error (round=%s): %s", msg.round, e)
                return False

            if not ok:
                VDF_PROOFS_SEEN.labels(result="bad").inc()
                logger.debug("invalid VDF proof (round=%s)", msg.round)
                return False

            # Persist & callback
            try:
                await self._store.write_vdf_proof(
                    msg.round, msg.y, msg.pi, True, msg.worker_id, msg.ts
                )
            except Exception as e:
                VDF_PROOFS_SEEN.labels(result="store_err").inc()
                logger.warning("failed to persist VDF proof (round=%s): %s", msg.round, e)
                return False

        VDF_PROOFS_SEEN.labels(result="ok").inc()
        if self._on_accept:
            try:
                await self._on_accept(msg.round, msg.y, msg.pi, msg.worker_id)
            except Exception as e:
                # Non-fatal — acceptance stands, but we log the callback failure.
                logger.debug("on_accept callback failed (round=%s): %s", msg.round, e)
        return True

    async def ingest_batch(self, items: list[dict[str, Any] | VdfProofMsg]) -> tuple[int, int]:
        """
        Ingest multiple proofs; returns (accepted, rejected) counts.
        Each item may be a VdfProofMsg or a dict with keys: round, y, pi, worker_id, ts.
        """
        ok = 0
        bad = 0
        for it in items:
            if isinstance(it, VdfProofMsg):
                accepted = await self.ingest_proof(
                    round_id=it.round, y=it.y, pi=it.pi, worker_id=it.worker_id, ts=it.ts
                )
            else:
                accepted = await self.ingest_proof(
                    round_id=int(it.get("round")),  # type: ignore[arg-type]
                    y=it.get("y"),
                    pi=it.get("pi"),
                    worker_id=it.get("worker_id"),
                    ts=it.get("ts"),
                )
            ok += int(accepted)
            bad += int(not accepted)
        return ok, bad

# ---- Helpers ----
class _Timer:
    """Context manager to time a block and observe a histogram (if provided)."""
    def __init__(self, hist: Any) -> None:
        self._hist = hist
        self._t0 = 0.0
    def __enter__(self) -> None:
        self._t0 = time.perf_counter()
    def __exit__(self, *_: Any) -> None:
        dt = time.perf_counter() - self._t0
        try:
            self._hist.observe(dt)  # type: ignore[attr-defined]
        except Exception:
            pass

__all__ = [
    "VdfProofsBridge",
    "VdfInputProvider",
    "VdfStore",
    "OnAcceptCallback",
    "VdfProofMsg",
]
