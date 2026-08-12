"""Persist chat turns to the Animica DA layer so users can resume
conversations from any client (CLI, studio.animica.org, mobile) and so
the trained-on-our-own-traffic training pipeline has a uniform source
of truth.

Design at a glance
------------------
- Each (wallet_address, thread_id) maps deterministically to a single
  *integer namespace* on the DA layer via SHA-256 truncation. Both ends
  of the conversation compute the same integer without coordinating, so
  CLI and web can find each other's threads with no central index.
- Each turn is one blob in that namespace. The blob payload is a JSON
  envelope (`schema: "animica.chat.turn.v1"`) carrying role, content,
  timestamp, sequence, provider attribution, cost, etc. Per-blob
  metadata duplicates the routing keys (`thread_id`, `wallet`, `seq`)
  so the trainer / explorer can filter without downloading bodies.
- A small local index at ``~/.animica/chat_threads.json`` keeps the
  list of *this client's* threads for the resume picker. The DA layer
  is the source of truth for content; the local file is just a cache so
  ``animica chat --list-threads`` doesn't have to scan every namespace.
- Encrypted threads (opt-in, per-thread) set ``encrypted: true`` and
  carry an AEAD nonce in the metadata; the trainer skips those blobs.

This module is deliberately RPC-only: it makes no assumptions about
which DA backend the node runs (sqlite store, NMT proof index, etc.),
and it works equally well from inside the CLI, the chat-animica
Next.js route, or a one-shot subprocess.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx


TURN_SCHEMA = "animica.chat.turn.v1"
THREAD_INDEX_PATH = "~/.animica/chat_threads.json"

# The DA layer's namespace width is a consensus constant: NAMESPACE_BITS = 32
# (da/nmt/codec.py, da/nmt/namespace.py). This used to mask to 56 bits on the
# reasoning that 63 bits "fits in a signed int64" — which is true and irrelevant,
# because the node rejects anything above 2**32-1:
#
#     da.putBlob RPC error -32602: namespace id 24735062580947889
#     exceeds maximum 4294967295 (NAMESPACE_BITS=32)
#
# So EVERY turn failed to persist, and with it `/threads` and `/resume` — the
# whole cross-device session-resume feature — could never work. Nothing needs
# migrating: no thread has ever been written successfully.
#
# 32 bits means a collision between two (wallet, thread) pairs is possible at
# around 2**16 threads per the birthday bound, which is fine here: the blob
# payload records its own wallet and thread_id, so a reader filters an unrelated
# turn out rather than mistaking it for its own.
_NS_BITS = 32
_NS_MASK = (1 << _NS_BITS) - 1


# --------------------------------------------------------------------------- #
# Namespace derivation                                                        #
# --------------------------------------------------------------------------- #


def derive_namespace(wallet_address: str, thread_id: str) -> int:
    """SHA-256 truncated to 56 bits. Deterministic — CLI and web compute
    the same integer for the same (wallet, thread) pair so they can
    discover each other's writes without an index server.
    """
    if not wallet_address:
        raise ValueError("wallet_address is required")
    if not thread_id:
        raise ValueError("thread_id is required")
    h = hashlib.sha256()
    h.update(b"animica.chat.v1\x00")
    h.update(wallet_address.encode("utf-8"))
    h.update(b"\x00")
    h.update(thread_id.encode("utf-8"))
    return int.from_bytes(h.digest()[:8], "big") & _NS_MASK


def new_thread_id() -> str:
    """Short, URL-safe thread identifier. uuid4 hex first 16 chars is
    enough — collisions inside a single wallet are ~zero, and the
    namespace is already wallet-scoped so two different wallets sharing
    a thread_id end up in different DA namespaces anyway."""
    return uuid.uuid4().hex[:16]


# --------------------------------------------------------------------------- #
# RPC helpers                                                                 #
# --------------------------------------------------------------------------- #


class ChatPersistError(RuntimeError):
    pass


def _rpc(rpc_url: str, method: str, params: Any, *, timeout: float = 15.0) -> Any:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        r = httpx.post(rpc_url, json=body, timeout=timeout)
    except httpx.HTTPError as exc:
        raise ChatPersistError(f"{method} transport failed: {exc}") from exc
    if r.status_code >= 400:
        # The mainnet edge sometimes serves an nginx HTML page on misroute;
        # the studio chat used to surface that as a V8 "Unexpected token '<'"
        # error. Give callers a clear string instead of letting json() crash.
        body_preview = (r.text or "").strip().replace("\n", " ")[:200]
        raise ChatPersistError(
            f"{method} HTTP {r.status_code} from {rpc_url} — {body_preview}"
        )
    try:
        payload = r.json()
    except ValueError as exc:
        body_preview = (r.text or "").strip().replace("\n", " ")[:200]
        raise ChatPersistError(
            f"{method} non-JSON response from {rpc_url} — {body_preview}"
        ) from exc
    if isinstance(payload, dict) and payload.get("error"):
        err = payload["error"]
        raise ChatPersistError(f"{method} RPC error {err.get('code')}: {err.get('message')}")
    return payload.get("result") if isinstance(payload, dict) else payload


# --------------------------------------------------------------------------- #
# Public turn model                                                           #
# --------------------------------------------------------------------------- #


@dataclass
class Turn:
    role: str               # "user" | "assistant" | "system"
    content: str
    ts: int                 # unix seconds
    sequence: int           # monotonically increasing within a thread
    thread_id: str
    wallet_address: str
    provider: Optional[str] = None
    tier: Optional[str] = None
    cost_animica: Optional[float] = None
    latency_ms: Optional[int] = None
    encrypted: bool = False
    extra: dict = field(default_factory=dict)

    def to_envelope(self) -> dict:
        env: dict[str, Any] = {
            "schema": TURN_SCHEMA,
            "thread_id": self.thread_id,
            "wallet": self.wallet_address,
            "role": self.role,
            "content": self.content,
            "ts": self.ts,
            "sequence": self.sequence,
        }
        for k in ("provider", "tier", "cost_animica", "latency_ms"):
            v = getattr(self, k)
            if v is not None:
                env[k] = v
        if self.encrypted:
            env["encrypted"] = True
        if self.extra:
            env["extra"] = self.extra
        return env

    @classmethod
    def from_envelope(cls, env: dict) -> "Turn":
        if env.get("schema") != TURN_SCHEMA:
            raise ChatPersistError(f"unknown chat envelope schema: {env.get('schema')!r}")
        return cls(
            role=str(env.get("role") or ""),
            content=str(env.get("content") or ""),
            ts=int(env.get("ts") or 0),
            sequence=int(env.get("sequence") or 0),
            thread_id=str(env.get("thread_id") or ""),
            wallet_address=str(env.get("wallet") or ""),
            provider=env.get("provider"),
            tier=env.get("tier"),
            cost_animica=env.get("cost_animica"),
            latency_ms=env.get("latency_ms"),
            encrypted=bool(env.get("encrypted", False)),
            extra=dict(env.get("extra") or {}),
        )


@dataclass
class ThreadInfo:
    thread_id: str
    wallet_address: str
    namespace: int
    title: str = ""
    first_ts: int = 0
    last_ts: int = 0
    turn_count: int = 0


# --------------------------------------------------------------------------- #
# Writes                                                                      #
# --------------------------------------------------------------------------- #


def write_turn(rpc_url: str, turn: Turn) -> str:
    """Persist a single turn. Returns the DA blob_id (content hash).

    Failures are non-fatal at the call site by convention — callers
    catch ChatPersistError and continue the chat even if persistence
    fails, since the user already paid for the inference.
    """
    namespace = derive_namespace(turn.wallet_address, turn.thread_id)
    body = json.dumps(turn.to_envelope(), separators=(",", ":")).encode("utf-8")
    metadata = {
        "content_type": "application/json",
        "kind": TURN_SCHEMA,
        "thread_id": turn.thread_id,
        "wallet": turn.wallet_address,
        "sequence": turn.sequence,
        "role": turn.role,
        "ts": turn.ts,
    }
    if turn.encrypted:
        metadata["encrypted"] = True
    result = _rpc(
        rpc_url,
        "da.putBlob",
        {
            "bytes": base64.b64encode(body).decode("ascii"),
            "namespace": namespace,
            "metadata": metadata,
        },
    )
    if not isinstance(result, dict):
        raise ChatPersistError(f"da.putBlob returned non-dict: {result!r}")
    blob_id = result.get("blob_id")
    if not blob_id:
        raise ChatPersistError(f"da.putBlob returned no blob_id: {result!r}")
    return str(blob_id)


# --------------------------------------------------------------------------- #
# Reads                                                                       #
# --------------------------------------------------------------------------- #


def _decode_blob_to_envelope(payload: Any) -> Optional[dict]:
    """da.getBlob returns either a raw bytes-ish string or a dict with
    ``data`` (base64). Normalize and JSON-decode."""
    if isinstance(payload, dict):
        raw = payload.get("data") or payload.get("bytes")
        if raw is None:
            return None
        try:
            raw_bytes = base64.b64decode(raw)
        except Exception:
            return None
    elif isinstance(payload, str):
        try:
            raw_bytes = base64.b64decode(payload)
        except Exception:
            return None
    else:
        return None
    try:
        env = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        return None
    return env if isinstance(env, dict) else None


def read_thread(rpc_url: str, wallet_address: str, thread_id: str,
                *, limit: int = 500) -> list[Turn]:
    """Fetch every turn in a thread, oldest-first. Returns [] if the
    thread doesn't exist or the namespace is empty."""
    ns = derive_namespace(wallet_address, thread_id)
    try:
        listing = _rpc(rpc_url, "da.listCommitments",
                       {"namespace": ns, "limit": limit})
    except ChatPersistError:
        # Empty namespaces commonly raise "not found" on some node builds;
        # treat that as an empty thread rather than a hard error.
        return []
    items = (listing or {}).get("items") or []
    turns: list[Turn] = []
    for item in items:
        blob_id = item.get("blob_id")
        if not blob_id:
            continue
        try:
            blob = _rpc(rpc_url, "da.getBlob", {"blob_id": blob_id})
        except ChatPersistError:
            continue
        env = _decode_blob_to_envelope(blob)
        if not env:
            continue
        try:
            turn = Turn.from_envelope(env)
        except ChatPersistError:
            continue
        # A namespace is 32 bits (the DA consensus width), so two distinct
        # (wallet, thread) pairs CAN land in the same one — around 2**16 threads
        # by the birthday bound. Without this check a collision would splice a
        # stranger's conversation into your transcript, which is both wrong and a
        # disclosure. The blob carries its own wallet and thread_id, so the
        # namespace is a lookup hint and these two fields are the actual identity.
        if turn.thread_id != thread_id:
            continue
        if turn.wallet_address and turn.wallet_address != wallet_address:
            continue
        turns.append(turn)
    turns.sort(key=lambda t: (t.sequence, t.ts))
    return turns


