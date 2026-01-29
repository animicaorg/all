from __future__ import annotations

"""
ProofsViewAdapter
=================

A resilient bridge that lets the miner (and other services) verify locally
built *proof envelopes*, extract canonical **metrics** used by PoIES, and
map those metrics into **ψ-input** records expected by the consensus scorer.

It prefers the real implementations from `proofs/`:

  • proofs.cbor                 → decode/encode envelope bodies (CBOR/CDDL)
  • proofs.registry             → type_id → verifier dispatcher
  • proofs.hashshare / ai / …   → full verifiers (return metrics)
  • proofs.policy_adapter       → metrics → ψ-input mapping
  • proofs.receipts             → compact proof receipt (Merkle leaf material)
  • proofs.nullifiers           → domain-separated nullifier computation

But it also includes graceful fallbacks so that single-node dev mining works
even if some submodules aren’t built/installed yet.

Envelope shape (duck-typed)
---------------------------
`envelope` may be:
  • bytes: CBOR-encoded envelope with fields {type_id, body, nullifier?}
  • dict-like: {"type_id": "...", "body": {...}, "nullifier": "0x..."} (nullifier optional)

Public API
----------
    adapter = ProofsViewAdapter()

    # verify a single envelope (bytes or dict)
    result = adapter.verify(envelope, context={"header": header_dict})
    result =>
      {
        "type_id": "ai" | "hash" | "quantum" | "storage" | "vdf" | "...",
        "metrics": {...},              # normalized metrics dict
        "psi_input": {...},            # mapped ψ-input record (no caps applied here)
        "receipt": {...},              # compact receipt (if receipts builder available)
        "nullifier": "0x...",          # computed or provided
        "ok": True,
      }

    # verify many (short-circuiting on first failure)
    batch = adapter.verify_batch([env1, env2, ...], context={...})
    batch =>
      {
        "items": [result, ...],
        "psi_inputs": [ {...}, {...}, ... ],
        "ok": True,                    # all verified
      }

Notes
-----
• This adapter *does not* apply consensus caps or compare against Θ; it only
  produces ψ-input candidates. Use mining/adapters/consensus_view.py for that.
• HashShare proofs normally contribute via H(u) in consensus math; verifiers
  may still report metrics (e.g., d_ratio). The ψ mapping keeps ψ≈0 for hash
  unless your policy adapter specifies otherwise.
"""

import binascii
import math
from dataclasses import dataclass
from typing import (Any, Callable, Dict, Iterable, List, Optional, Sequence,
                    Tuple, Union)

# ---------------------------------------------------------------------------
# Logging (best-effort)
try:
    from core.logging import get_logger

    log = get_logger("mining.adapters.proofs_view")
except Exception:  # noqa: BLE001
    import logging

    log = logging.getLogger("mining.adapters.proofs_view")
    if not log.handlers:
        logging.basicConfig(level=logging.INFO)

Envelope = Union[bytes, Dict[str, Any]]

# ---------------------------------------------------------------------------
# Optional imports from proofs/
# CBOR decode for envelope ---------------------------------------------------
_decode_envelope: Optional[Callable[[bytes], Dict[str, Any]]] = None
try:
    # Prefer a dedicated function if exposed
    from proofs.cbor import \
        decode_envelope as _dec1  # type: ignore[attr-defined]

    _decode_envelope = _dec1
except Exception:  # noqa: BLE001
    try:
        from proofs.cbor import decode as _dec2  # type: ignore[attr-defined]

        def _decode_envelope(b: bytes) -> Dict[str, Any]:  # type: ignore[no-redef]
            obj = _dec2(b)  # type: ignore[misc]
            if not isinstance(obj, dict):
                raise ValueError("Decoded envelope is not a dict")
            return obj

    except Exception:  # noqa: BLE001
        try:
            import cbor2  # type: ignore

            def _decode_envelope(b: bytes) -> Dict[str, Any]:  # type: ignore[no-redef]
                obj = cbor2.loads(b)
                if not isinstance(obj, dict):
                    raise ValueError("Decoded envelope is not a dict")
                return obj

        except Exception:  # noqa: BLE001
            _decode_envelope = None

