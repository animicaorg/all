"""
animica.ena.serving
===================

Serve-while-train. Loads a pool's *currently-promoted* checkpoint and serves
OpenAI-compatible inference (``/v1/chat/completions`` + ``/v1/models``), and
hot-reloads when the pool promotes a newer round — so version N is served while
version N+1 trains. Every served request is credited back to the pool ledger
(``PoolContribution`` role ``server``), so serving earns the pool's ``servers``
reward bucket; rewards stay ANM-denominated through the pool payout path.

Heavy deps (torch/transformers/peft) are imported lazily. Without them — or when
the promoted checkpoint is a CPU/test merge-plan rather than real weights — a
clearly-labelled deterministic stub responds, so the wiring + reward loop are
exercisable on a CPU-only box and in tests.
"""

from __future__ import annotations

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .models import canonical_json, new_uuid, now_ts, sha3_hex


def build_serving_receipt(*, pool_id: str, model_id: str, requester: Optional[str],
                          provider_id: str, prompt: str, output: str,
                          tokens_in: int, tokens_out: int, fee_nano: int = 0,
                          aicf_bps: int = 2000, chain_id: int = 1,
                          served_round: Optional[int] = None) -> dict[str, Any]:
    """Deterministic, on-chain-ready inference settlement receipt for one served
    request (mirrors aicf ComputeReceipt). The provider signs + anchors it on
    chain; here we produce the canonical content + ``receipt_hash`` so the served
    work is verifiable and the fee split (provider vs AICF) is fixed off-chain.
    """
    aicf_cut = int(fee_nano) * int(aicf_bps) // 10000
    fields = {
        "receipt_version": 1, "kind": "ena.inference", "chain_id": int(chain_id),
        "pool_id": pool_id, "model_id": model_id,
        "requester_address": requester or "", "provider_id": provider_id or "",
        "prompt_hash": sha3_hex(prompt or ""), "output_hash": sha3_hex(output or ""),
        "tokens_in": int(tokens_in), "tokens_out": int(tokens_out),
        "fee_paid": int(fee_nano), "aicf_cut": aicf_cut,
        "provider_cut": int(fee_nano) - aicf_cut,
        "served_round": served_round, "timestamp": now_ts(),
    }
    fields["receipt_hash"] = sha3_hex(canonical_json(fields))
    return fields


