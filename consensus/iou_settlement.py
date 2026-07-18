"""
consensus.iou_settlement — FORK_VPN_RELAY_REWARDS realized: on-chain IOU settlement (9.0.0)
===========================================================================================

9.0.0 transforms the 50,000 fork (shipped gated-INERT in 8.0.1 as
FORK_VPN_RELAY_REWARDS) into REAL per-block settlement of service IOUs —
dVPN relay/exit IOUs, AICF inference IOUs, media-queue IOUs and hosting IOUs
alike. The off-chain ledgers stay where they are; what changes is that the
operator can now discharge them with real coins, rate-limited by consensus:

  - A **settlement anchor** is an ordinary signed TRANSFER transaction FROM the
    code-committed settlement authority (the foundation treasury account) whose
    TxTransfer.data carries ``ANMSETL1`` + a strict JSON distribution
    ``{"v": 1, "pay": [["anim1…", amount_nANM], …]}``. To every pre-9.0.0 node
    it is just a normal transfer with opaque data — relayed, included and
    executed unchanged (no new tx type, no flag-day mempool rule).

  - A block at/after the fork height (50,000) that CONTAINS valid anchors pays
    the anchored entries IN THAT SAME BLOCK: the total is capped at
    ``settlement_pool_cap`` (50 ANM at the current epoch, halving exactly when
    the block subsidy halves — the cap is scaled by current_subsidy/start_subsidy,
    the same integer math as the 8.0.1 relay cap) and CARVED from the miner
    subsidy output. Never minted: miner + settlement == the pre-fork miner
    output, every block, so total emission and MAX_MONEY are untouched.

  - Self-gating: a block with no anchors is byte-identical to 8.0.x behaviour.
    Non-upgraded nodes only diverge once the FIRST anchor is posted — and only
    the settlement authority can post one, so activation timing stays under
    operator control (post the first anchor only after network adoption).

Determinism contract (consensus-critical):
  - Everything here is a pure function of the block's transaction bytes and the
    static emission schedule. No I/O, no clock, no node-local state.
  - An anchor that fails ANY check (magic, JSON shape, address decode, amount
    type/range, entry count, signature, authority binding) is ignored WHOLE —
    never partially interpreted (fail-closed, no guessing).
  - Amounts are integers in base units (nANM, 1 ANM = 10^9). ``bool`` is
    explicitly rejected (it is an ``int`` subclass in Python).
  - The authority is bound to the SIGNATURE PUBKEY (the account the executor
    debits), not merely the declared sender field; both must match. Candidate
    anchors are signature-verified here even though the block-import
    PQ-hardening gate already verifies every tx — the gate has an observe-only
    shadow env, and settlement must not trust it.
  - If a candidate anchor is present but the verify machinery is unavailable,
    extraction RAISES (the importing node halts loudly instead of silently
    paying nobody while healthy peers pay — the ANM-H08 fail-closed rule).
"""

from __future__ import annotations

import json
import logging
from typing import Any, List, Optional, Sequence, Tuple

from core.network_params import is_fork_active, FORK_VPN_RELAY_REWARDS

log = logging.getLogger("consensus.iou_settlement")

# Readable alias: 9.0.0 generalizes the fork from "VPN relay rewards" to full
# IOU settlement. The registry key (and env override
# ANIMICA_FORK_VPN_RELAY_REWARDS_HEIGHT) are unchanged — same fork, realized.
FORK_IOU_SETTLEMENT = FORK_VPN_RELAY_REWARDS

# Magic prefix marking a TRANSFER's data field as a settlement anchor payload.
SETTLEMENT_MAGIC: bytes = b"ANMSETL1"

# The settlement authority: only anchors signed by this account settle IOUs.
# Code-committed (never params/env) for the same reason as the foundation
# split address — emission-affecting identity must be byte-identical on every
# node. The foundation treasury already receives the 15% subsidy slice and is
# operator-controlled, so it is the natural (funded) settlement issuer.
# A tuple so a dedicated settlement key can be added by a future release.
from consensus.rewards import (  # noqa: E402  (import placement: constants below)
    FOUNDATION_TREASURY_ADDRESS,
    VPN_RELAY_REWARD_CAP,
    MAX_MONEY,
    parse_emission_schedule,
    vpn_relay_pool_cap,
)

SETTLEMENT_AUTHORITY_ADDRESSES: Tuple[str, ...] = (FOUNDATION_TREASURY_ADDRESS,)

