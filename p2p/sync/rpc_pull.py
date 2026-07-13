"""
HTTP/RPC pull-sync fallback for pip-installed nodes
====================================================

This module pulls blocks from a configured bootstrap RPC endpoint and
imports them via the normal chain import path. It exists because:

  • Public P2P seeds can become unreachable or run protocol versions that
    don't AEAD-decrypt with the wheel's handshake (we've seen InvalidTag
    across all known mainnet seeds), and
  • The bootstrap HTTP RPC (https://rpc.animica.org/rpc for mainnet) is
    far more durable: it speaks plain JSON-RPC over HTTPS, so a fresh
    pip install can advance past genesis even when the P2P swarm is in a
    bad state.

The pull-sync is OFF by default. Enable it via env var:

    ANIMICA_RPC_SYNC_FALLBACK_URL=https://rpc.animica.org/rpc

When set, the RPC service spawns a background task at startup that:

  1. Polls `chain.getHead` against the URL.
  2. If `remote.height > local.height`, fetches each missing block via
     `debug.getRawBlock` (preferred — returns the canonical CBOR bytes)
     and imports it directly via `core.chain.block_import.import_block`.
     Falls back to `chain.getBlockByHeight` (with a JSON→Header
     reconstruction step) if the raw-block endpoint is unavailable.
  3. Sleeps and repeats.

`debug.getRawBlock` is strongly preferred: re-hashing a JSON-decoded
header risks dropping fields the original miner included (`extra`,
`workType`, the policy roots), which produces a header hash that does
not match the PoW target. Raw CBOR sidesteps the problem.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger("animica.p2p.rpc_pull")


def _hex_to_bytes(value: Any, default_len: int = 32) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("0x"):
            s = s[2:]
        if not s:
            return b"\x00" * default_len
        try:
            return bytes.fromhex(s)
        except ValueError:
            return b"\x00" * default_len
    return b"\x00" * default_len


def _flatten_header_dict(rpc_header: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert the RPC chain.getBlockByHeight header shape into the flat form
    `_dataclass_from_dict(Header, ...)` expects (top-level stateRoot etc.).
    """
    roots = rpc_header.get("roots") or {}
    out: Dict[str, Any] = dict(rpc_header)
    # Pull nested roots up
    for k in ("stateRoot", "txsRoot", "receiptsRoot", "proofsRoot", "daRoot"):
        if k in roots and k not in out:
            out[k] = roots[k]
        elif k in roots:
            # Prefer the nested value (RPC has canonical bytes there)
            out[k] = roots[k]
    # Convert hex hashes to bytes
    for key in (
        "parentHash",
        "stateRoot",
        "txsRoot",
        "receiptsRoot",
        "proofsRoot",
        "daRoot",
        "mixSeed",
        "poiesPolicyRoot",
        "pqAlgPolicyRoot",
    ):
        if key in out:
            out[key] = _hex_to_bytes(out[key])
    # Map RPC field aliases to canonical Header field names
    if "number" in out and "height" not in out:
        out["height"] = int(out["number"])
    if "chainId" in out:
        out["chainId"] = int(out["chainId"])
    if "thetaMicro" in out:
        out["thetaMicro"] = int(out["thetaMicro"])
    if "nonce" in out:
        try:
            out["nonce"] = int(out["nonce"])
        except (TypeError, ValueError):
            out["nonce"] = 0
    if "timestamp" in out:
        out["timestamp"] = int(out["timestamp"])
    if "workType" not in out:
        out["workType"] = int(rpc_header.get("workType") or 0)
    # Default policy roots to zeros if RPC omits them
    out.setdefault("poiesPolicyRoot", b"\x00" * 32)
    out.setdefault("pqAlgPolicyRoot", b"\x00" * 32)
    # The Header dataclass requires the `v` (version) field
    out.setdefault("v", 1)
    out.setdefault("extra", b"")
    return out


def _block_dict_from_rpc(rpc_block: Dict[str, Any]) -> Dict[str, Any]:
    """Turn an RPC `chain.getBlockByHeight` result into something
    `block_from_mapping` can consume."""
    header_src = rpc_block.get("header")
    if not isinstance(header_src, dict):
        # Some RPCs flatten the header into the top-level block — use the
        # block itself as the header source.
        header_src = rpc_block
    header_flat = _flatten_header_dict(header_src)
    txs = rpc_block.get("txs")
    if not isinstance(txs, list):
        txs = rpc_block.get("transactions") or []
    return {"header": header_flat, "txs": list(txs), "proofs": []}


