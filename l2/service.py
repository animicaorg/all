"""Async node service around :class:`l2.node.L2Node`.

The sequencer (:mod:`l2.sequencer`) is deliberately synchronous — it is driven
with ``submit()`` + ``tick()``. This module is the asyncio driver that embeds an
L2 node inside the L1 RPC process (wired from ``rpc/deps.py`` when
``ANIMICA_L2_ENABLE=1``):

* **fast tick loop** — calls ``sequencer.tick()`` every
  ``config.tick_interval_ms`` so batches seal on the closure policy without any
  external caller, and publishes ``l2NewBatches`` / ``l2SoftConfirmations``
  WebSocket events for each sealed batch (see ``rpc/ws.py``).
* **slow loop (~1s)** — refreshes the rolling TPS gauges in
  :data:`l2.metrics.L2_METRICS` from counter deltas, admits DEPOSIT_CLAIM txs
  for newly finalized L1 deposits (``sequencer.enqueue_finalized_deposits()``),
  runs the L1 deposit indexer, and — when ``config.settlement_enabled`` — the
  settlement submitter.

L1 anchoring interface (10.0.0)
-------------------------------
Real settlement publishes each proven batch's state-root + DA commitment as an
ordinary L1 transaction **to the bridge address** carrying the ``ANML2C1``
magic memo (below ``FORK_L2_ANCHOR_HEIGHT`` the memo is opaque to L1 consensus;
at/above it full nodes validate it — see :mod:`l2.bridge`). Building that L1 tx
requires the sequencer's funded L1 signing key, which ships with the
FORK_L2_ANCHOR activation tooling. Until then :meth:`L2Service._settle_proven_batches`
wires the full interface — it assembles the canonical anchor payload and calls
the L1 RPC over ``aiohttp`` at ``config.l1_rpc_url`` — but degrades safely
(logged, never raised) when the L1 node is unreachable or does not yet expose
the anchoring method, and then advances local tx records PROVEN → L1_SUBMITTED.

Deposit indexing
----------------
Deposits are ordinary L1 transfers to the bridge address whose ``data`` memo is
``ANML2D1 || beneficiary(32)``. :meth:`L2Service._index_l1_deposits` scans L1
blocks via JSON-RPC (``chain_getHead`` / ``chain_getBlockByNumber``), feeds
matches to ``bridge.observe_deposit(...)`` and advances confirmation tiers with
``bridge.update_l1_head(...)``. Every network call is guarded: an unreachable
L1 never crashes the L2.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional, Set

import aiohttp

from .constants import L1_FINALITY_DEPTH, TxStatus
from .metrics import L2_METRICS
from .node import L2Node, get_l2_node

log = logging.getLogger("animica.l2.service")

# Magic memo prefixes carried in the L1 tx ``data`` field (see l2/bridge.py).
DEPOSIT_MEMO_MAGIC = b"ANML2D1"  # deposit:  magic || beneficiary(32)
ANCHOR_MEMO_MAGIC = b"ANML2C1"  # anchoring: magic || anchor payload (below)

_ADDR_LEN = 32

# counter key -> gauge key for the rolling TPS gauges.
_TPS_MAP = {
    "ingress_total": "ingress_tps",
    "executed_total": "executed_tps",
    "soft_confirmed_total": "soft_confirmed_tps",
    "settled_total": "settled_tps",
}

# Cap the number of txids carried in one l2SoftConfirmations frame so a huge
# batch cannot produce a multi-megabyte WS frame.
_SOFT_CONFIRM_TXID_CAP = 1024


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return int(v, 0)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _norm_hex(value: Optional[str]) -> str:
    """Normalize an address/hash string for comparison: lowercase, no 0x."""
    if not value or not isinstance(value, str):
        return ""
    v = value.strip().lower()
    if v.startswith("0x"):
        v = v[2:]
    return v


def _hex_to_bytes(value: Optional[str]) -> Optional[bytes]:
    v = _norm_hex(value)
    if not v:
        return None
    try:
        return bytes.fromhex(v)
    except ValueError:
        return None


def _extract_height(head: Any) -> Optional[int]:
    """Pull an integer height out of a chain_getHead result (dict or scalar)."""
    if head is None:
        return None
    if isinstance(head, int):
        return head
    if isinstance(head, dict):
        for key in ("height", "number", "blockNumber", "canonicalHeight"):
            raw = head.get(key)
            if raw is None:
                continue
            try:
                if isinstance(raw, str):
                    return int(raw, 0)
                return int(raw)
            except (TypeError, ValueError):
                continue
    return None


class L2Service:
    """Long-running asyncio wrapper around an :class:`l2.node.L2Node`.

    Follows the repo's service shape (see ``p2p/core_p2p/service.py``):
    ``asyncio.create_task`` per loop, ``CancelledError`` swallowed at loop exit,
    a ``_running`` flag, and idempotent ``start()`` / ``stop()``.
    """

    def __init__(self, node: Optional[L2Node] = None) -> None:
        self.node: L2Node = node or get_l2_node()
        self._running: bool = False
        self._tasks: List[asyncio.Task] = []

        # Rolling-TPS bookkeeping (counter snapshot from the previous slow tick).
        self._tps_prev: Dict[str, int] = {}
        self._tps_prev_ts: float = 0.0

        # Settlement submitter bookkeeping: batch numbers whose tx records were
        # already advanced to L1_SUBMITTED this process lifetime.
        self._l1_submitted: Set[int] = set()

        # Deposit indexer bookkeeping. ``None`` = not initialized yet; the first
        # successful head query anchors the scan window (see _index_l1_deposits).
        self._indexed_l1_height: Optional[int] = None
        scan_from = os.environ.get("ANIMICA_L2_L1_SCAN_FROM")
        self._scan_from_override: Optional[int] = None
        if scan_from is not None and scan_from != "":
            try:
                self._scan_from_override = int(scan_from, 0)
            except ValueError:
                log.warning("Invalid ANIMICA_L2_L1_SCAN_FROM=%r ignored", scan_from)

        # Tunables (non-consensus).
        self._slow_interval_s = _env_float("ANIMICA_L2_SLOW_LOOP_S", 1.0)
        self._l1_timeout_s = _env_float("ANIMICA_L2_L1_RPC_TIMEOUT_S", 5.0)
        self._max_blocks_per_scan = _env_int("ANIMICA_L2_INDEX_MAX_BLOCKS", 64)

    # ── lifecycle ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self.node.start()
        self._tps_prev = {k: L2_METRICS.counter_value(k) for k in _TPS_MAP}
        self._tps_prev_ts = time.monotonic()
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="l2.tick"),
            asyncio.create_task(self._slow_loop(), name="l2.slow"),
        ]
        log.info(
            "L2 service started",
            extra={
                "l2_chain_id": self.node.config.l2_chain_id,
                "mode": self.node.config.mode,
                "settlement_mode": self.node.config.settlement_mode.value,
                "settlement_enabled": self.node.config.settlement_enabled,
                "tick_interval_ms": self.node.config.tick_interval_ms,
            },
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.node.stop()
        log.info("L2 service stopped")

    # ── fast loop: sequencer tick ─────────────────────────────────────────────

    async def _tick_loop(self) -> None:
        """Drive ``sequencer.tick()`` every ``tick_interval_ms``.

        Time-based closure lives here: ``Sequencer.tick()`` re-feeds its builder
        with a single ``now`` timestamp each call, so the builder's own age is
        always 0 and only the tx-count/byte limits can fire from a plain tick.
        The async driver is the component that owns wall-clock time, so it
        tracks how long the pending set has been non-empty and passes
        ``force_close=True`` once ``config.batch_max_ms`` elapses — implementing
        the closure policy's ``max_age_ms`` exactly, without touching sequencer
        logic.
        """
        interval = max(int(self.node.config.tick_interval_ms), 1) / 1000.0
        max_age_s = max(int(self.node.config.batch_max_ms), 1) / 1000.0
        pending_since: Optional[float] = None
        try:
            while self._running:
                seq = self.node.sequencer
                now = time.monotonic()
                if seq._pending:  # noqa: SLF001 (documented driver view)
                    if pending_since is None:
                        pending_since = now
                    force = (now - pending_since) >= max_age_s
                else:
                    pending_since = None
                    force = False
                batch = None
                try:
                    batch = seq.tick(force_close=force)
                except Exception:
                    # A tick failure must not kill the loop; the sequencer's own
                    # invariant checks raise on money-conservation violations and
                    # those are logged loudly here for the operator.
                    log.exception("L2 sequencer tick failed")
                if batch is not None:
                    # _seal consumes the whole pending set; restart the age clock.
                    pending_since = None
                    await self._publish_batch_events(batch)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

    # ── slow loop: metrics, deposits, settlement ─────────────────────────────

    async def _slow_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self._slow_interval_s)
                self._update_tps_gauges()
                try:
                    enq = self.node.sequencer.enqueue_finalized_deposits()
                    if enq:
                        log.info("Enqueued %d finalized L1 deposit claim(s)", enq)
                except Exception:
                    log.exception("enqueue_finalized_deposits failed")
                try:
                    await self._index_l1_deposits()
                except Exception:
                    # Belt-and-braces: the indexer guards its own network calls,
                    # but an unexpected parse error must not kill the loop either.
                    log.exception("L1 deposit indexer iteration failed")
                if self.node.config.settlement_enabled:
                    try:
                        await self._settle_proven_batches()
                    except Exception:
                        log.exception("L2 settlement submitter iteration failed")
        except asyncio.CancelledError:
            return

    def _update_tps_gauges(self) -> None:
        """Refresh the rolling TPS gauges from counter deltas since last tick."""
        now = time.monotonic()
        elapsed = now - self._tps_prev_ts
        if elapsed <= 0:
            return
        for counter_key, gauge_key in _TPS_MAP.items():
            cur = L2_METRICS.counter_value(counter_key)
            prev = self._tps_prev.get(counter_key, cur)
            L2_METRICS.set(gauge_key, max(0.0, (cur - prev) / elapsed))
            self._tps_prev[counter_key] = cur
        self._tps_prev_ts = now

    # ── L1 JSON-RPC (aiohttp) ─────────────────────────────────────────────────

    async def _l1_rpc(self, method: str, params: Optional[list] = None) -> Any:
        """Single JSON-RPC call to the L1 node. Raises on transport/RPC errors;
        callers wrap in try/except so an unreachable L1 degrades safely."""
        url = self.node.config.l1_rpc_url
        if not url:
            raise RuntimeError("no L1 RPC url configured (ANIMICA_L2_L1_RPC_URL)")
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or [],
        }
        timeout = aiohttp.ClientTimeout(total=self._l1_timeout_s)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                body = await resp.json(content_type=None)
        if not isinstance(body, dict):
            raise RuntimeError(f"malformed JSON-RPC response for {method}")
        err = body.get("error")
        if err:
            raise RuntimeError(f"L1 RPC error for {method}: {err}")
        return body.get("result")

    # ── deposit indexer ───────────────────────────────────────────────────────

    async def _index_l1_deposits(self) -> int:
        """Scan L1 for bridge deposits and advance confirmation tiers.

        Queries the L1 node at ``config.l1_rpc_url`` for transfers **to the
        bridge address** whose ``data`` memo starts with ``ANML2D1``, records
        each via ``bridge.observe_deposit(...)`` (idempotent on deposit id, so a
        rescan after restart never double-credits), then calls
        ``bridge.update_l1_head(head)`` so OBSERVED deposits progress to
        CONFIRMED/FINALIZED. Returns the number of deposits newly observed.

        The scan is bounded to ``ANIMICA_L2_INDEX_MAX_BLOCKS`` (default 64)
        blocks per call so one iteration can never stall the slow loop; catch-up
        proceeds across iterations. The first run anchors the window one L1
        finality-depth behind head (override: ``ANIMICA_L2_L1_SCAN_FROM``) —
        older history is recovered from the persisted bridge snapshot in
        :mod:`l2.store`, not by rescanning the whole chain.
        """
        bridge = self.node.sequencer.bridge

        try:
            head = await self._l1_rpc("chain_getHead", [])
        except Exception as e:
            log.debug("L1 head query failed (L1 unreachable?): %s", e)
            return 0
        l1_head = _extract_height(head)
        if l1_head is None:
            log.debug("L1 head response had no usable height: %r", head)
            return 0

        if self._indexed_l1_height is None:
            if self._scan_from_override is not None:
                self._indexed_l1_height = max(-1, self._scan_from_override - 1)
            else:
                self._indexed_l1_height = max(-1, l1_head - 2 * L1_FINALITY_DEPTH)

        bridge_hex = _norm_hex(self.node.config.bridge_address)
        found = 0
        if bridge_hex:
            start = self._indexed_l1_height + 1
            end = min(l1_head, start + self._max_blocks_per_scan - 1)
            for height in range(start, end + 1):
                try:
                    # This node serves `chain.getBlockByNumber {number}` and
                    # returns txs as HASH strings (not full objects), so each
                    # candidate is fetched via `tx.getTransactionByHash`.
                    blk = await self._l1_rpc(
                        "chain.getBlockByNumber", {"number": height}
                    )
                except Exception as e:
                    log.debug("L1 block %d fetch failed: %s", height, e)
                    break  # retry from the same height next iteration
                if isinstance(blk, dict):
                    found += await self._observe_block_deposits(blk, height, bridge_hex)
                self._indexed_l1_height = height

        # Advance confirmation tiers even when no bridge address is configured
        # (test fixtures may seed deposits directly via bridge.observe_deposit).
        try:
            newly_final = bridge.update_l1_head(int(l1_head))
            if newly_final:
                log.info(
                    "%d L1 deposit(s) reached finality (claimable on L2)",
                    len(newly_final),
                )
        except Exception:
            log.exception("bridge.update_l1_head failed")
        return found

    async def _observe_block_deposits(
        self, block: dict, height: int, bridge_hex: str
    ) -> int:
        """Observe bridge deposits in one L1 block.

        This node's block view lists txs as HASH strings, so each is resolved
        via ``tx.getTransactionByHash``. A transfer counts as a deposit when its
        recipient is the bridge address. The credited L2 beneficiary is:
          * the 32-byte address in an ``ANML2D1`` memo when present, else
          * the L1 SENDER (``from``) — L1 and L2 share the same address
            derivation, so a plain transfer to the bridge credits the sender on
            L2. This matches what the wallet deposit screen instructs (send an
            ordinary L1 transfer), so memo-less deposits are credited.
        """
        bridge = self.node.sequencer.bridge
        raw = block.get("transactions") or block.get("txs") or []
        found = 0
        for entry in raw:
            tx: Optional[dict] = None
            if isinstance(entry, dict):
                tx = entry
            elif isinstance(entry, str):
                try:
                    # tx.getTransactionByHash takes a POSITIONAL hash arg on
                    # this node (the object {hash:…} form errors); block fetch
                    # above uses the object {number:…} form. Node conventions
                    # differ per method.
                    tx = await self._l1_rpc("tx.getTransactionByHash", [entry])
                except Exception as e:
                    log.debug("L1 tx %s fetch failed: %s", entry, e)
                    continue
                if isinstance(tx, dict) and not tx.get("hash"):
                    tx["hash"] = entry
            if not isinstance(tx, dict):
                continue
            if _norm_hex(tx.get("to")) != bridge_hex:
                continue
            try:
                amount = int(tx.get("value") or 0)
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            memo = _hex_to_bytes(tx.get("data"))
            if memo is not None and memo.startswith(DEPOSIT_MEMO_MAGIC):
                beneficiary = memo[len(DEPOSIT_MEMO_MAGIC) : len(DEPOSIT_MEMO_MAGIC) + _ADDR_LEN]
                if len(beneficiary) != _ADDR_LEN:
                    log.warning(
                        "Bridge deposit memo too short in L1 tx %s (block %d); "
                        "falling back to sender", tx.get("hash"), height,
                    )
                    beneficiary = _hex_to_bytes(tx.get("from"))
            else:
                # Plain transfer: credit the L1 sender on L2 (same address).
                beneficiary = _hex_to_bytes(tx.get("from"))
            if beneficiary is None or len(beneficiary) != _ADDR_LEN:
                log.warning(
                    "Bridge deposit in L1 tx %s (block %d) has no resolvable "
                    "beneficiary; skipped", tx.get("hash"), height,
                )
                continue
            l1_txid = _hex_to_bytes(tx.get("hash"))
            if l1_txid is None:
                continue
            try:
                dep = bridge.observe_deposit(l1_txid, beneficiary, amount, height)
                found += 1
                log.info(
                    "Observed L1 bridge deposit id=0x%s beneficiary=0x%s "
                    "amount=%d nanos at height %d",
                    dep.deposit_id.hex()[:16], beneficiary.hex()[:16], amount, height,
                )
            except Exception:
                log.exception(
                    "bridge.observe_deposit failed for L1 tx %s", tx.get("hash")
                )
        return found

    # ── settlement submitter ──────────────────────────────────────────────────

    def _build_anchor_payload(self, proof: Any) -> bytes:
        """Canonical ``ANML2C1`` anchoring memo for one proven batch.

        Layout (fixed-width, big-endian):
            magic(7) || l2_chain_id(8) || batch_number(8) ||
            new_state_root(32) || data_root(32) || public_inputs_digest(32)
        """
        pi = proof.public_inputs
        out = bytearray()
        out += ANCHOR_MEMO_MAGIC
        out += int(pi.l2_chain_id).to_bytes(8, "big")
        out += int(pi.batch_number).to_bytes(8, "big")
        out += pi.new_state_root
        out += pi.data_root
        out += pi.digest()
        return bytes(out)

    async def _settle_proven_batches(self) -> int:
        """Submit proven batches toward L1 and mark their txs L1_SUBMITTED.

        Real anchoring (see module docstring) publishes the anchor payload as an
        L1 transaction **to the bridge address**, signed with the sequencer's
        funded L1 key; that signer lands with the FORK_L2_ANCHOR tooling. Here
        the interface is wired end-to-end: the canonical payload is built and
        the L1 RPC at ``config.l1_rpc_url`` is called via aiohttp — guarded by
        try/except so an unreachable L1 or a not-yet-exposed anchoring method
        degrades safely — after which local records advance PROVEN → L1_SUBMITTED.
        L1_FINALIZED (and the ``settled_total`` counter feeding ``settled_tps``)
        is only ever set by an anchor-finality watcher once consensus-side
        anchoring activates; this stub never claims finality it cannot verify.
        """
        seq = self.node.sequencer
        submitted = 0
        for n in sorted(seq.proofs.keys()):
            if n in self._l1_submitted:
                continue
            proof = seq.proofs[n]
            anchor = self._build_anchor_payload(proof)
            try:
                # Where a real deployment sends the signed bridge-address tx.
                await self._l1_rpc(
                    "l2_submitAnchor",
                    ["0x" + anchor.hex(), "0x" + _norm_hex(self.node.config.bridge_address)],
                )
                log.info("Published L2 anchor for batch %d to L1", n)
            except Exception as e:
                log.debug(
                    "L1 anchor publish for batch %d degraded (stub path): %s", n, e
                )
            marked = self._mark_batch_l1_submitted(n)
            self._l1_submitted.add(n)
            submitted += 1
            if marked:
                log.info(
                    "Batch %d marked L1_SUBMITTED (%d tx record(s))", n, marked
                )
        return submitted

    def _mark_batch_l1_submitted(self, batch_number: int) -> int:
        """Advance PROVEN tx records of ``batch_number`` to L1_SUBMITTED."""
        seq = self.node.sequencer
        marked = 0
        for rec in seq.status.values():
            if rec.batch_number == batch_number and rec.status == TxStatus.PROVEN:
                rec.status = TxStatus.L1_SUBMITTED
                marked += 1
        return marked

    # ── WebSocket events ──────────────────────────────────────────────────────

    async def _publish_batch_events(self, batch: Any) -> None:
        """Publish l2NewBatches + l2SoftConfirmations frames for a sealed batch.

        The WS hub lives in the RPC layer; import lazily and tolerate absence so
        ``l2.service`` stays usable in processes that never mount the RPC app.
        """
        try:
            from rpc.ws import hub
        except Exception:
            return
        header = batch.header
        batch_payload = {
            "batchNumber": header.number,
            "l2ChainId": header.l2_chain_id,
            "batchId": "0x" + batch.id().hex(),
            "txCount": header.tx_count,
            "prevStateRoot": "0x" + header.prev_state_root.hex(),
            "stateRoot": "0x" + header.new_state_root.hex(),
            "transactionsRoot": "0x" + header.transactions_root.hex(),
            "receiptsRoot": "0x" + header.receipts_root.hex(),
            "dataRoot": "0x" + header.data_root.hex(),
            "timestampMs": header.timestamp_ms,
            "feesCollected": header.fees_collected,
            "deposited": header.deposited,
            "withdrawn": header.withdrawn,
        }
        try:
            await hub.publish_l2_new_batch(batch_payload)
        except Exception as e:
            log.debug("l2NewBatches publish failed: %s", e)

        confirmed = [r.txid for r in batch.receipts if r.status == "SUCCESS"]
        soft_payload = {
            "batchNumber": header.number,
            "l2ChainId": header.l2_chain_id,
            "confirmedCount": len(confirmed),
            "revertedCount": len(batch.receipts) - len(confirmed),
            "txids": ["0x" + t.hex() for t in confirmed[:_SOFT_CONFIRM_TXID_CAP]],
            "txidsTruncated": len(confirmed) > _SOFT_CONFIRM_TXID_CAP,
            "stateRoot": "0x" + header.new_state_root.hex(),
        }
        try:
            await hub.publish_l2_soft_confirmation(soft_payload)
        except Exception as e:
            log.debug("l2SoftConfirmations publish failed: %s", e)

    async def publish_finalized_batch(self, batch_number: int, l1_height: int) -> None:
        """Publish an l2FinalizedBatches frame. Called by the anchor-finality
        watcher once consensus-interpreted anchoring (FORK_L2_ANCHOR) activates
        and a batch's anchor tx is buried past L1 finality depth."""
        try:
            from rpc.ws import hub
        except Exception:
            return
        payload = {
            "batchNumber": batch_number,
            "l2ChainId": self.node.config.l2_chain_id,
            "l1Height": l1_height,
        }
        try:
            await hub.publish_l2_finalized_batch(payload)
        except Exception as e:
            log.debug("l2FinalizedBatches publish failed: %s", e)

    # ── status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Service-level status view layered over ``L2Node.status()``."""
        s = self.node.status()
        s["service"] = {
            "running": self._running,
            "indexedL1Height": self._indexed_l1_height,
            "l1SubmittedBatches": len(self._l1_submitted),
            "settlementEnabled": self.node.config.settlement_enabled,
            "l1RpcUrl": self.node.config.l1_rpc_url,
        }
        return s


__all__ = [
    "L2Service",
    "DEPOSIT_MEMO_MAGIC",
    "ANCHOR_MEMO_MAGIC",
]