# Hard shape bounds (DoS + determinism): beyond these an anchor/block is not
# "trimmed" — the offending anchor is ignored whole / extra anchors are ignored
# in deterministic tx order.
MAX_ENTRIES_PER_ANCHOR = 2000
MAX_ANCHORS_PER_BLOCK = 8

__all__ = [
    "FORK_IOU_SETTLEMENT",
    "SETTLEMENT_MAGIC",
    "SETTLEMENT_AUTHORITY_ADDRESSES",
    "MAX_ENTRIES_PER_ANCHOR",
    "MAX_ANCHORS_PER_BLOCK",
    "SettlementUnavailable",
    "settlement_pool_cap",
    "parse_anchor_payload",
    "extract_settlement_distribution",
    "scale_settlement_outputs",
    "encode_anchor_payload",
]


class SettlementUnavailable(Exception):
    """Raised when a candidate anchor exists but the machinery to verify it is
    unavailable on this node. Block import must treat this as a hard error
    (fail closed) — never as "no settlement this block" (silent divergence)."""


# ----------------------------------------------------------------------------------
# Cap — 50 ANM per block, halving exactly when the block subsidy halves
# ----------------------------------------------------------------------------------


def settlement_pool_cap(height_for_halving: int, params: Any) -> int:
    """Max settlement payout for this block, in base units.

    Delegates to the reviewed 8.0.1 relay-cap math: 50 ANM scaled by
    current_subsidy/initial_subsidy (integer-only), so it is exactly 50 ANM
    until the first halving at epoch boundary 1,350,000, then 25 ANM, etc.,
    and follows the tail floor proportionally. Returns 0 when the schedule is
    unavailable (no schedule ⇒ no subsidy ⇒ nothing to carve from anyway).
    """
    try:
        schedule = parse_emission_schedule(params or {})
    except Exception:
        return 0
    return vpn_relay_pool_cap(int(height_for_halving), schedule)


# ----------------------------------------------------------------------------------
# Anchor payload — strict, deterministic parse
# ----------------------------------------------------------------------------------


def _decode_payout_address(addr: Any) -> Optional[bytes]:
    """bech32m ``anim1…`` → 32-byte account key, or None if invalid.

    Uses core.utils.address.address_to_bytes — the SAME decoder the foundation
    treasury credit uses (and that getbalance reads), never the pq.address
    fallback path that can silently produce a dead zero key."""
    if not isinstance(addr, str) or not addr or len(addr) > 128:
        return None
    try:
        from core.utils.address import address_to_bytes

        raw = address_to_bytes(addr)
    except Exception:
        return None
    if not isinstance(raw, (bytes, bytearray)) or len(raw) != 32:
        return None
    return bytes(raw)


def parse_anchor_payload(data: bytes) -> Optional[List[Tuple[bytes, int]]]:
    """Parse one anchor's data field into [(account_key_32B, amount_nANM), …].

    Returns None if the payload is not a valid anchor (the caller ignores the
    anchor WHOLE). Strict on shape: exactly the keys {"v", "pay"}, v == 1,
    1..MAX_ENTRIES_PER_ANCHOR entries of [address_str, positive_int]. Duplicate
    addresses are aggregated (first-occurrence order) so the credit order is
    deterministic and each account is credited at most once per anchor.
    """
    if not isinstance(data, (bytes, bytearray)):
        return None
    data = bytes(data)
    if not data.startswith(SETTLEMENT_MAGIC):
        return None
    try:
        obj = json.loads(data[len(SETTLEMENT_MAGIC):].decode("utf-8", errors="strict"))
    except Exception:
        return None
    if not isinstance(obj, dict) or set(obj.keys()) != {"v", "pay"}:
        return None
    if obj["v"] != 1 or type(obj["v"]) is not int:
        return None
    pay = obj["pay"]
    if not isinstance(pay, list) or not (1 <= len(pay) <= MAX_ENTRIES_PER_ANCHOR):
        return None

    aggregated: dict[bytes, int] = {}
    order: List[bytes] = []
    total = 0
    for entry in pay:
        if not isinstance(entry, list) or len(entry) != 2:
            return None
        addr, amt = entry
        # bool is an int subclass — reject explicitly; floats/strings rejected.
        if type(amt) is not int or amt <= 0 or amt > MAX_MONEY:
            return None
        key = _decode_payout_address(addr)
        if key is None:
            return None
        total += amt
        if total > MAX_MONEY:
            return None
        if key in aggregated:
            aggregated[key] += amt
        else:
            aggregated[key] = amt
            order.append(key)
    return [(k, aggregated[k]) for k in order]


