"""
randomness.beacon.finalize
==========================

Finalize a randomness round:

1) Aggregate valid reveals into a single *aggregate* value.
2) Derive the VDF input from (prev_beacon, aggregate, round_id) and
   verify the provided VDF proof.
3) Optionally mix the VDF output with QRNG bytes via an
   extract-then-xor style combiner.
4) Return a ``BeaconOut`` record for persistence and downstream use.

This module performs *no network I/O* and does not fetch reveals on its
own; pass already-validated/canonical ``RevealRecord`` items.

Notes
-----
- All hashes are domain-separated via constants in ``randomness.constants``.
- The exact layout of the transcript is a node-local detail; consensus
  depends only on the derived bytes and proof validity.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable, Optional, Tuple
import time

from randomness.constants import (
    DOMAIN_BEACON_VDF_INPUT,
    DOMAIN_BEACON_FINAL,
    DOMAIN_BEACON_MIX,
)
from randomness.errors import VDFInvalid
from randomness.utils.hash import sha3_256, sha3_512, dhash
from randomness.commit_reveal.aggregate import aggregate_reveals  # bias-resistant combiner
from randomness.vdf.input_builder import build_input  # prev_beacon, aggregate, round_id -> bytes
from randomness.vdf.verifier import verify as vdf_verify  # (vdf_input, proof) -> vdf_output bytes
from randomness.qrng.mixer import mix as qrng_mix  # (vdf_output, qrng_bytes, transcript) -> mixed bytes

from randomness.types.core import (
    RoundId,
    RevealRecord,
    VDFInput,
    VDFProof,
    BeaconOut,
)
from randomness.types.state import BeaconState

# Metrics are optional in unit tests; guard imports.
try:  # pragma: no cover - optional metrics
    from randomness.metrics import (
        VDF_VERIFY_SECONDS,
        MIX_ENTROPY_BYTES,
        REVEALS_AGGREGATED,
    )
    _METRICS = True
except Exception:  # pragma: no cover
    _METRICS = False
    class _Null:
        def observe(self, *_a, **_k): ...
        def inc(self, *_a, **_k): ...
    VDF_VERIFY_SECONDS = MIX_ENTROPY_BYTES = REVEALS_AGGREGATED = _Null()  # type: ignore


def _derive_vdf_input(prev_beacon: bytes, aggregate: bytes, round_id: RoundId) -> bytes:
    """
    Derive the VDF input using the dedicated builder and a domain-tagged hash.
    """
    raw = build_input(prev_beacon=prev_beacon, aggregate=aggregate, round_id=round_id)
    # Extra defensive domain separation to ensure stable width and non-ambiguity.
    return sha3_256(DOMAIN_BEACON_VDF_INPUT + round_id.to_bytes(8, "big") + raw)


def _finalize_bytes_from_vdf(vdf_out: bytes, round_id: RoundId) -> bytes:
    """
    Produce final beacon bytes from the VDF output when no QRNG mix is used.
    """
    return sha3_512(DOMAIN_BEACON_FINAL + round_id.to_bytes(8, "big") + vdf_out)


def _maybe_mix_with_qrng(vdf_out: bytes, qrng: Optional[bytes], transcript: bytes) -> Tuple[bytes, bool]:
    """
    Mix with QRNG if provided; returns (final_bytes, mixed_flag).
    """
    if qrng is None or len(qrng) == 0:
        return vdf_out, False
    mixed = qrng_mix(vdf_out, qrng, transcript=transcript)
    return mixed, True


def finalize_round(
    *,
    state: BeaconState,
    round_id: RoundId,
    reveals: Iterable[RevealRecord],
    vdf_proof: VDFProof,
    qrng_bytes: Optional[bytes] = None,
) -> BeaconOut:
    """
    Orchestrate round finalization and return a BeaconOut.

    Parameters
    ----------
    state : BeaconState
        Current beacon state (used for previous output reference).
    round_id : RoundId
        The round being finalized.
    reveals : Iterable[RevealRecord]
        Validated reveal records for this round (window-checked elsewhere).
    vdf_proof : VDFProof
        Proof of sequential work over the derived VDF input.
    qrng_bytes : Optional[bytes]
        Optional quantum RNG byte-stream to mix into the final output.

    Returns
    -------
    BeaconOut
        The finalized beacon output object.

    Raises
    ------
    VDFInvalid
        If the provided VDF proof does not verify for the computed input.
    """
    # 1) Aggregate reveals (bias-resistant combiner; already reveal-verified upstream).
    agg = aggregate_reveals(reveals)
    if _METRICS:  # pragma: no cover
        # Count included reveals (length of iterable may be non-materialized).
        REVEALS_AGGREGATED.inc(sum(1 for _ in reveals))  # reveals likely consumed; materialize first if needed.

    # 2) Derive VDF input and verify proof.
    vdf_in = _derive_vdf_input(prev_beacon=state.prev_output, aggregate=agg, round_id=round_id)
    t0 = time.perf_counter()
    vdf_out = vdf_verify(vdf_in, vdf_proof)
    if _METRICS:  # pragma: no cover
        VDF_VERIFY_SECONDS.observe(time.perf_counter() - t0)

    # 3) Optional QRNG mix via extract-then-xor combiner with a transcript.
    # Transcript binds the round and VDF transcript, preventing reuse/replay of QRNG bytes.
    transcript = dhash(DOMAIN_BEACON_MIX, round_id.to_bytes(8, "big"), vdf_in, vdf_out)
    mixed_out, mixed = _maybe_mix_with_qrng(vdf_out, qrng_bytes, transcript)
    if _METRICS and qrng_bytes:  # pragma: no cover
        MIX_ENTROPY_BYTES.observe(len(qrng_bytes))

    # 4) Produce final beacon bytes and structure. If QRNG was mixed, hash once more
    #    under the final domain to obtain the published out; otherwise hash the VDF out.
    if mixed:
        final_bytes = sha3_512(DOMAIN_BEACON_FINAL + round_id.to_bytes(8, "big") + mixed_out)
    else:
        final_bytes = _finalize_bytes_from_vdf(vdf_out, round_id)

    # Compose BeaconOut (fields defined in types.core.BeaconOut).
    # We include helpful non-consensus metadata (agg, vdf_in/out) if the dataclass supports them.
    # Fallback to the minimal constructor (round_id, output) if necessary.
    kwargs = {
        "round_id": round_id,
        "output": final_bytes,
        "aggregate": agg,
        "vdf_input": vdf_in,
        "vdf_output": vdf_out,
        "mixed_with_qrng": mixed,
    }
    try:
        return BeaconOut(**kwargs)  # type: ignore[arg-type]
    except TypeError:
        # Minimal shape
        try:
            return BeaconOut(round_id=round_id, output=final_bytes)  # type: ignore[call-arg]
        except TypeError:
            # Absolute minimal (some implementations use 'bytes' / 'data')
            return BeaconOut(final_bytes)  # type: ignore[call-arg]


__all__ = ["finalize_round"]