async def _rpc_call(
    client: Any, url: str, method: str, params: Any, timeout: float = 15.0
) -> Any:
    """Send a JSON-RPC request via the shared httpx client."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    response = await client.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    if "error" in data and data["error"]:
        raise RuntimeError(f"RPC {method} error: {data['error']}")
    return data.get("result")


class RpcPullSync:
    """
    Background sync loop that pulls blocks from a bootstrap HTTP RPC.

    Args:
        url: Bootstrap RPC URL (e.g. https://rpc.animica.org/rpc).
        local_head: callable returning (height: int, head_hash: bytes).
        import_block: callable accepting a block dict; returns (accepted, reason).
        poll_interval_s: seconds between catch-up checks once at tip.
        max_batch_per_tick: maximum blocks to fetch+import per loop iteration.
    """

    def __init__(
        self,
        *,
        url: str,
        local_head,
        import_block,
        poll_interval_s: float = 10.0,
        max_batch_per_tick: int = 200,
    ) -> None:
        self._url = url.rstrip("/")
        self._local_head = local_head
        self._import_block = import_block
        self._poll_interval_s = max(1.0, float(poll_interval_s))
        self._max_batch = max(1, int(max_batch_per_tick))
        # Bound on how far back a reorg backfill will walk the winning branch
        # (mirrors core.chain.block_import DEFAULT_MAX_REORG_DEPTH / the same env).
        try:
            self._max_reorg_depth = max(1, int(os.environ.get("ANIMICA_MAX_REORG_DEPTH", "96")))
        except (TypeError, ValueError):
            self._max_reorg_depth = 96
        self._reorg_recoveries = 0
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="rpc_pull_sync")
        log.info(
            "rpc-pull sync started (url=%s interval=%.1fs batch=%d)",
            self._url,
            self._poll_interval_s,
            self._max_batch,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run(self) -> None:
        try:
            import httpx
        except Exception as exc:
            log.warning("rpc-pull sync disabled (httpx unavailable): %s", exc)
            return
        async with httpx.AsyncClient(timeout=20.0) as client:
            consecutive_failures = 0
            while not self._stop.is_set():
                try:
                    advanced = await self._tick(client)
                    consecutive_failures = 0
                except Exception as exc:
                    consecutive_failures += 1
                    # %r, not %s: many httpx/network exceptions stringify to ""
                    # which left operators staring at "tick failed:" with no
                    # cause during the 38728 outage. Include a traceback on the
                    # first failure of each streak.
                    log.warning(
                        "rpc-pull tick failed (consecutive=%d): %r",
                        consecutive_failures,
                        exc,
                        exc_info=consecutive_failures == 1,
                    )
                    advanced = False

                # Idle longer when at tip, shorter while still catching up
                wait = 0.25 if advanced else self._poll_interval_s
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=wait)
                except asyncio.TimeoutError:
                    continue

    async def _tick(self, client: Any) -> bool:
        head = await _rpc_call(client, self._url, "chain.getHead", {}, timeout=10.0)
        if not isinstance(head, dict):
            raise RuntimeError(
                f"chain.getHead returned non-dict ({type(head).__name__}); "
                "refusing to treat as remote tip"
            )
        raw_height = head.get("height")
        if raw_height is None:
            raw_height = head.get("number")
        if raw_height is None:
            raise RuntimeError(
                f"chain.getHead response missing height/number: {head!r}"
            )
        try:
            remote_height = int(raw_height)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"chain.getHead height is not an integer: {raw_height!r}"
            ) from exc
        if remote_height < 0:
            raise RuntimeError(
                f"chain.getHead returned negative height: {remote_height}"
            )
        local_height, _ = self._safe_local_head()
        if remote_height <= local_height:
            return False
        next_h = local_height + 1
        last_target = min(remote_height, next_h + self._max_batch - 1)
        log.info(
            "rpc-pull catching up: local=%d remote=%d batch=%d..%d",
            local_height,
            remote_height,
            next_h,
            last_target,
        )
        any_accepted = False
        for height in range(next_h, last_target + 1):
            if self._stop.is_set():
                break
            payload, fetch_err = await self._fetch_block(client, height)
            if payload is None:
                log.warning("rpc-pull fetch height=%d failed: %s", height, fetch_err)
                break
            try:
                accepted, reason = self._import_block(payload)
            except Exception as exc:
                log.warning(
                    "rpc-pull import height=%d crashed: %s",
                    height,
                    exc,
                    exc_info=True,
                )
                break
            if not accepted:
                if self._is_missing_parent(reason):
                    # The block at `height` builds on a parent we don't have —
                    # the network reorged and our local head is on the losing
                    # fork. Fetch the winning branch BY HASH, walk back to the
                    # common ancestor, and let the importer reorg + drain the
                    # buffered orphan(s). See _try_reorg_recovery.
                    recovered = await self._try_reorg_recovery(
                        client, height, payload, reason
                    )
                    if recovered:
                        any_accepted = True
                        # Head advanced via reorg + orphan cascade; recompute the
                        # catch-up window from the new local head on the next tick.
                        break
                log.warning(
                    "rpc-pull import height=%d rejected: %s",
                    height,
                    reason,
                )
                break
            any_accepted = True
        return any_accepted

    async def _fetch_block(
        self, client: Any, height: int
    ) -> Tuple[Optional[Any], Optional[str]]:
        """
        Fetch a block at `height`. Prefer `debug.getRawBlock` because it
        returns the canonical CBOR bytes — those go straight into
        `import_block` and skip the lossy JSON-header reconstruction
        that drops `extra` / `workType` and produces a header_hash that
        misses the PoW target.

        Falls back to `chain.getBlockByHeight` if the raw endpoint is
        unavailable or returns an unexpected shape.
        """
        # Skip the raw-block call after a confirmed unsupported response.
        if not getattr(self, "_raw_block_disabled", False):
            try:
                raw = await _rpc_call(
                    client,
                    self._url,
                    "debug.getRawBlock",
                    {"height": int(height)},
                    timeout=15.0,
                )
            except Exception as exc:
                msg = str(exc).lower()
                if "method not found" in msg or "-32601" in msg or "unknown" in msg:
                    self._raw_block_disabled = True
                    log.info(
                        "rpc-pull: debug.getRawBlock unavailable, falling back to JSON: %s",
                        exc,
                    )
                else:
                    return None, str(exc)
            else:
                if isinstance(raw, dict):
                    block_cbor_hex = raw.get("blockCbor") or raw.get("block_cbor")
                    if isinstance(block_cbor_hex, str) and block_cbor_hex:
                        s = block_cbor_hex
                        if s.startswith("0x") or s.startswith("0X"):
                            s = s[2:]
                        try:
                            return bytes.fromhex(s), None
                        except ValueError as exc:
                            return None, f"bad blockCbor hex: {exc}"
                # Unexpected shape — disable and fall through to JSON
                self._raw_block_disabled = True
                log.info(
                    "rpc-pull: debug.getRawBlock returned unexpected shape, falling back to JSON"
                )

        try:
            rpc_block = await _rpc_call(
                client,
                self._url,
                "chain.getBlockByHeight",
                {"height": int(height)},
                timeout=15.0,
            )
        except Exception as exc:
            return None, str(exc)
        if not isinstance(rpc_block, dict):
            return None, f"unexpected response type {type(rpc_block).__name__}"
        return _block_dict_from_rpc(rpc_block), None

    # ── reorg recovery via by-hash backfill ──────────────────────────────
    #
    # The plain catch-up loop only ever requests local_height + 1 BY HEIGHT.
    # When the network reorgs and our local head lands on the losing fork, the
    # next canonical block builds on a parent we don't have, the importer buffers
    # it as an orphan and returns "missing parent", and the loop stalls forever
    # (the upstream height index can even still point at the losing block, so
    # re-fetching by height never helps). Recovery: fetch the winning branch BY
    # HASH, walk back to the common ancestor already in our DB, and let the
    # importer's fork-choice + orphan cascade perform the actual reorg.

    @staticmethod
    def _norm_hash_hex(value: Any) -> Optional[str]:
        """Normalize a hash to lowercase 0x-prefixed hex, or None."""
        if isinstance(value, (bytes, bytearray)):
            return "0x" + bytes(value).hex()
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            if not s.lower().startswith("0x"):
                s = "0x" + s
            return s.lower()
        return None

    @staticmethod
    def _is_missing_parent(reason: Any) -> bool:
        s = str(reason or "").lower()
        return "missing parent" in s or "orphan" in s

    @classmethod
    def _extract_parent_hash_hex(cls, rpc_block: Dict[str, Any]) -> Optional[str]:
        """Read parentHash from a JSON block (getBlockByHeight/getBlockByHash)."""
        header = rpc_block.get("header")
        if not isinstance(header, dict):
            header = rpc_block
        return cls._norm_hash_hex(header.get("parentHash") or header.get("parent_hash"))

    @classmethod
    def _payload_parent_hash_hex(cls, payload: Any) -> Optional[str]:
        """Read parentHash from an already-fetched importable payload when it is
        the JSON-dict form (raw CBOR bytes carry no accessible parentHash)."""
        if isinstance(payload, dict):
            header = payload.get("header")
            if isinstance(header, dict):
                return cls._norm_hash_hex(header.get("parentHash"))
        return None

    async def _fetch_block_by_hash(
        self, client: Any, block_hash_hex: str
    ) -> Tuple[Optional[Any], Optional[str], Optional[str]]:
        """Fetch a block BY HASH for reorg backfill.

        Returns (importable_payload, parent_hash_hex, error). The JSON
        `chain.getBlockByHash` view is always fetched — it yields the parentHash
        needed to walk further back and doubles as an import fallback — while
        `debug.getRawBlock {blockHash}` is preferred for the payload because raw
        CBOR imports without the lossy JSON header reconstruction that can miss
        the PoW target.
        """
        try:
            rpc_block = await _rpc_call(
                client,
                self._url,
                "chain.getBlockByHash",
                {"blockHash": block_hash_hex},
                timeout=15.0,
            )
        except Exception as exc:
            return None, None, str(exc)
        if not isinstance(rpc_block, dict):
            return None, None, f"getBlockByHash returned {type(rpc_block).__name__}"
        parent_hex = self._extract_parent_hash_hex(rpc_block)

        raw_payload: Optional[bytes] = None
        if not getattr(self, "_raw_by_hash_disabled", False):
            try:
                raw = await _rpc_call(
                    client,
                    self._url,
                    "debug.getRawBlock",
                    {"blockHash": block_hash_hex},
                    timeout=15.0,
                )
            except Exception as exc:
                msg = str(exc).lower()
                if "method not found" in msg or "-32601" in msg or "unknown" in msg:
                    self._raw_by_hash_disabled = True
                # else: transient — fall back to the JSON payload for this block
            else:
                if isinstance(raw, dict):
                    cbor_hex = raw.get("blockCbor") or raw.get("block_cbor")
                    if isinstance(cbor_hex, str) and cbor_hex:
                        s = cbor_hex[2:] if cbor_hex[:2].lower() == "0x" else cbor_hex
                        try:
                            raw_payload = bytes.fromhex(s)
                        except ValueError:
                            raw_payload = None

        payload = raw_payload if raw_payload is not None else _block_dict_from_rpc(rpc_block)
        return payload, parent_hex, None

    async def _fetch_parent_hash_of_height(
        self, client: Any, height: int
    ) -> Optional[str]:
        """Read the parentHash of the canonical block at `height` from the
        upstream JSON view — seeds the reorg walk when the rejected payload was
        raw CBOR (which carries no accessible parentHash)."""
        try:
            rpc_block = await _rpc_call(
                client,
                self._url,
                "chain.getBlockByHeight",
                {"height": int(height)},
                timeout=15.0,
            )
        except Exception as exc:
            log.debug("rpc-pull reorg: getBlockByHeight(%d) failed: %s", height, exc)
            return None
        if not isinstance(rpc_block, dict):
            return None
        return self._extract_parent_hash_hex(rpc_block)

    async def _backfill_reorg(
        self, client: Any, first_parent_hex: str
    ) -> Tuple[bool, int, Optional[str]]:
        """Walk the winning branch backward BY HASH from `first_parent_hex` until
        a block connects to our DB (its parent is already stored — the common
        ancestor). Each winning block that is itself an orphan gets buffered; the
        importer's _process_orphans cascade then re-imports the whole forward
        chain (including the block rpc-pull rejected) and performs the reorg.
        Returns (connected, imported_count, reason)."""
        imported = 0
        current: Optional[str] = first_parent_hex
        seen: set[str] = set()
        for _ in range(self._max_reorg_depth):
            if not current:
                return False, imported, "empty parent hash in reorg walk"
            if current in seen:
                return False, imported, "cycle in reorg walk"
            seen.add(current)
            payload, next_parent, err = await self._fetch_block_by_hash(client, current)
            if payload is None:
                return False, imported, f"fetch {current[:18]} failed: {err}"
            try:
                accepted, reason = self._import_block(payload)
            except Exception as exc:
                return False, imported, f"import {current[:18]} crashed: {exc}"
            imported += 1
            if accepted:
                # Connected to the common ancestor. The orphan cascade drains the
                # winning branch forward and triggers the reorg.
                return True, imported, None
            if self._is_missing_parent(reason):
                current = next_parent
                continue
            return False, imported, f"{current[:18]} rejected: {reason}"
        return False, imported, f"reorg depth limit {self._max_reorg_depth} exceeded"

    async def _try_reorg_recovery(
        self, client: Any, height: int, payload: Any, reason: Any
    ) -> bool:
        parent_hex = self._payload_parent_hash_hex(payload)
        if parent_hex is None:
            parent_hex = await self._fetch_parent_hash_of_height(client, height)
        if not parent_hex:
            log.warning(
                "rpc-pull reorg: could not determine parentHash at height=%d (%s)",
                height,
                reason,
            )
            return False
        before_h, before_hash = self._safe_local_head()
        connected, imported, walk_reason = await self._backfill_reorg(client, parent_hex)
        after_h, after_hash = self._safe_local_head()
        if connected and (after_h > before_h or after_hash != before_hash):
            self._reorg_recoveries += 1
            log.info(
                "rpc-pull reorg recovery ok at height=%d: backfilled %d winning "
                "block(s), local %d->%d (total recoveries=%d)",
                height,
                imported,
                before_h,
                after_h,
                self._reorg_recoveries,
            )
            return True
        log.warning(
            "rpc-pull reorg recovery failed at height=%d "
            "(imported=%d connected=%s): %s",
            height,
            imported,
            connected,
            walk_reason,
        )
        return False

    def _safe_local_head(self) -> Tuple[int, bytes]:
        try:
            head = self._local_head()
            if isinstance(head, tuple) and len(head) >= 2:
                return int(head[0] or 0), bytes(head[1] or b"")
        except Exception as exc:
            log.debug("rpc-pull local_head lookup failed: %s", exc)
        return 0, b""


def create_from_env(local_head, import_block) -> Optional[RpcPullSync]:
    """
    Construct an `RpcPullSync` from environment variables, or return None
    if the fallback isn't configured.

    Env:
      ANIMICA_RPC_SYNC_FALLBACK_URL  (required to enable)
      ANIMICA_RPC_SYNC_INTERVAL_S    (default: 10)
      ANIMICA_RPC_SYNC_BATCH         (default: 200)
    """
    url = os.environ.get("ANIMICA_RPC_SYNC_FALLBACK_URL") or os.environ.get(
        "ANIMICA_BOOTSTRAP_SYNC_URL"
    )
    if not url:
        return None
    try:
        interval = float(os.environ.get("ANIMICA_RPC_SYNC_INTERVAL_S", "10"))
    except ValueError:
        interval = 10.0
    try:
        batch = int(os.environ.get("ANIMICA_RPC_SYNC_BATCH", "200"))
    except ValueError:
        batch = 200
    return RpcPullSync(
        url=url.strip(),
        local_head=local_head,
        import_block=import_block,
        poll_interval_s=interval,
        max_batch_per_tick=batch,
    )


__all__ = ["RpcPullSync", "create_from_env", "_block_dict_from_rpc", "_flatten_header_dict"]