# --------------------------------------------------------------------------- #
# Local thread index                                                          #
# --------------------------------------------------------------------------- #
#
# The DA layer is the source of truth for *content*, but enumerating
# "all threads for this wallet" against an integer-keyed namespace table
# would require scanning every namespace and probing for our schema. To
# avoid that on every `--list-threads`, each client caches a small index
# of the threads it has seen / written to.
#
# The cache is best-effort: if it's deleted the only loss is the resume
# picker UI, the DA blobs are still recoverable by anyone who knows the
# (wallet, thread_id) pair.


def _index_path() -> Path:
    return Path(os.path.expanduser(THREAD_INDEX_PATH))


def _load_index() -> dict[str, list[dict]]:
    p = _index_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _save_index(data: dict[str, list[dict]]) -> None:
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def remember_thread(wallet_address: str, info: ThreadInfo) -> None:
    """Upsert a thread entry into the local index. Idempotent."""
    if not wallet_address:
        return
    idx = _load_index()
    bucket = list(idx.get(wallet_address) or [])
    found = False
    now = int(time.time())
    for entry in bucket:
        if entry.get("thread_id") == info.thread_id:
            entry["last_ts"] = max(int(entry.get("last_ts") or 0), info.last_ts or now)
            entry["turn_count"] = info.turn_count or entry.get("turn_count", 0) + 1
            if info.title:
                entry["title"] = info.title
            entry["namespace"] = info.namespace
            found = True
            break
    if not found:
        bucket.append({
            "thread_id": info.thread_id,
            "namespace": info.namespace,
            "title": info.title,
            "first_ts": info.first_ts or now,
            "last_ts": info.last_ts or now,
            "turn_count": info.turn_count,
        })
    bucket.sort(key=lambda e: -int(e.get("last_ts") or 0))
    idx[wallet_address] = bucket
    _save_index(idx)