# Verifier registry ----------------------------------------------------------
_get_verifier: Optional[Callable[[str], Any]] = None
try:
    from proofs.registry import \
        get_verifier as _get_verifier  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    _get_verifier = None

# Specific verifiers (direct) as last-resort fallbacks ----------------------
_ver_hash = _ver_ai = _ver_quantum = _ver_storage = _ver_vdf = None
try:
    from proofs.hashshare import \
        verify as _ver_hash  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass
try:
    from proofs.ai import verify as _ver_ai  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass
try:
    from proofs.quantum import \
        verify as _ver_quantum  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass
try:
    from proofs.storage import \
        verify as _ver_storage  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass
try:
    from proofs.vdf import verify as _ver_vdf  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

# Policy adapter (metrics → ψ-inputs) ---------------------------------------
_metrics_to_psi: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
try:
    from proofs.policy_adapter import \
        metrics_to_psi as _metrics_to_psi  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    try:
        from proofs.policy_adapter import \
            map_metrics_to_psi as _metrics_to_psi  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        _metrics_to_psi = None

# Receipts + nullifiers ------------------------------------------------------
_build_receipt: Optional[Callable[..., Dict[str, Any]]] = None
try:
    from proofs.receipts import \
        build_receipt as _build_receipt  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    _build_receipt = None

_compute_nullifier: Optional[Callable[[str, Dict[str, Any]], bytes]] = None
try:
    from proofs.nullifiers import \
        compute_nullifier as _compute_nullifier  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    _compute_nullifier = None

# Some utility hashing if needed for fallback nullifiers
try:
    from proofs.utils.hash import \
        sha3_256 as _sha3_256  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    try:
        from core.utils.hash import \
            sha3_256 as _sha3_256  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        _sha3_256 = None


# ---------------------------------------------------------------------------
# Errors
class ProofsViewError(Exception):
    pass


@dataclass
class VerifyResult:
    type_id: str
    metrics: Dict[str, Any]
    psi_input: Dict[str, Any]
    receipt: Optional[Dict[str, Any]]
    nullifier: Optional[str]
    ok: bool


