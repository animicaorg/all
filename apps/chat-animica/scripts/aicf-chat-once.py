#!/usr/bin/env python3
"""
One-shot AICF inference call for Studio's agent runner.

Reads a JSON spec from stdin:
    {
        "prompt": "system + user message text",
        "wallet_path": "/root/.animica/wallets.json",   # optional
        "wallet_label": "pool",                         # optional
        "tier": "tiny",                                  # optional
        "max_output_tokens": 1024,                      # optional
        "temperature": 0.2,                              # optional
        "yolo": true                                     # optional
    }

Writes a JSON receipt to stdout (one line):
    {
        "ok": true,
        "content": "model response text",
        "provider": "distributed-aicf",
        "tier": "tiny",
        "cost_animica": 0.001036,
        "latency_ms": 68559,
        "fallback_reasons": [],
        "source": "aicf"
    }

On any failure, exits with code 1 and prints:
    {"ok": false, "error": "...", "code": "WALLET_UNAVAILABLE|..." }

Studio's agent runner spawns this as a subprocess. Keeping the contract narrow
(stdin JSON in / stdout JSON out) means the runner does not import any Python
and the secret material never crosses an env-var boundary that's visible to
unrelated processes via `ps`.
"""
from __future__ import annotations

import json
import os
import sys
import time


def emit(payload: dict, code: int = 0) -> None:
    print(json.dumps(payload), flush=True)
    sys.exit(code)


def fail(error: str, *, kind: str = "GENERIC") -> None:
    emit({"ok": False, "error": error, "code": kind}, code=1)


def main() -> None:
    try:
        spec_raw = sys.stdin.read()
        spec = json.loads(spec_raw) if spec_raw.strip() else {}
    except Exception as exc:
        fail(f"invalid stdin json: {exc}", kind="BAD_STDIN")

    prompt = str(spec.get("prompt") or "").strip()
    if not prompt:
        fail("prompt is required", kind="MISSING_PROMPT")

    wallet_path = spec.get("wallet_path") or os.environ.get(
        "ANIMICA_AICF_WALLET", "/root/.animica/wallets.json")
    wallet_label = spec.get("wallet_label") or os.environ.get(
        "ANIMICA_AICF_WALLET_LABEL", "pool")
    tier = str(spec.get("tier") or os.environ.get("ANIMICA_AICF_TIER", "tiny"))
    max_output_tokens = int(spec.get("max_output_tokens", 1024))
    temperature = float(spec.get("temperature", 0.2))
    yolo = bool(spec.get("yolo", True))
    rpc_url = (spec.get("rpc_url")
               or os.environ.get("ANIMICA_RPC_URL")
               or "http://127.0.0.1:8545/rpc")

    try:
        from agent_runtime.config import load_config
        from agent_runtime.providers import DistributedAICFProvider, TurnRequest
        from agent_runtime.errors import ProviderUnavailable
    except Exception as exc:
        fail(f"agent_runtime import failed: {exc}", kind="IMPORT_FAILED")

    try:
        cfg = load_config()
    except Exception as exc:
        fail(f"config load failed: {exc}", kind="CONFIG_FAILED")

    try:
        provider = DistributedAICFProvider(
            cfg=cfg,
            rpc_url=rpc_url,
            wallet_path=wallet_path,
            wallet_label=wallet_label,
        )
    except Exception as exc:
        fail(f"provider init failed: {exc}", kind="PROVIDER_INIT_FAILED")

    avail, reason = provider.is_available()
    if not avail:
        fail(f"provider unavailable: {reason}", kind="PROVIDER_UNAVAILABLE")

    req = TurnRequest(
        prompt=prompt,
        tier_preferred=tier,
        history=[],
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        yolo=yolo,
    )

    try:
        result = provider.serve(req)
    except ProviderUnavailable as exc:
        fail(f"insufficient balance / provider refused: {exc}",
             kind="PROVIDER_UNAVAILABLE")
    except Exception as exc:
        fail(f"serve failed: {exc}", kind="SERVE_FAILED")

    # `result.provider` is the cascade-entry name ("distributed-aicf");
    # the actually-serving miner id lives in result.metadata.provider_id.
    # Expose both so the UI can render a real receipt (which miner served
    # this turn, which AICF job id, etc.) instead of a generic cascade tag.
    meta = result.metadata or {}
    emit({
        "ok": True,
        "content": result.text,
        "provider": result.provider,
        "miner_id": str(meta.get("provider_id") or ""),
        "job_id": str(meta.get("job_id") or ""),
        "tier": result.tier,
        "requested_tier": result.requested_tier,
        "cost_animica": result.cost_animica,
        "latency_ms": result.latency_ms,
        "fallback_reasons": result.fallback_reasons,
        "source": "aicf",
    })


if __name__ == "__main__":
    main()
