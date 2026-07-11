"""
animica.ai.receipt_mw — attach Proof-of-Inference receipts to gateway responses.

One small, well-tested seam the gateway calls after a completion is produced. It
builds an :class:`InferenceReceipt`, optionally signs it (ML-DSA-65), and returns
``(response_with_receipt, receipt)`` plus the ``X-Animica-Receipt`` header value.

Mode (``ANIMICA_AI_RECEIPTS``): ``off`` | ``hash`` | ``signed`` (default ``signed``).
  * ``off``    — response is byte-identical to pre-7.1.1 (no field, no header).
  * ``hash``   — attach an unsigned, content-hashed receipt (``signed=False``).
  * ``signed`` — additionally attach an ML-DSA-65 signature; degrades to ``hash``
                 when no signing key is available or PQ is disabled.

Signing ML-DSA-65 is a few milliseconds of pure Python, done inline for
non-streaming responses (the content is already generated, so it adds negligible
latency). For streaming, the gateway emits the receipt as the final SSE event so
the user sees tokens first.
"""

from __future__ import annotations

import os
from typing import Any, Optional

RECEIPTS_HEADER = "X-Animica-Receipt"


def receipts_mode() -> str:
    mode = (os.environ.get("ANIMICA_AI_RECEIPTS", "signed") or "signed").strip().lower()
    return mode if mode in {"off", "hash", "signed"} else "signed"


def build_receipt(*, provider_key: str, resolved_model: str, prompt: str, output: str,
                  tokens_in: int, tokens_out: int, requester: Optional[str] = None,
                  qseed: Optional[dict[str, Any]] = None, seed_int: Optional[int] = None,
                  max_tokens: Optional[int] = None, checkpoint_hash: Optional[str] = None,
                  chain_id: int = 1, mode: Optional[str] = None) -> Optional[Any]:
    """Build (and, in ``signed`` mode, sign) a receipt. Returns None in ``off`` mode."""
    mode = mode or receipts_mode()
    if mode == "off":
        return None
    from animica.ena.inference_receipt import build_inference_receipt, sign_inference_receipt

    metadata: dict[str, Any] = {}
    if seed_int is not None:
        metadata["seed_int"] = int(seed_int)
    if max_tokens is not None:
        metadata["max_tokens"] = int(max_tokens)

    receipt = build_inference_receipt(
        model_id=resolved_model, provider_key=provider_key, prompt=prompt, output=output,
        tokens_in=tokens_in, tokens_out=tokens_out, requester=requester,
        checkpoint_hash=checkpoint_hash, qseed=qseed, chain_id=chain_id, metadata=metadata)
    if mode == "signed":
        receipt = sign_inference_receipt(receipt)  # fail-open → signed=False
    return receipt


def attach_receipt(response: dict[str, Any], *, provider_key: str, resolved_model: str,
                   prompt: str, output: str, tokens_in: int, tokens_out: int,
                   requester: Optional[str] = None, qseed: Optional[dict[str, Any]] = None,
                   seed_int: Optional[int] = None, max_tokens: Optional[int] = None,
                   checkpoint_hash: Optional[str] = None, chain_id: int = 1,
                   mode: Optional[str] = None) -> tuple[dict[str, Any], Optional[Any], Optional[str]]:
    """Attach ``animica_receipt`` to ``response`` (unless mode=off).

    Returns ``(response, receipt_or_None, header_value_or_None)``.
    """
    receipt = build_receipt(provider_key=provider_key, resolved_model=resolved_model,
                            prompt=prompt, output=output, tokens_in=tokens_in,
                            tokens_out=tokens_out, requester=requester, qseed=qseed,
                            seed_int=seed_int, max_tokens=max_tokens,
                            checkpoint_hash=checkpoint_hash, chain_id=chain_id, mode=mode)
    if receipt is None:
        return response, None, None
    rd = receipt.to_dict()
    response = dict(response)
    response["animica_receipt"] = rd
    return response, receipt, rd.get("receipt_hash")