def encode_anchor_payload(entries: Sequence[Tuple[str, int]]) -> bytes:
    """Operator-side helper (CLI): build the canonical anchor data bytes from
    [(bech32m_address, amount_nANM), …]. Raises on invalid input — the CLI must
    never post an anchor consensus would ignore."""
    if not entries or len(entries) > MAX_ENTRIES_PER_ANCHOR:
        raise ValueError(
            f"anchor must carry 1..{MAX_ENTRIES_PER_ANCHOR} entries, got {len(entries)}"
        )
    pay: List[List[Any]] = []
    total = 0
    for addr, amt in entries:
        amt = int(amt)
        if amt <= 0:
            raise ValueError(f"non-positive amount for {addr}: {amt}")
        if _decode_payout_address(addr) is None:
            raise ValueError(f"invalid payout address: {addr!r}")
        total += amt
        if total > MAX_MONEY:
            raise ValueError("anchor total exceeds MAX_MONEY")
        pay.append([addr, amt])
    payload = SETTLEMENT_MAGIC + json.dumps(
        {"v": 1, "pay": pay}, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    parsed = parse_anchor_payload(payload)
    if parsed is None:
        raise ValueError("internal error: encoded anchor failed strict re-parse")
    return payload


# ----------------------------------------------------------------------------------
# Anchor extraction from a block's transactions
# ----------------------------------------------------------------------------------


def _tx_raw_cbor(tx: Any) -> Optional[bytes]:
    """Normalize a block tx to canonical CBOR bytes — the exact normalization
    the block-import signature gate uses (bytes | .to_cbor() | normalize)."""
    try:
        if isinstance(tx, (bytes, bytearray)):
            return bytes(tx)
        if hasattr(tx, "to_cbor"):
            return tx.to_cbor()
        from rpc.methods import tx as tx_methods

        nb = getattr(tx_methods, "normalize_tx_bytes", None)
        return nb(tx) if nb is not None else None
    except Exception:
        return None


def _looks_like_anchor(tx: Any) -> bool:
    """Cheap pre-filter: does this tx's transfer data start with the magic?
    Never authoritative — only decides whether the full verified path runs.
    Handles both decoded Tx objects and raw CBOR bytes."""
    try:
        unsigned = getattr(tx, "unsigned", None) or getattr(tx, "tx", None)
        payload = getattr(unsigned, "payload", None) if unsigned is not None else None
        data = getattr(payload, "data", None) if payload is not None else None
        if isinstance(data, (bytes, bytearray)):
            return bytes(data).startswith(SETTLEMENT_MAGIC)
        raw = _tx_raw_cbor(tx)
        # Raw-bytes fallback: the magic appears verbatim inside the CBOR byte
        # string; a substring scan can only over-select (full path re-checks).
        return raw is not None and SETTLEMENT_MAGIC in raw
    except Exception:
        # Cannot even inspect it cheaply — let the full path decide.
        return True


def _authority_account_keys() -> List[bytes]:
    keys: List[bytes] = []
    for addr in SETTLEMENT_AUTHORITY_ADDRESSES:
        key = _decode_payout_address(addr)
        if key is not None:
            keys.append(key)
    if not keys:
        # The authority constant failing to decode is a build defect, not a
        # runtime condition — fail loudly, never "no authority ⇒ no checks".
        raise SettlementUnavailable("settlement authority address failed to decode")
    return keys


def extract_settlement_distribution(
    txs: Sequence[Any], *, chain_id: int
) -> List[Tuple[bytes, int]]:
    """Extract the aggregated settlement distribution from a block's txs.

    Deterministic over the block's transaction bytes: scans txs in block
    order, takes at most MAX_ANCHORS_PER_BLOCK VALID anchors (a valid anchor
    is a signature-verified TRANSFER from the settlement authority whose data
    parses strictly), aggregates their entries by account (first-occurrence
    order), and returns [(account_key_32B, requested_nANM), …].

    Raises SettlementUnavailable when a candidate anchor is present but the
    decode/verify machinery is unavailable (fail closed — see module docs).
    Individual anchors failing verification/parse are ignored whole.
    """
    candidates = [tx for tx in (txs or ()) if _looks_like_anchor(tx)]
    if not candidates:
        return []

    try:
        from rpc.methods import tx as tx_methods
    except Exception as e:
        raise SettlementUnavailable(f"tx verify module unavailable: {e}") from e
    if (
        getattr(tx_methods, "_pq_verify", None) is None
        and getattr(tx_methods, "_pq_verify_tx", None) is None
    ):
        raise SettlementUnavailable("pq verify backend missing")

    authority_keys = _authority_account_keys()

    aggregated: dict[bytes, int] = {}
    order: List[bytes] = []
    anchors_used = 0

    for tx in candidates:
        if anchors_used >= MAX_ANCHORS_PER_BLOCK:
            log.warning(
                "iou_settlement: anchor limit (%d) reached — ignoring further anchors this block",
                MAX_ANCHORS_PER_BLOCK,
            )
            break
        try:
            raw = _tx_raw_cbor(tx)
            if raw is None:
                continue
            tx_like, obj = tx_methods._decode_tx(raw)

            unsigned = getattr(tx_like, "unsigned", None)
            if unsigned is None:
                continue
            from core.types.tx import TxKind

            if unsigned.kind != TxKind.TRANSFER:
                continue
            data = getattr(unsigned.payload, "data", b"") or b""
            if not bytes(data).startswith(SETTLEMENT_MAGIC):
                continue

            # Authority binding 1/2: the DECLARED sender must be the authority
            # (that is the account the transfer debits).
            if bytes(unsigned.sender) not in authority_keys:
                log.warning(
                    "iou_settlement: anchor-like tx from non-authority sender ignored"
                )
                continue

            # Authority binding 2/2: the SIGNATURE PUBKEY must derive to the
            # authority address — the declared field alone is never trusted.
            sender_from_sig = tx_methods._extract_sender_address(obj)
            sig_key = _decode_payout_address(sender_from_sig) if sender_from_sig else None
            if sig_key is None or sig_key not in authority_keys:
                log.warning(
                    "iou_settlement: anchor signature pubkey does not derive to authority — ignored"
                )
                continue

            # Verify the signature over the tx body directly (never trust the
            # import gate: it has an observe-only shadow mode).
            tx_methods._verify_pq_signature(tx_like, obj, chain_id=int(chain_id))

            entries = parse_anchor_payload(bytes(data))
            if entries is None:
                log.warning("iou_settlement: authority anchor failed strict parse — ignored")
                continue

            anchors_used += 1
            for key, amt in entries:
                if key in aggregated:
                    aggregated[key] += amt
                else:
                    aggregated[key] = amt
                    order.append(key)
        except SettlementUnavailable:
            raise
        except (ImportError, ModuleNotFoundError, AttributeError, NameError, OSError) as e:
            # These are INFRASTRUCTURE failures (missing module, renamed symbol,
            # unloadable backend), not properties of the anchor bytes. Treating
            # them as "anchor invalid" would make a partially-broken node silently
            # pay nobody while healthy peers pay — the exact fail-open divergence
            # the module contract forbids. Halt loudly instead.
            raise SettlementUnavailable(f"verify machinery failed: {e!r}") from e
        except Exception as e:
            # Per-anchor fail-closed: a malformed/badly-signed anchor is ignored
            # whole. This is deterministic — the same bytes fail the same way on
            # every node (backend availability is handled above and infra errors
            # raise SettlementUnavailable, loudly).
            log.warning("iou_settlement: candidate anchor ignored: %r", e)
            continue

    return [(k, aggregated[k]) for k in order]


# ----------------------------------------------------------------------------------
# Cap + proportional scale (the reviewed 8.0.1 carve math)
# ----------------------------------------------------------------------------------


def scale_settlement_outputs(
    distribution: Sequence[Tuple[bytes, int]],
    pool_cap: int,
    miner_subsidy: int,
) -> List[Tuple[bytes, int]]:
    """Scale the requested distribution to what this block can afford.

    affordable = min(requested_total, pool_cap, miner_subsidy); each entry gets
    floor(affordable * amount / requested_total) — integer-only, deterministic;
    the rounding remainder stays with the miner, so
    sum(outputs) + miner_after == miner_before always holds (never mints).
    When the operator keeps an anchor's total ≤ the cap (the normal case) the
    floor is exact and every entry is paid in full.
    """
    requested_total = sum(max(0, int(a)) for _, a in distribution)
    if requested_total <= 0:
        return []
    affordable = min(requested_total, max(0, int(pool_cap)), max(0, int(miner_subsidy)))
    if affordable <= 0:
        return []
    outputs: List[Tuple[bytes, int]] = []
    for key, amt in distribution:
        share = (affordable * max(0, int(amt))) // requested_total
        if share > 0:
            outputs.append((bytes(key), share))
    return outputs
