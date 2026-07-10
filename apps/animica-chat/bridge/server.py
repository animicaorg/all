"""Animica Chat ↔ AICF bridge.

Exposes an OpenAI-compatible HTTP surface (/v1/chat/completions,
/v1/models) and translates each request into an on-chain AICF job
via agent_runtime.providers.DistributedAICFProvider. The chain's
existing miners serve the inference and get paid in ANM; the chat
server (animica-chat-server) calls this bridge instead of OpenAI.

Why bridge instead of using aicf-api directly:
- aicf-api on :8099 keeps its own provider registry, separate from the
  chain's AICF workers. Without a registered model node it falls back
  to a first-party stub, which is what the user sees right now.
- The 15 AICF workers already running register against the chain RPC
  (port 8545) via aicf.workerRegister. The chain queues jobs and the
  workers pick them up. DistributedAICFProvider is the canonical
  Python client for that flow; it handles wallet payment signing.

Single global provider instance is created at startup; concurrent
requests share it. The underlying AICFClient is httpx.Client-based
and threadsafe for the calls we make.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any, AsyncGenerator, Iterable, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agent_runtime.config import load_config
from agent_runtime.errors import (
    AICFError,
    AgentRuntimeError,
    ProviderUnavailable,
    WalletError,
)
from agent_runtime.providers import DistributedAICFProvider, TurnRequest


log = logging.getLogger("animica-chat-bridge")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

DEFAULT_MODEL = os.environ.get("BRIDGE_DEFAULT_MODEL", "animica-chat")
DEFAULT_TIER = os.environ.get("BRIDGE_DEFAULT_TIER", "small")

app = FastAPI(title="Animica Chat AICF Bridge", version="0.1.0")


# ---------------------------------------------------------------------------
# Lazy global provider
# ---------------------------------------------------------------------------

_provider: Optional[DistributedAICFProvider] = None


def _get_provider() -> DistributedAICFProvider:
    global _provider
    if _provider is not None:
        return _provider
    cfg = load_config()
    network = os.environ.get("ANIMICA_NETWORK", "mainnet")
    rpc_url = (
        os.environ.get("ANIMICA_RPC_URL")
        or cfg.integration["aicf"]["endpoint"].get(network)
        or cfg.integration["aicf"]["endpoint"]["mainnet"]
    )
    wallet_label = os.environ.get("ANIMICA_BRIDGE_WALLET_LABEL", "aicf")
    wallet_path = os.environ.get("ANIMICA_BRIDGE_WALLET_PATH") or None
    # Stretch the AICF stream timeout well past the node's worker-claim
    # grace (300s by default) so the bridge sees the stub fallback even
    # when no real worker picks up. Without this the bridge's
    # AICFClient.stream raises TIMEOUT right at the grace boundary and
    # nothing reaches the chat client.
    bridge_timeout = float(os.environ.get("BRIDGE_AICF_TIMEOUT_S", "600"))
    cfg.integration["aicf"]["job_submit"]["timeout_sec"] = bridge_timeout
    log.info(
        "bridge starting: rpc=%s wallet_label=%s aicf_timeout=%ss",
        rpc_url, wallet_label, bridge_timeout,
    )
    _provider = DistributedAICFProvider(
        cfg=cfg,
        rpc_url=rpc_url,
        wallet_path=wallet_path,
        wallet_label=wallet_label,
    )
    # The chain accepts ONLY ml_dsa_65 (0x1003) signatures. A legacy
    # sphincs_shake_128s wallet (the historical default label "aicf") cannot sign
    # on mainnet — its only backend is the pure-Python fallback, which is disabled
    # — so every AICF payment fails with a cryptic "Pure-Python PQ fallbacks are
    # disabled". Surface the real cause loudly at startup instead of at request time.
    try:
        _wi = _provider._wallet_info()
        if _wi.scheme and _wi.scheme != "ml_dsa_65":
            log.error(
                "bridge wallet %r uses scheme %r, which CANNOT sign on this network "
                "(only ml_dsa_65 is accepted) — AICF payments will fail. Point "
                "ANIMICA_BRIDGE_WALLET_LABEL at an ml_dsa_65 wallet, e.g. create one "
                "with `animica wallet create --label <name> --alg ml_dsa_65` and fund it.",
                wallet_label, _wi.scheme,
            )
    except Exception:  # noqa: BLE001 — advisory only; never block startup on it
        pass
    return _provider


# ---------------------------------------------------------------------------
# OpenAI request/response models
# ---------------------------------------------------------------------------


class OpenAIMessage(BaseModel):
    role: str
    content: Optional[str] = None
    # Present on assistant turns that called tools in a previous round.
    tool_calls: Optional[list[dict]] = None
    # Present on tool turns — identifies which call this is a result for.
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class OpenAIChatRequest(BaseModel):
    model: Optional[str] = None
    messages: list[OpenAIMessage]
    max_tokens: Optional[int] = None
    temperature: Optional[float] = 0.2
    top_p: Optional[float] = 0.95
    stop: Optional[list[str]] = None
    stream: Optional[bool] = False
    # OpenAI-format tools. The bridge serializes them into a textual
    # system-prompt suffix that instructs the worker model to emit
    # `<tool_call>{...}</tool_call>` blocks when it wants to call one;
    # the streaming path then parses those blocks back into OpenAI-format
    # `tool_calls` deltas so the chat-server's agent loop fires.
    tools: Optional[list[dict]] = None
    tool_choice: Optional[Any] = None
    # Animica-specific knobs ride through metadata so vanilla OpenAI
    # clients aren't disturbed if they're set or absent.
    tier: Optional[str] = None
    mode: Optional[str] = None
    stages: Optional[int] = None
    decode_mode: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flatten_messages(messages: list[OpenAIMessage]) -> tuple[str, list[dict[str, str]]]:
    """Split an OpenAI-style message list into (latest_user_prompt, history).

    AICF's job spec carries a single ``prompt`` string. We keep all but
    the last user message in ``history`` so the upstream agent can use
    it for context, and surface the final user turn as the prompt.

    Tool turns (role="tool", role="assistant" with tool_calls) are folded
    into history as readable role:"user"/"assistant" markers so the
    worker model — which only sees plain chat text — can follow the
    conversation. The worker doesn't speak tools natively; it only sees
    the textual rendering we produce here.
    """
    if not messages:
        return "", []
    system_prefix: list[str] = []
    history: list[dict[str, str]] = []
    final_prompt: Optional[str] = None
    for i, m in enumerate(messages):
        # 1. System messages: stash as prefix for the next user turn.
        if m.role == "system":
            if m.content:
                system_prefix.append(m.content)
            continue

        # 2. Render assistant tool-call turns as visible text so the
        # model sees its own prior calls when reasoning about the next
        # step.
        if m.role == "assistant" and m.tool_calls:
            blocks: list[str] = []
            if m.content:
                blocks.append(m.content)
            for tc in m.tool_calls:
                fn = (tc.get("function") or {})
                blocks.append(
                    "<tool_call>"
                    + json.dumps({
                        "name": fn.get("name"),
                        "arguments": _parse_maybe_json(fn.get("arguments")),
                    })
                    + "</tool_call>"
                )
            history.append({"role": "assistant", "content": "\n".join(blocks)})
            continue

        # 3. Tool results come back as role="tool"; surface them as
        # role="user" text marked with the tool name so the model can
        # condition on them.
        if m.role == "tool":
            name = m.name or "tool"
            history.append({
                "role": "user",
                "content": f"<tool_result name=\"{name}\">{m.content or ''}</tool_result>",
            })
            continue

        # 4. The final user message becomes the prompt; everything else
        # is plain history.
        if i == len(messages) - 1 and m.role == "user":
            final_prompt = m.content or ""
            continue
        history.append({"role": m.role, "content": m.content or ""})

    if final_prompt is None:
        final_prompt = messages[-1].content or ""
    full_prompt = "\n\n".join([*system_prefix, final_prompt]) if system_prefix else final_prompt
    return full_prompt, history


def _parse_maybe_json(value: Any) -> Any:
    """Best-effort JSON-decode for tool argument strings; pass through on
    failure so the model still sees something readable."""
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    return value


# Token markers we ask the model to emit when it wants to call a tool.
# Mirrors Qwen 2.5 / 3's native tool-call syntax so when a worker swaps
# in a model whose chat template already produces these tokens, the
# parser keeps working unchanged.
_TOOL_CALL_OPEN = "<tool_call>"
_TOOL_CALL_CLOSE = "</tool_call>"


def _format_tools_prompt(tools: list[dict]) -> str:
    """Render an OpenAI tool list as a system-prompt addendum the worker
    model can act on.

    Without this the AICF inference path has no idea tools exist — it
    just generates free-form chat. With it, a capable model emits one
    or more `<tool_call>{"name": "...", "arguments": {...}}</tool_call>`
    blocks which the bridge then converts back into OpenAI-format
    delta.tool_calls events on the SSE stream.
    """
    if not tools:
        return ""
    lines = [
        "You have access to the following tools. Use them when they would "
        "materially improve your answer. When you want to call a tool, emit "
        "a fenced JSON block of the form:",
        "",
        f"  {_TOOL_CALL_OPEN}",
        '  {"name": "<tool_name>", "arguments": {"arg1": "value1"}}',
        f"  {_TOOL_CALL_CLOSE}",
        "",
        "Emit one block per call. Do not invent tool names. Do not call a "
        "tool you don't need. After all tool calls (if any), continue your "
        "answer normally; the tool results will arrive on the next turn.",
        "",
        "Available tools:",
    ]
    for t in tools:
        fn = t.get("function") or t
        name = fn.get("name", "")
        desc = fn.get("description", "").strip().replace("\n", " ")
        params = fn.get("parameters") or {}
        props = (params.get("properties") or {})
        required = set(params.get("required") or [])
        arg_lines = []
        for k, schema in props.items():
            t_ = schema.get("type", "any")
            arg_desc = schema.get("description", "").strip().replace("\n", " ")
            marker = "*" if k in required else " "
            arg_lines.append(f"      - {marker} {k} ({t_}): {arg_desc}")
        lines.append(f"  - {name}: {desc}")
        if arg_lines:
            lines.append("    arguments:")
            lines.extend(arg_lines)
    return "\n".join(lines)


class _ToolCallStreamParser:
    """State machine that walks the worker's streamed text and yields
    content/tool-call events in OpenAI streaming-delta format.

    Workflow:
      buffer += chunk
      while we can extract a complete <tool_call>...</tool_call> block,
        emit a tool-call delta, drop the block from buffer
      else if we see a clear "no open tag in sight" prefix, emit it as
        a content delta and trim it from buffer
      remainder of buffer waits for the next chunk
    """

    def __init__(self) -> None:
        self._buf = ""
        self._tool_idx = 0

    def feed(self, chunk: str) -> list[dict]:
        """Returns a list of {kind: 'content'|'tool_call', ...} events."""
        events: list[dict] = []
        self._buf += chunk
        while True:
            open_at = self._buf.find(_TOOL_CALL_OPEN)
            if open_at == -1:
                # No tool-call opener anywhere; the last few chars might
                # still be a partial opener tag, so hold them back.
                hold = len(_TOOL_CALL_OPEN) - 1
                if len(self._buf) > hold:
                    flush_to = len(self._buf) - hold
                    text = self._buf[:flush_to]
                    self._buf = self._buf[flush_to:]
                    if text:
                        events.append({"kind": "content", "text": text})
                return events
            # Emit any content before the opener.
            if open_at > 0:
                text = self._buf[:open_at]
                self._buf = self._buf[open_at:]
                events.append({"kind": "content", "text": text})
            # We're now at the start of a (possibly incomplete) tool_call.
            close_at = self._buf.find(_TOOL_CALL_CLOSE)
            if close_at == -1:
                # Block isn't finished streaming yet.
                return events
            block = self._buf[len(_TOOL_CALL_OPEN):close_at].strip()
            self._buf = self._buf[close_at + len(_TOOL_CALL_CLOSE):]
            try:
                parsed = json.loads(block)
                name = str(parsed.get("name") or "")
                args = parsed.get("arguments")
            except (json.JSONDecodeError, AttributeError):
                # Malformed block: surface as content so the agent loop
                # at least sees what the model tried to say.
                events.append({
                    "kind": "content",
                    "text": _TOOL_CALL_OPEN + block + _TOOL_CALL_CLOSE,
                })
                continue
            if not name:
                events.append({
                    "kind": "content",
                    "text": _TOOL_CALL_OPEN + block + _TOOL_CALL_CLOSE,
                })
                continue
            events.append({
                "kind": "tool_call",
                "index": self._tool_idx,
                "id": f"call_{uuid.uuid4().hex[:10]}",
                "name": name,
                "arguments": json.dumps(args) if not isinstance(args, str) else args,
            })
            self._tool_idx += 1

    def flush(self) -> list[dict]:
        """Drain any remaining buffer as a final content event."""
        if self._buf:
            text = self._buf
            self._buf = ""
            return [{"kind": "content", "text": text}]
        return []


def _chunk_payload(chunk_id: str, model: str, delta: str, finish: Optional[str] = None) -> dict:
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": ({"content": delta} if delta else {}),
                "finish_reason": finish,
            }
        ],
    }


def _full_payload(resp_id: str, model: str, text: str, usage: dict) -> dict:
    return {
        "id": resp_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/v1/models")
async def list_models() -> JSONResponse:
    return JSONResponse(
        {
            "object": "list",
            "data": [
                {
                    "id": "animica-chat",
                    "object": "model",
                    "owned_by": "animica",
                    "description": "On-chain AICF chat — routed through registered miners.",
                },
                {
                    "id": "animica-chat-small",
                    "object": "model",
                    "owned_by": "animica",
                    "description": "Tier 'small' on the AICF network.",
                },
                {
                    "id": "animica-chat-flagship",
                    "object": "model",
                    "owned_by": "animica",
                    "description": "Tier 'flagship' on the AICF network.",
                },
            ],
        }
    )


# Static catalog of user-facing tiers. The chain itself uses the names
# free/standard/premium/elite; the UI prefers the flagship_agent
# nomenclature (tiny/small/flagship/large) so we expose both via the
# `chain_tier` field. "requires_pro" gates premium/elite behind a paid
# subscription. "available" is filled in at request time based on which
# chain tiers actually have a registered worker right now.
_TIER_CATALOG: list[dict] = [
    {
        "code": "tiny", "label": "Tiny",
        "description": "~0.5B params — fastest",
        "chain_tier": "standard",
        "requires_pro": False,
    },
    {
        "code": "small", "label": "Small",
        "description": "~1.5B params — balanced",
        "chain_tier": "standard",
        "requires_pro": False,
    },
    {
        "code": "flagship", "label": "Flagship",
        "description": "~7B params — highest quality",
        "chain_tier": "premium",
        "requires_pro": True,
    },
    {
        "code": "large", "label": "Large",
        "description": "~16B MoE — datacenter",
        "chain_tier": "elite",
        "requires_pro": True,
    },
]


_KNOWN_WORKER_WALLETS = [
    w.strip() for w in os.environ.get(
        "BRIDGE_KNOWN_WORKER_WALLETS",
        # Default to the live local worker; operators can extend this
        # via the env var as additional miners come online.
        "anim1zqpye0muk7etljd2fh7wxsh9y9027cq7dykj3de8u80s2mcnfp6qxecpunkth",
    ).split(",") if w.strip()
]


def _probe_chain_tier_availability() -> set[str]:
    """Return the set of chain tiers (free/standard/premium/elite) that
    currently have a registered, recently-seen worker.

    We can't enumerate workers globally — the chain only exposes
    `aicf.workerStatus` per address. The bridge polls a configured set
    of known wallet addresses (BRIDGE_KNOWN_WORKER_WALLETS); operators
    add wallets here as new miners join. Best-effort; failures degrade
    to "no workers known" which the UI surfaces by greying out tiers.
    """
    import httpx
    rpc_url = (
        os.environ.get("ANIMICA_RPC_URL")
        or "http://127.0.0.1:8545/rpc"
    )
    available: set[str] = set()
    now_ms = int(time.time() * 1000)
    fresh_window_ms = 5 * 60 * 1000    # treat as alive if seen in 5 min
    for addr in _KNOWN_WORKER_WALLETS:
        try:
            with httpx.Client(timeout=3.0) as client:
                resp = client.post(rpc_url, json={
                    "jsonrpc": "2.0", "id": 1,
                    "method": "aicf.workerStatus",
                    "params": {"address": addr},
                })
                data = resp.json().get("result") or {}
        except Exception:    # noqa: BLE001
            continue
        if not data.get("registered"):
            continue
        last_seen_raw = data.get("last_seen")
        if isinstance(last_seen_raw, (int, float)):
            # Timestamps are sometimes seconds (workerStatus) and
            # sometimes milliseconds (work registry). Normalize.
            last_seen_ms = (
                int(last_seen_raw) if last_seen_raw > 1e12
                else int(last_seen_raw * 1000)
            )
            if now_ms - last_seen_ms > fresh_window_ms:
                # Stale heartbeat — worker likely down. Skip.
                continue
        for tier in (data.get("tiers") or []):
            # Filter out the synthetic "pipeline" tier, which is a
            # capability flag, not a serving tier.
            if tier and tier != "pipeline":
                available.add(str(tier))
    return available


@app.get("/v1/tiers")
async def list_tiers() -> JSONResponse:
    chain_available = _probe_chain_tier_availability()
    out = []
    for spec in _TIER_CATALOG:
        out.append({
            **spec,
            "available": spec["chain_tier"] in chain_available,
        })
    return JSONResponse({"object": "list", "data": out})


@app.get("/healthz")
async def healthz() -> JSONResponse:
    try:
        prov = _get_provider()
        ok, reason = prov.is_available()
        return JSONResponse({"ok": ok, "reason": reason})
    except Exception as exc:    # noqa: BLE001
        return JSONResponse({"ok": False, "reason": str(exc)}, status_code=503)


def _resolve_tier(req_model: Optional[str], req_tier: Optional[str]) -> str:
    if req_tier:
        return req_tier
    if not req_model:
        return DEFAULT_TIER
    # animica-chat-small / animica-chat-flagship / animica-chat-tiny
    if req_model.startswith("animica-chat-"):
        return req_model[len("animica-chat-"):]
    return DEFAULT_TIER


@app.post("/v1/chat/completions")
async def chat_completions(req: OpenAIChatRequest, request: Request,
                           authorization: Optional[str] = Header(None)):
    # API key is accepted but not validated — the bridge is internal-only,
    # bound to 127.0.0.1. Anything passing through nginx upstream of the
    # chat-server already had its session enforced. Treat any non-empty
    # Authorization header as fine.
    del authorization

    try:
        provider = _get_provider()
    except Exception as exc:    # noqa: BLE001
        log.exception("provider init failed")
        raise HTTPException(status_code=500, detail=f"provider_init_failed: {exc}")

    prompt, history = _flatten_messages(req.messages)
    tier = _resolve_tier(req.model, req.tier)
    model_label = req.model or DEFAULT_MODEL

    # If the caller passed `tools`, glue an instruction block onto the
    # prompt so the worker model knows about them. The streamer below
    # will parse `<tool_call>{...}</tool_call>` blocks out of the model's
    # output and emit them as OpenAI-format `delta.tool_calls` events.
    if req.tools:
        tools_prompt = _format_tools_prompt(req.tools)
        if tools_prompt:
            prompt = tools_prompt + "\n\n---\n\n" + prompt

    turn = TurnRequest(
        prompt=prompt,
        tier_preferred=tier,
        history=history,
        max_output_tokens=req.max_tokens or 512,
        temperature=req.temperature if req.temperature is not None else 0.2,
        top_p=req.top_p if req.top_p is not None else 0.95,
        yolo=True,    # bridge is internal; skip the interactive cost prompt
        mode=req.mode,
        stages=req.stages,
        decode_mode=req.decode_mode,
    )

    if not req.stream:
        # Synchronous path — provider.serve() runs the full submit →
        # stream → settle round-trip on its own thread. Wrap with
        # run_in_executor so we don't block the asyncio loop.
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, provider.serve, turn)
        except (AICFError, WalletError, ProviderUnavailable, AgentRuntimeError) as exc:
            log.warning("provider.serve failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"upstream_error: {exc.message}")
        except Exception as exc:    # noqa: BLE001
            log.exception("provider.serve unexpected error")
            raise HTTPException(status_code=500, detail=f"internal_error: {exc}")
        usage = {
            "prompt_tokens": max(1, len(prompt) // 4),
            "completion_tokens": max(1, len(result.text) // 4),
            "total_tokens": max(2, (len(prompt) + len(result.text)) // 4),
        }
        return JSONResponse(_full_payload(
            f"chatcmpl-{uuid.uuid4().hex[:24]}",
            model_label,
            result.text,
            usage,
        ))

    # Streaming path. We marshal deltas via a queue so the
    # provider thread (which calls back synchronously) can hand chunks
    # to the asyncio generator producing the SSE response.
    async def streamer() -> AsyncGenerator[bytes, None]:
        chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        queue: asyncio.Queue[Optional[tuple[str, bool]]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def relay(text: str, is_final: bool) -> None:
            # provider calls this on its own thread.
            asyncio.run_coroutine_threadsafe(
                queue.put((text, is_final)), loop
            )

        turn.stream_callback = relay

        def run_in_thread() -> None:
            try:
                provider.serve(turn)
            except Exception as exc:    # noqa: BLE001
                log.exception("stream provider.serve crashed")
                err_chunk = {
                    "error": {
                        "message": str(exc),
                        "type": exc.__class__.__name__,
                    }
                }
                asyncio.run_coroutine_threadsafe(
                    queue.put((json.dumps(err_chunk), True)), loop
                )
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        # run_in_executor already returns a Future; don't wrap it in create_task.
        future = loop.run_in_executor(None, run_in_thread)

        parser = _ToolCallStreamParser() if req.tools else None

        def _emit_events(events: list[dict]) -> list[bytes]:
            """Convert parsed content/tool_call events to SSE bytes."""
            out: list[bytes] = []
            for ev in events:
                if ev["kind"] == "content":
                    payload = _chunk_payload(chunk_id, model_label, ev["text"])
                elif ev["kind"] == "tool_call":
                    payload = {
                        "id": chunk_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model_label,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": ev["index"],
                                            "id": ev["id"],
                                            "type": "function",
                                            "function": {
                                                "name": ev["name"],
                                                "arguments": ev["arguments"],
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": None,
                            }
                        ],
                    }
                else:
                    continue
                out.append(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
            return out

        try:
            sent_any = False
            saw_tool_call = False
            while True:
                item = await queue.get()
                if item is None:
                    break
                text, is_final = item
                if text:
                    sent_any = True
                    if parser is not None:
                        events = parser.feed(text)
                        for ev in events:
                            if ev["kind"] == "tool_call":
                                saw_tool_call = True
                        for chunk in _emit_events(events):
                            yield chunk
                    else:
                        payload = _chunk_payload(chunk_id, model_label, text)
                        yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")
                if is_final:
                    # Drain any buffered partial-content from the parser.
                    if parser is not None:
                        for chunk in _emit_events(parser.flush()):
                            yield chunk
                    finish_reason = "tool_calls" if saw_tool_call else "stop"
                    final = _chunk_payload(chunk_id, model_label, "", finish=finish_reason)
                    yield f"data: {json.dumps(final)}\n\n".encode("utf-8")
                    yield b"data: [DONE]\n\n"
                    break
            if not sent_any:
                # Make absolutely sure clients always see a frame.
                final = _chunk_payload(chunk_id, model_label, "", finish="stop")
                yield f"data: {json.dumps(final)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
        finally:
            if not future.done():
                future.cancel()

    return StreamingResponse(
        streamer(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def main() -> None:    # pragma: no cover — runtime entry
    import uvicorn
    port = int(os.environ.get("BRIDGE_PORT", "4600"))
    host = os.environ.get("BRIDGE_HOST", "127.0.0.1")
    log.info("listening on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