def approx_tokens(text: str) -> int:
    """Cheap, deterministic token estimate (~4 chars/token), min 1 for non-empty."""
    n = len((text or "").strip())
    return max(1, n // 4) if n else 0


def messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    """Flatten OpenAI chat messages into a single prompt string."""
    parts = []
    for m in messages or []:
        role = str(m.get("role", "user"))
        content = m.get("content", "")
        if isinstance(content, list):  # OpenAI content-parts form
            content = "".join(str(p.get("text", "")) for p in content
                              if isinstance(p, dict))
        parts.append(f"{role}: {content}")
    parts.append("assistant:")
    return "\n".join(parts)


def _stub_reply(prompt: str, model_id: str) -> str:
    """Deterministic offline reply (no torch/weights) — clearly labelled."""
    digest = sha3_hex(f"{model_id}|{prompt}")[:12]
    tail = (prompt or "").strip().splitlines()[-1:] or [""]
    return (f"[animica-pool stub · {model_id}] received: {tail[0][:160]} "
            f"(install animica[gpu] + promote real weights for live inference; "
            f"trace {digest})")


# ---------------------------------------------------------------------------
# checkpoint runner (lazy heavy deps; stub fallback)
# ---------------------------------------------------------------------------

class PoolModelRunner:
    """Loads ``base_model`` (+ optional LoRA adapter dir) and generates text."""

    def __init__(self, base_model: str, adapter_path: Optional[str] = None) -> None:
        self.base_model = base_model
        self.adapter_path = adapter_path
        self._model = None
        self._tok = None
        self._stub = False
        self._device = "cpu"

    def _adapter_dir(self) -> Optional[Path]:
        if not self.adapter_path:
            return None
        p = Path(self.adapter_path)
        if p.is_dir() and (p / "adapter_config.json").is_file():
            return p
        return None

    def _load(self) -> None:
        if self._model is not None or self._stub:
            return
        try:  # pragma: no cover - heavy path exercised only with the gpu extra
            import torch  # type: ignore
            from transformers import (AutoModelForCausalLM,  # type: ignore
                                      AutoTokenizer)
        except Exception:
            self._stub = True
            return
        try:
            tok = AutoTokenizer.from_pretrained(self.base_model)
            if tok.pad_token is None:
                tok.pad_token = tok.eos_token
            model = AutoModelForCausalLM.from_pretrained(self.base_model)
            adapter = self._adapter_dir()
            if adapter is not None:
                try:
                    from peft import PeftModel  # type: ignore
                    model = PeftModel.from_pretrained(model, str(adapter))
                except Exception:
                    pass
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            model.to(self._device)
            model.eval()
            self._tok, self._model = tok, model
        except Exception:  # any load failure → stub, never crash the server
            self._stub = True

    def generate(self, prompt: str, *, max_tokens: int = 256,
                 temperature: float = 0.2) -> str:
        self._load()
        if self._stub or self._model is None:
            return _stub_reply(prompt, self.base_model)
        try:  # pragma: no cover - heavy path
            import torch  # type: ignore
            inputs = self._tok(prompt, return_tensors="pt").to(self._device)
            with torch.no_grad():
                out = self._model.generate(
                    **inputs, max_new_tokens=int(max_tokens),
                    do_sample=temperature > 0, temperature=max(0.01, float(temperature)),
                    pad_token_id=self._tok.pad_token_id)
            text = self._tok.decode(out[0][inputs["input_ids"].shape[1]:],
                                    skip_special_tokens=True)
            return text.strip()
        except Exception:
            return _stub_reply(prompt, self.base_model)


# Per-pool runner cache, keyed by the promoted checkpoint hash → hot-reload.
_RUNNERS: dict[str, tuple[str, PoolModelRunner]] = {}


def runner_for(ena, pool_id: str) -> tuple[PoolModelRunner, dict[str, Any]]:
    """Return a runner bound to the pool's current promoted checkpoint.

    Rebuilds (hot-reloads) whenever the promoted ``checkpoint_hash`` changes.
    """
    served = ena.pool.get_served_checkpoint(pool_id)
    ckpt = served["checkpoint_hash"]
    cached = _RUNNERS.get(pool_id)
    if cached is None or cached[0] != ckpt:
        runner = PoolModelRunner(served["base_model"], adapter_path=served.get("path"))
        _RUNNERS[pool_id] = (ckpt, runner)
    return _RUNNERS[pool_id][1], served


# ---------------------------------------------------------------------------
# OpenAI-compatible HTTP server
# ---------------------------------------------------------------------------

def make_handler(ena, pool_id: str, *, worker_id: str,
                 address: Optional[str] = None):
    """Build a BaseHTTPRequestHandler serving the pool's promoted model."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence default stderr logging
            return

        def _send(self, code: int, payload: Any) -> None:
            body = json.dumps(payload, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.send_header("access-control-allow-origin", "*")
            self.send_header("access-control-allow-headers", "content-type, authorization")
            self.send_header("access-control-allow-methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length).decode("utf-8")
            try:
                return json.loads(raw) if raw else {}
            except ValueError:
                return {}

        def do_OPTIONS(self):  # noqa: N802
            self._send(204, {})

        def do_GET(self):  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            try:
                if path == "/health":
                    return self._send(200, {"status": "ok", "service": "ena-serving",
                                            "pool_id": pool_id})
                if path == "/v1/models":
                    served = ena.pool.get_served_checkpoint(pool_id)
                    return self._send(200, {"object": "list", "data": [{
                        "id": served["model_id"], "object": "model",
                        "owned_by": "animica-pool", "base_model": served["base_model"],
                        "round": served["round"]}]})
                return self._send(404, {"error": "not found", "path": path})
            except Exception as exc:  # noqa: BLE001
                return self._send(400, {"error": str(exc)})

        def do_POST(self):  # noqa: N802
            path = urlparse(self.path).path.rstrip("/") or "/"
            body = self._body()
            try:
                if path == "/v1/chat/completions":
                    return self._chat(body)
                return self._send(404, {"error": "not found", "path": path})
            except Exception as exc:  # noqa: BLE001
                return self._send(400, {"error": str(exc)})

        def _chat(self, body: dict[str, Any]) -> None:
            runner, served = runner_for(ena, pool_id)
            prompt = messages_to_prompt(body.get("messages") or [])
            max_tokens = int(body.get("max_tokens") or 256)
            temperature = float(body.get("temperature") or 0.2)
            created = now_ts()
            t0 = time.perf_counter()
            text = runner.generate(prompt, max_tokens=max_tokens, temperature=temperature)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            pt, ct = approx_tokens(prompt), approx_tokens(text)
            # credit the server for tokens served, to the round whose checkpoint
            # was served (not the round currently training).
            try:
                ena.pool.record_served(pool_id, worker_id, ct, address=address,
                                       latency_ms=latency_ms,
                                       served_round=served.get("round"))
            except Exception:  # noqa: BLE001 - never fail a response on bookkeeping
                pass
            # verifiable, on-chain-ready settlement receipt for this served request
            receipt = build_serving_receipt(
                pool_id=pool_id, model_id=served["model_id"],
                requester=body.get("user"), provider_id=worker_id,
                prompt=prompt, output=text, tokens_in=pt, tokens_out=ct,
                chain_id=int(getattr(ena.cfg, "chain_id", 1) or 1),
                served_round=served.get("round"))
            self._send(200, {
                "id": "chatcmpl-" + new_uuid()[:24], "object": "chat.completion",
                "created": created, "model": served["model_id"],
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": pt, "completion_tokens": ct,
                          "total_tokens": pt + ct},
                "animica_receipt": {"receipt_hash": receipt["receipt_hash"],
                                    "model_id": receipt["model_id"],
                                    "provider_id": receipt["provider_id"],
                                    "served_round": receipt["served_round"]},
            })

    return Handler


def serve(ena, pool_id: str, *, host: str = "127.0.0.1", port: int = 8799,
          worker_id: str, address: Optional[str] = None) -> None:
    """Run the OpenAI-compatible serve-while-train server (blocking)."""
    # Fail fast if there's nothing to serve yet.
    ena.pool.get_served_checkpoint(pool_id)
    handler = make_handler(ena, pool_id, worker_id=worker_id, address=address)
    httpd = ThreadingHTTPServer((host, port), handler)
    served = ena.pool.get_served_checkpoint(pool_id)
    print(f"[ena-serve] pool {pool_id} model {served['model_id']} "
          f"on http://{host}:{port}/v1  (worker {worker_id})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