class ProofsViewAdapter:
    """
    Verify envelopes → metrics → ψ-inputs. Best-effort & portable.
    """

    # ---------------------------- public API ----------------------------
    def verify(
        self, envelope: Envelope, *, context: Optional[Dict[str, Any]] = None
    ) -> VerifyResult:
        """
        Verify a single proof envelope and map to ψ-inputs.

        Parameters
        ----------
        envelope : bytes | dict
            CBOR-encoded envelope bytes or dict-like object with keys:
            "type_id", "body", optional "nullifier".
        context : dict
            Optional verification context (e.g., {"header": {...}, "policy": {...}}).

        Returns
        -------
        VerifyResult
        """
        env = self._ensure_envelope_dict(envelope)
        type_id = str(env.get("type_id", "")).lower().strip()
        if not type_id:
            raise ProofsViewError("Envelope missing 'type_id'")

        # Verify body → metrics
        metrics = self._verify_body(type_id, env.get("body"), env=env, context=context)

        # Map metrics → ψ-inputs (policy-adapter preferred; heuristic fallback)
        psi_input = self._map_metrics_to_psi(metrics)

        # Receipt/nullifier (optional, best-effort)
        rec = self._build_receipt_safe(type_id, env, metrics)
        nul = self._ensure_nullifier(type_id, env)

        return VerifyResult(
            type_id=type_id,
            metrics=metrics,
            psi_input=psi_input,
            receipt=rec,
            nullifier=nul,
            ok=True,
        )

    def verify_batch(
        self,
        envelopes: Sequence[Envelope],
        *,
        context: Optional[Dict[str, Any]] = None,
        stop_on_fail: bool = True,
    ) -> Dict[str, Any]:
        """
        Verify a batch of envelopes. Short-circuits on first failure by default.

        Returns
        -------
        {
          "items": [VerifyResult, ...],
          "psi_inputs": [ dict, ... ],
          "ok": True|False,
          "failed_index": int | None,
          "error": str | None,
        }
        """
        items: List[VerifyResult] = []
        psi_inputs: List[Dict[str, Any]] = []
        for i, env in enumerate(envelopes):
            try:
                r = self.verify(env, context=context)
                items.append(r)
                psi_inputs.append(r.psi_input)
            except Exception as e:  # noqa: BLE001
                log.warning("verify_batch failed", extra={"index": i, "err": str(e)})
                if stop_on_fail:
                    return {
                        "items": items,
                        "psi_inputs": psi_inputs,
                        "ok": False,
                        "failed_index": i,
                        "error": str(e),
                    }
                # else skip and continue
        return {
            "items": items,
            "psi_inputs": psi_inputs,
            "ok": True,
            "failed_index": None,
            "error": None,
        }

    # ---------------------------- internals ----------------------------
    def _ensure_envelope_dict(self, envelope: Envelope) -> Dict[str, Any]:
        if isinstance(envelope, (bytes, bytearray, memoryview)):
            if _decode_envelope is None:
                raise ProofsViewError(
                    "CBOR decoder is unavailable and envelope is bytes"
                )
            return _decode_envelope(bytes(envelope))  # type: ignore[misc]
        if not isinstance(envelope, dict):
            raise ProofsViewError("Envelope must be bytes or dict")
        return envelope

    def _verify_body(
        self,
        type_id: str,
        body: Any,
        *,
        env: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Dispatch to the correct verifier.
        Expects a dict of *metrics* on success; raises on failure.
        """
        # 1) Try registry.get_verifier(type_id) path
        if _get_verifier is not None:
            try:
                verifier = _get_verifier(type_id)  # may raise KeyError
                # duck-typing: verifier could be a callable or an object with .verify()
                if callable(verifier):
                    metrics = verifier(body, envelope=env, context=context)  # type: ignore[misc]
                elif hasattr(verifier, "verify"):
                    metrics = verifier.verify(body, envelope=env, context=context)  # type: ignore[attr-defined]
                else:
                    raise ProofsViewError(f"Verifier for '{type_id}' is not callable")
                return self._normalize_metrics(type_id, metrics)
            except KeyError:
                # fall through
                pass
            except Exception as e:  # noqa: BLE001
                raise ProofsViewError(
                    f"Verifier via registry failed for {type_id}: {e}"
                ) from e

        # 2) Try direct module fallbacks
        try:
            if type_id in ("hash", "hashshare", "share") and _ver_hash is not None:
                m = _ver_hash(body, envelope=env, context=context)  # type: ignore[misc]
                return self._normalize_metrics("hash", m)
            if type_id in ("ai", "ai_v1") and _ver_ai is not None:
                m = _ver_ai(body, envelope=env, context=context)  # type: ignore[misc]
                return self._normalize_metrics("ai", m)
            if type_id in ("quantum", "quantum_v1") and _ver_quantum is not None:
                m = _ver_quantum(body, envelope=env, context=context)  # type: ignore[misc]
                return self._normalize_metrics("quantum", m)
            if type_id in ("storage", "storage_v0") and _ver_storage is not None:
                m = _ver_storage(body, envelope=env, context=context)  # type: ignore[misc]
                return self._normalize_metrics("storage", m)
            if type_id in ("vdf", "vdf_wesolowski") and _ver_vdf is not None:
                m = _ver_vdf(body, envelope=env, context=context)  # type: ignore[misc]
                return self._normalize_metrics("vdf", m)
        except Exception as e:  # noqa: BLE001
            raise ProofsViewError(
                f"Direct verifier call failed for {type_id}: {e}"
            ) from e

        # 3) Last-resort heuristic validation (non-consensus; DEV ONLY)
        log.warning(
            "No verifier found for type_id=%s; using heuristic DEV checks", type_id
        )
        heuristic = self._heuristic_verify(type_id, body)
        return self._normalize_metrics(type_id, heuristic)

    def _normalize_metrics(self, type_id: str, metrics: Any) -> Dict[str, Any]:
        """
        Ensure metrics is a dict and contains 'kind' and commonly used fields.
        """
        if not isinstance(metrics, dict):
            raise ProofsViewError(f"Verifier returned non-dict metrics for {type_id}")
        out = {"kind": type_id}
        out.update(metrics)
        # Ensure key spellings exist (used by policy_adapter)
        if type_id == "hash" and "d_ratio" not in out and "difficulty_ratio" in out:
            out["d_ratio"] = out["difficulty_ratio"]
        return out

    def _map_metrics_to_psi(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        if _metrics_to_psi is not None:
            try:
                return _metrics_to_psi(metrics)  # type: ignore[misc]
            except Exception as e:  # noqa: BLE001
                log.warning(
                    "metrics_to_psi failed; using heuristic mapping",
                    extra={"err": str(e)},
                )

        # Heuristic ψ mapping (kept consistent with consensus_view fallback)
        kind = str(metrics.get("kind", "unknown")).lower()
        if kind == "hash":
            return {"kind": "hash", "psi": 0.0}
        if kind == "ai":
            units = float(metrics.get("ai_units", 0.0))
            traps = float(metrics.get("traps_ratio", 0.0))
            qos = float(metrics.get("qos", 0.0))
            return {"kind": "ai", "psi": 0.001 * units * _clip01(traps) * _clip01(qos)}
        if kind == "quantum":
            units = float(metrics.get("quantum_units", 0.0))
            traps = float(metrics.get("traps_ratio", 0.0))
            qos = float(metrics.get("qos", 0.0))
            return {
                "kind": "quantum",
                "psi": 0.002 * units * _clip01(traps) * _clip01(qos),
            }
        if kind == "storage":
            qos = float(metrics.get("qos", 0.0))
            return {"kind": "storage", "psi": 0.0001 * _clip01(qos)}
        if kind == "vdf":
            sec = float(metrics.get("vdf_seconds", 0.0))
            return {"kind": "vdf", "psi": 0.0002 * max(0.0, sec)}
        return {"kind": kind, "psi": float(metrics.get("psi", 0.0))}

    def _build_receipt_safe(
        self, type_id: str, env: Dict[str, Any], metrics: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if _build_receipt is None:
            return None
        try:
            return _build_receipt(type_id=type_id, envelope=env, metrics=metrics)  # type: ignore[misc]
        except Exception as e:  # noqa: BLE001
            log.debug("receipt build failed", extra={"err": str(e)})
            return None

    def _ensure_nullifier(self, type_id: str, env: Dict[str, Any]) -> Optional[str]:
        # Prefer provided field
        nul = env.get("nullifier")
        if isinstance(nul, (bytes, bytearray)):
            return "0x" + binascii.hexlify(bytes(nul)).decode()
        if isinstance(nul, str) and nul.startswith("0x"):
            return nul

        # Compute if we can
        if _compute_nullifier is not None:
            try:
                b = _compute_nullifier(type_id, env.get("body", {}))  # type: ignore[misc]
                if isinstance(b, (bytes, bytearray)):
                    return "0x" + binascii.hexlify(bytes(b)).decode()
            except Exception as e:  # noqa: BLE001
                log.debug("nullifier compute failed", extra={"err": str(e)})

        # Fallback: hash(body) with domain tag (DEV ONLY)
        if _sha3_256 is not None:
            try:
                body = env.get("body", {})
                enc = _cheap_json_like(body).encode("utf-8")
                digest = _sha3_256(b"DEV/NULLIFIER|" + enc)
                return "0x" + binascii.hexlify(digest).decode()
            except Exception:  # noqa: BLE001
                pass
        return None

    # ---------------------------- heuristics ----------------------------
    def _heuristic_verify(self, type_id: str, body: Any) -> Dict[str, Any]:
        """
        DEV-ONLY heuristic validators for when real verifiers are unavailable.
        They perform basic shape/sanity checks and emit *very conservative* metrics.
        """
        if not isinstance(body, dict):
            raise ProofsViewError("Proof body must be a dict for heuristic checks")

        if type_id in ("hash", "hashshare", "share"):
            # Expect: {"target": <float 0..1>, "draw": <float 0..1>}
            t = float(body.get("target", 1.0))
            d = float(body.get("draw", 1.0))
            if not (0.0 < t <= 1.0) or not (0.0 < d <= 1.0):
                raise ProofsViewError("hashshare: target/draw must be in (0,1]")
            d_ratio = -math.log(d) / max(1e-12, -math.log(t))  # share difficulty ratio
            if d_ratio < 0.0 or math.isnan(d_ratio) or math.isinf(d_ratio):
                raise ProofsViewError("hashshare: invalid ratio")
            return {"kind": "hash", "d_ratio": float(max(0.0, min(10.0, d_ratio)))}

        if type_id in ("ai", "ai_v1"):
            # Expect: {"ai_units": number, "traps_ratio": 0..1, "qos": 0..1}
            units = float(body.get("ai_units", 0.0))
            traps = _clip01(body.get("traps_ratio", 0.0))
            qos = _clip01(body.get("qos", 0.0))
            if units < 0:
                raise ProofsViewError("ai: ai_units must be non-negative")
            return {"kind": "ai", "ai_units": units, "traps_ratio": traps, "qos": qos}

        if type_id in ("quantum", "quantum_v1"):
            units = float(body.get("quantum_units", 0.0))
            traps = _clip01(body.get("traps_ratio", 0.0))
            qos = _clip01(body.get("qos", 0.0))
            if units < 0:
                raise ProofsViewError("quantum: quantum_units must be non-negative")
            return {
                "kind": "quantum",
                "quantum_units": units,
                "traps_ratio": traps,
                "qos": qos,
            }

        if type_id in ("storage", "storage_v0"):
            qos = _clip01(body.get("qos", 0.0))
            return {"kind": "storage", "qos": qos}

        if type_id in ("vdf", "vdf_wesolowski"):
            sec = float(body.get("vdf_seconds", 0.0))
            if sec < 0:
                raise ProofsViewError("vdf: seconds must be non-negative")
            return {"kind": "vdf", "vdf_seconds": sec}

        # Unknown → minimally validated
        return {"kind": type_id, "psi": 0.0}


# ---------------------------------------------------------------------------
# Small helpers


def _clip01(x: Any) -> float:
    try:
        v = float(x)
    except Exception:  # noqa: BLE001
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _cheap_json_like(obj: Any) -> str:
    """
    Deterministic-ish stringification for DEV fallback hashing.
    NOT a consensus serializer—only used when proper nullifier code is missing.
    """
    if obj is None or isinstance(obj, (int, float, str)):
        return repr(obj)
    if isinstance(obj, (bytes, bytearray, memoryview)):
        return "0x" + binascii.hexlify(bytes(obj)).decode()
    if isinstance(obj, dict):
        # sort keys for stability
        items = ",".join(
            f"{repr(k)}:{_cheap_json_like(obj[k])}" for k in sorted(obj.keys())
        )
        return "{" + items + "}"
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(_cheap_json_like(x) for x in obj) + "]"
    return repr(obj)


__all__ = ["ProofsViewAdapter", "VerifyResult", "ProofsViewError"]