def list_threads_local(wallet_address: str) -> list[ThreadInfo]:
    """Return the locally-cached thread list for a wallet, newest-first."""
    if not wallet_address:
        return []
    idx = _load_index()
    out: list[ThreadInfo] = []
    for entry in idx.get(wallet_address) or []:
        out.append(ThreadInfo(
            thread_id=str(entry.get("thread_id") or ""),
            wallet_address=wallet_address,
            namespace=int(entry.get("namespace") or 0),
            title=str(entry.get("title") or ""),
            first_ts=int(entry.get("first_ts") or 0),
            last_ts=int(entry.get("last_ts") or 0),
            turn_count=int(entry.get("turn_count") or 0),
        ))
    return out


# --------------------------------------------------------------------------- #
# Convenience: append a (user, assistant) pair                                #
# --------------------------------------------------------------------------- #


def append_exchange(
    rpc_url: str,
    *,
    wallet_address: str,
    thread_id: str,
    user_text: str,
    assistant_text: str,
    next_sequence: int,
    provider: Optional[str] = None,
    tier: Optional[str] = None,
    cost_animica: Optional[float] = None,
    latency_ms: Optional[int] = None,
    title: str = "",
) -> tuple[Turn, Turn]:
    """Write a user + assistant turn pair and bump the local index.

    Designed for the studio / CLI hot path: pass it the strings and
    sequence number after each settled inference and don't worry about
    the rest. Raises ChatPersistError only on the user-turn write; the
    assistant-turn failure is logged into the returned object's extra
    so a partial state is still recoverable.
    """
    now = int(time.time())
    user_turn = Turn(
        role="user",
        content=user_text,
        ts=now,
        sequence=next_sequence,
        thread_id=thread_id,
        wallet_address=wallet_address,
    )
    write_turn(rpc_url, user_turn)
    assistant_turn = Turn(
        role="assistant",
        content=assistant_text,
        ts=int(time.time()),
        sequence=next_sequence + 1,
        thread_id=thread_id,
        wallet_address=wallet_address,
        provider=provider,
        tier=tier,
        cost_animica=cost_animica,
        latency_ms=latency_ms,
    )
    try:
        write_turn(rpc_url, assistant_turn)
    except ChatPersistError as exc:
        assistant_turn.extra["persist_error"] = str(exc)

    remember_thread(wallet_address, ThreadInfo(
        thread_id=thread_id,
        wallet_address=wallet_address,
        namespace=derive_namespace(wallet_address, thread_id),
        title=title or (user_text[:60].strip() if next_sequence == 0 else ""),
        first_ts=user_turn.ts,
        last_ts=assistant_turn.ts,
        turn_count=next_sequence + 2,
    ))
    return user_turn, assistant_turn


__all__ = [
    "TURN_SCHEMA",
    "ChatPersistError",
    "Turn",
    "ThreadInfo",
    "derive_namespace",
    "new_thread_id",
    "write_turn",
    "read_thread",
    "remember_thread",
    "list_threads_local",
    "append_exchange",
]
