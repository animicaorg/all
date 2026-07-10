"""
animica.ai.replay — independent, offline replay of Proof-of-Inference receipts.

Given an :class:`InferenceReceipt` (as a dict) *and the original prompt* (receipts
are content-addressed and never store the raw prompt), this re-runs generation
with the recorded seed and checks ``sha3(output) == output_hash``.

Reproducibility is claimed honestly:
  * ``verified``    — a bit-for-bit reproducible local backend (DeterministicModel,
                      or a seed-honoring in-process ENA runner). We re-run and
                      report a real ``match``.
  * ``best_effort`` — a seed-honoring but non-deterministic backend (remote
                      openai/ollama/anthropic/chutes, or torch): we do NOT re-run
                      (cost / kernel & provider drift make it unreliable).
  * ``unsupported`` — the backend cannot be replayed at all.

No network is required for a ``verified`` replay. Heavy deps stay lazy.
"""

from __future__ import annotations

from typing import Any, Optional

from animica.ena.models import sha3_hex

# Only bit-for-bit reproducible LOCAL backends can be 'verified'. ena_served is
# an HTTP call to a serve URL (possibly remote, torch-backed) → best_effort, never
# 'verified', unless an explicit in-process runner is supplied.
_LOCAL_VERIFIABLE = {"deterministic"}
_REMOTE_SEEDED = {"openai", "openai_compatible", "ollama", "anthropic", "claude",
                  "chutes", "bittensor", "ena_served"}


def _classify(provider_key: str, *, ena: Any) -> str:
    p = (provider_key or "").lower()
    if p in _LOCAL_VERIFIABLE:
        return "verified"
    if p == "ena_served" and ena is not None and getattr(ena, "in_process_runner", False):
        return "verified"
    if p in _REMOTE_SEEDED:
        return "best_effort"
    return "unsupported"


def _regenerate(receipt: dict[str, Any], prompt: str, *, ena: Any) -> Optional[str]:
    provider = (receipt.get("provider_key") or "").lower()
    md = receipt.get("metadata") or {}
    seed = md.get("seed_int")
    max_tokens = md.get("max_tokens")
    if provider == "deterministic":
        from animica.ena.models import ModelProviderConfig
        from animica.ena.providers import build_model_adapter
        cfg = ModelProviderConfig(name="deterministic", provider="deterministic",
                                  model=receipt.get("model_id") or "deterministic")
        adapter = build_model_adapter(cfg)
        return adapter.generate(prompt, seed=seed, max_tokens=max_tokens)
    if provider == "ena_served" and ena is not None:
        try:
            from animica.ena.serving import runner_for  # type: ignore
            runner = runner_for(ena, md.get("pool_id"))
            return runner.generate(prompt, seed=seed, max_tokens=max_tokens)
        except Exception:  # noqa: BLE001
            return None
    return None


def replay_receipt(receipt: dict[str, Any], prompt: str, *, ena: Any = None) -> dict[str, Any]:
    """Re-run and verify a receipt against its original prompt (offline)."""
    stored_prompt_hash = receipt.get("prompt_hash", "")
    prompt_ok = bool(stored_prompt_hash) and sha3_hex(prompt or "") == stored_prompt_hash
    provider = receipt.get("provider_key", "")
    output_hash = receipt.get("output_hash", "")
    reproducible = _classify(provider, ena=ena)

    result = {
        "reproducible": reproducible,
        "match": None,
        "prompt_ok": prompt_ok,
        "output_hash": output_hash,
        "recomputed_hash": None,
        "backend_class": provider,
    }
    if reproducible != "verified":
        return result
    if not prompt_ok:
        # Can't reproduce an output for a prompt that doesn't match the receipt.
        result["match"] = False
        return result
    try:
        out = _regenerate(receipt, prompt, ena=ena)
    except Exception:  # noqa: BLE001
        out = None
    if out is None:
        result["reproducible"] = "unsupported"
        return result
    recomputed = sha3_hex(out)
    result["recomputed_hash"] = recomputed
    result["match"] = bool(output_hash) and recomputed == output_hash
    return result


def audit_pick(receipts: list[dict[str, Any]], *, k: int) -> list[dict[str, Any]]:
    """Deterministically pick ``k`` receipts to deep-replay, unbiasable given the
    set (the selection seed is the SHA3 of the sorted receipt hashes)."""
    if k <= 0 or not receipts:
        return []
    if k >= len(receipts):
        return list(receipts)
    hashes = sorted(str(r.get("receipt_hash", "")) for r in receipts)
    beacon_seed = bytes.fromhex(sha3_hex("".join(hashes)))
    try:
        from aicf.integration.quantum_seed import audit_select
        picked = audit_select(receipts, beacon_seed=beacon_seed, beacon_round=0,
                              k=k, salt=b"animica.ai.replay-audit")
        if picked:
            return list(picked)
    except Exception:  # noqa: BLE001 — fall back to a deterministic local pick
        pass
    # Deterministic fallback: index by a keyed hash of each receipt_hash.
    order = sorted(range(len(receipts)),
                   key=lambda i: sha3_hex(beacon_seed.hex() + str(receipts[i].get("receipt_hash", ""))))
    return [receipts[i] for i in order[:k]]
