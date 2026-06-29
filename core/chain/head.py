from __future__ import annotations

"""
Head management & genesis finalization
=====================================

This module centralizes:
- Reading the canonical head (height, hash) from the block DB.
- Writing/updating the canonical head after fork choice.
- One-time genesis finalization: ensure genesis header is persisted and the
  canonical head is at height 0 on first boot, with basic invariants checked.

Design notes
------------
We deliberately keep this module "core-only":
- No PoIES/consensus checks here (that's in consensus/).
- No heavy state rebuild; we only ensure the *header/block* persistence and a
  consistent head pointer so the node can boot from genesis and begin syncing.

Expected block_db API (from core/db/block_db.py):
- get_canonical_head() -> Optional[tuple[int, bytes]]
- set_canonical_head(height: int, h: bytes) -> None
- get_header_by_hash(h: bytes) -> Optional[Header]
- put_header(height: int, h: bytes, header) -> None
- put_block(h: bytes, block) -> None    # optional; not used here for genesis
- has_genesis() -> bool                 # optional; if missing we detect via head/height
- get_genesis_hash() -> Optional[bytes] # optional; if missing we infer from stored header

We *feature-detect* optional methods. If absent, we fall back to portable logic.

Public API
----------
- read_head(block_db) -> Optional[tuple[int, bytes]]
- write_head(block_db, height: int, h: bytes) -> None
- finalize_genesis(block_db, params: ChainParams, genesis_header: Header) -> tuple[int, bytes]
"""

from dataclasses import asdict
import logging
import time
from typing import Any, Optional, Tuple

from core.db.block_db import PFX_HIX, _from_u64be

log = logging.getLogger(__name__)
from core.encoding.canonical import header_signing_bytes
from core.errors import GenesisError, GenesisMismatchError
from core.types.header import Header
from core.types.params import ChainParams
from core.utils.hash import sha3_256

# --- Small field helpers (tolerate snake/camel) -----------------------------


def _get_chain_id(hdr: Header) -> int:
    if hasattr(hdr, "chain_id"):
        return int(getattr(hdr, "chain_id"))
    if hasattr(hdr, "chainId"):
        return int(getattr(hdr, "chainId"))
    # As a last resort, try dataclass→dict
    m = asdict(hdr)
    if "chain_id" in m:
        return int(m["chain_id"])
    if "chainId" in m:
        return int(m["chainId"])
    raise GenesisError("genesis header missing chainId/chain_id")


def _get_height(hdr: Header) -> int:
    if hasattr(hdr, "height"):
        return int(getattr(hdr, "height"))
    m = asdict(hdr)
    if "height" in m:
        return int(m["height"])
    raise GenesisError("genesis header missing height")


def _header_hash(hdr: Header) -> bytes:
    """Canonical header hash. Prefer header.hash() to match BlockDB storage."""
    if hasattr(hdr, "hash") and callable(getattr(hdr, "hash")):
        return bytes(hdr.hash())  # type: ignore[no-any-return]
    return sha3_256(header_signing_bytes(hdr))


# --- Head I/O ---------------------------------------------------------------


def read_head(block_db) -> Optional[Tuple[int, bytes]]:
    """
    Return the canonical head (height, hash) if present, else None.
    Supports both the legacy get_canonical_head() API and the newer get_head().
    """
    head = None
    if hasattr(block_db, "get_canonical_head"):
        head = block_db.get_canonical_head()
    elif hasattr(block_db, "get_head"):
        head = block_db.get_head()

    # Only run the (expensive, O(N)) canonical-tip recovery scan when the
    # stored head pointer is missing — i.e. when importing a prebuilt DB or
    # after a crash. Doing this unconditionally turned every read_head() into
    # a full scan of the canonical-height index (~12K rows at h=12204), and
    # read_head() is called from hot paths (p2p head-watch loop, chain.getHead,
    # miner.getBlockTemplate, etc.), pegging the asyncio event loop at 100%
    # CPU and starving every RPC.
    if head is None:
        fallback = _recover_head_from_canonical(block_db)
        if fallback is not None:
            return fallback

    if head is not None:
        return head
    raise GenesisError("block_db missing head getter")


def _recover_head_from_canonical(block_db) -> Optional[Tuple[int, bytes]]:
    """
    Reconstruct the head from the canonical height index if the explicit head
    pointer is missing or stale (e.g., when importing a prebuilt devnet DB).
    """

    kv = getattr(block_db, "kv", None)
    if kv is None or not hasattr(kv, "iter_prefix"):
        return None

    try:
        max_height = -1
        max_hash: Optional[bytes] = None
        for key, value in kv.iter_prefix(PFX_HIX):
            if len(key) < len(PFX_HIX) + 8:
                continue
            height = _from_u64be(key[-8:])
            if height >= max_height:
                max_height = height
                max_hash = bytes(value)
        if max_hash is not None:
            # Defensive (the recurring silent "chain reset"): if the height index
            # recovered a head far below the canonical-height counter, the index
            # was truncated/corrupted and we're about to boot thousands of blocks
            # behind. Log CRITICAL so a watchdog/operator restores from the newest
            # snapshot immediately instead of discovering it hours later.
            try:
                getc = getattr(block_db, "get_canonical_height", None)
                canon = getc() if callable(getc) else None
                if canon is not None and int(canon) > int(max_height) + 64:
                    import logging

                    logging.getLogger("core.chain.head").critical(
                        "CHAIN-RESET GUARD: recovered head=%s from height index "
                        "but canonical_height=%s (%s ahead) — index looks "
                        "TRUNCATED. Restore from the newest snapshot.",
                        max_height, canon, int(canon) - int(max_height),
                    )
            except Exception:
                pass
            return (max_height, max_hash)
    except Exception:
        return None

    return None


def write_head(block_db, height: int, h: bytes, *, allow_reorg: bool = False) -> None:
    """
    Update the canonical head pointer. Supports both set_canonical_head(height, h)
    and set_head(height, h) naming variants.

    ``allow_reorg=True`` is required when moving the head to a height that is not
    strictly greater than the current head (e.g. a checkpoint rollback); without it
    the DB refuses the move ("refusing to move head without explicit reorg").
    """
    if hasattr(block_db, "set_canonical_head"):
        block_db.set_canonical_head(
            height, h, allow_overwrite=allow_reorg, allow_reorg=allow_reorg
        )
        return
    if hasattr(block_db, "set_head"):
        block_db.set_head(height, h, allow_reorg=allow_reorg)
        return
    raise GenesisError("block_db missing head setter")


def validate_head_against_checkpoint(
    block_db, checkpoint_height: int, checkpoint_hash: bytes
) -> bool:
    """Self-heal a node whose head is on a non-canonical fork.

    Determines the local block at ``checkpoint_height`` by an AUTHORITATIVE walk of
    the head's parentHash chain — NOT via the by-height index, which may itself be
    stale/corrupt (an unreconciled or re-corrupted index must never be the basis for
    a rollback decision: that is how a healthy node would get wrongly rolled back).
    If that authoritative block does not match the pinned ``checkpoint_hash``, the
    node's head is genuinely on a dead fork: roll it back to the divergent block's
    own parent (the shared ancestor at ``checkpoint_height - 1``) and prune the
    now-orphaned by-height index above it, so re-sync re-fetches the canonical chain.

    Brick-safe by construction — returns immediately (NO-OP) when:
      * there is no head, or the head is BELOW the checkpoint (still syncing), or
      * the parentHash walk cannot reach the checkpoint height (incomplete data —
        never roll back on uncertainty), or
      * the authoritative local hash MATCHES the pinned hash (the canonical case —
        a node on the right chain is NEVER modified, regardless of index state).
    Only a genuine authoritative-hash mismatch triggers a rollback. Never raises;
    returns True only if it actually rolled the head back.
    """
    try:
        head = read_head(block_db)
        if head is None:
            return False
        head_height, head_hash = int(head[0]), bytes(head[1])
        cp = int(checkpoint_height)
        if head_height < cp:
            return False  # not reached yet — normal during sync, must not interfere

        # Resolve the local block at `cp`. Fast path: when the by-height index has
        # been deep-reconciled (marker set), it is consistent with the head's parent
        # chain, so trust get_canonical_hash(cp) directly — O(1), avoids an
        # O(head - cp) walk on every boot as the chain grows. Fall back to the
        # authoritative parentHash walk whenever the index is NOT trusted (first boot
        # before reconcile, or a reconcile that aborted) or the indexed entry looks
        # insane — so a stale/corrupt index can never drive the rollback decision.
        local = None
        parent_of_local = None
        if _hix_index_trusted(block_db):
            get_canon = getattr(block_db, "get_canonical_hash", None)
            cand = get_canon(cp) if callable(get_canon) else None
            if cand is not None:
                chdr = block_db.get_header_by_hash(bytes(cand))
                if chdr is not None and int(chdr.height) == cp:
                    local = bytes(cand)
                    parent_of_local = bytes(chdr.parentHash)
        if local is None:
            # Authoritative: walk head -> cp via parentHash, independent of the index.
            cur = head_hash
            steps = 0
            while steps <= (head_height - cp) + 2:
                steps += 1
                hdr = block_db.get_header_by_hash(cur)
                if hdr is None:
                    return False  # incomplete chain data — do not roll back on uncertainty
                h = int(hdr.height)
                if h == cp:
                    local = cur
                    parent_of_local = bytes(hdr.parentHash)
                    break
                if h < cp:
                    return False  # overshot (corrupt heights) — bail safe
                nxt = bytes(hdr.parentHash)
                if nxt == b"\x00" * len(nxt):
                    return False
                cur = nxt
            if local is None:
                return False
        if bytes(local) == bytes(checkpoint_hash):
            return False  # CANONICAL — no-op (cannot brick healthy nodes)

        import logging

        log = logging.getLogger("core.chain.head")
        target = cp - 1
        target_hash = parent_of_local
        if target_hash is None or target_hash == b"\x00" * len(target_hash):
            # Fall back to the index for the rollback target (best-effort).
            get_canon = getattr(block_db, "get_canonical_hash", None)
            target_hash = get_canon(target) if callable(get_canon) else None
        if target_hash is None:
            log.critical(
                "CHECKPOINT VIOLATION at %d (local=%s != pinned=%s) but no block at "
                "%d to roll back to — deferring to snapshot.import / operator",
                cp, bytes(local).hex(), bytes(checkpoint_hash).hex(), target,
            )
            return False
        log.critical(
            "CHECKPOINT VIOLATION: head is on a non-canonical fork (block at %d = %s "
            "!= pinned canonical %s) — rolling head back to %d to resync canonical",
            cp, bytes(local).hex(), bytes(checkpoint_hash).hex(), target,
        )
        write_head(block_db, target, bytes(target_hash), allow_reorg=True)
        set_canon_h = getattr(block_db, "set_canonical_height", None)
        if callable(set_canon_h):
            set_canon_h(target)
        # Prune the now-orphaned by-height index above the rollback target so the
        # node never serves (to peers or itself) the dead-fork blocks it abandoned.
        kv = getattr(block_db, "kv", None)
        if kv is not None and hasattr(kv, "put"):
            try:
                from core.db.block_db import k_hix

                deleter = getattr(kv, "delete", None)
                if callable(deleter):
                    for hh in range(target + 1, head_height + 1):
                        deleter(k_hix(hh))
            except Exception:
                pass
        if kv is not None and hasattr(kv, "commit"):
            kv.commit()
        return True
    except Exception:
        try:
            import logging

            logging.getLogger("core.chain.head").exception(
                "validate_head_against_checkpoint failed (non-fatal)"
            )
        except Exception:
            pass
        return False


def get_head(block_db) -> Tuple[int, bytes]:
    """
    Compatibility alias for read_head(block_db).
    """
    return read_head(block_db)


# --- Genesis finalization ---------------------------------------------------


def _persist_chain_meta(
    block_db,
    *,
    chain_id: int,
    genesis_hash: bytes,
    genesis_sha256: bytes | None,
    created_at: int | None,
) -> None:
    if hasattr(block_db, "set_chain_id"):
        try:
            block_db.set_chain_id(chain_id)
        except Exception:
            pass
    if hasattr(block_db, "set_genesis_hash"):
        try:
            block_db.set_genesis_hash(genesis_hash)
        except Exception:
            pass
    if genesis_sha256 is not None and hasattr(block_db, "set_genesis_sha256"):
        try:
            block_db.set_genesis_sha256(genesis_sha256)
        except Exception:
            pass
    if created_at is not None and hasattr(block_db, "set_genesis_created_at"):
        try:
            existing = (
                block_db.get_genesis_created_at()
                if hasattr(block_db, "get_genesis_created_at")
                else None
            )
            if existing is None:
                block_db.set_genesis_created_at(created_at)
        except Exception:
            pass


def finalize_genesis(
    block_db,
    params: ChainParams,
    genesis_header: Header,
    *,
    genesis_sha256: bytes | None = None,
    genesis_path: str | None = None,
    created_at: int | None = None,
) -> Tuple[int, bytes]:
    """
    Ensure the DB has a consistent genesis and a canonical head.

    Steps:
      1) Validate basic genesis invariants: chainId matches params, height == 0.
      2) Compute genesis hash H0.
      3) If no canonical head yet: persist header(0) if missing and set head=(0, H0).
      4) If a head exists:
           - If height==0, ensure stored header hash equals H0, else fail.
           - If height>0, ensure the stored genesis (by lookup through parent chain)
             is consistent with H0; if not, fail (wrong DB for this chain).
    Returns:
      (height, hash) of the canonical head after finalization (height will be 0
       on first boot; may be >0 if DB already synced).
    """
    # (1) Basic invariants from header vs params
    chain_id = _get_chain_id(genesis_header)
    if chain_id != params.chain_id:
        raise GenesisError(
            f"genesis chainId={chain_id} does not match params.chain_id={params.chain_id}"
        )

    height0 = _get_height(genesis_header)
    if height0 != 0:
        raise GenesisError(f"genesis header height must be 0, got {height0}")

    # (2) Genesis hash
    h0 = _header_hash(genesis_header)

    # (3) If no head, write genesis and set head
    head = None
    try:
        head = read_head(block_db)
    except Exception:
        head = None
    if head is None:
        # Persist header(0) if not present
        existing = block_db.get_header_by_hash(h0)
        if existing is None:
            if hasattr(block_db, "write_header"):
                try:
                    block_db.write_header(0, genesis_header)  # type: ignore[attr-defined]
                except TypeError:
                    block_db.write_header(0, h0, genesis_header)  # type: ignore[arg-type]
            elif hasattr(block_db, "put_header"):
                try:
                    block_db.put_header(0, h0, genesis_header)  # type: ignore[arg-type]
                except TypeError:
                    block_db.put_header(genesis_header)  # type: ignore[call-arg]
            else:
                raise GenesisError("block_db missing header writer")
        if hasattr(block_db, "set_canonical"):
            block_db.set_canonical(0, h0)  # type: ignore[attr-defined]
        write_head(block_db, 0, h0)
        _persist_chain_meta(
            block_db,
            chain_id=chain_id,
            genesis_hash=h0,
            genesis_sha256=genesis_sha256,
            created_at=created_at,
        )
        return (0, h0)

    # (4) Head exists; sanity-check against our genesis
    cur_height, cur_hash = head
    if cur_height == 0:
        # If DB points to genesis, ensure it's OUR genesis
        if cur_hash != h0:
            expected = "0x" + h0.hex()
            found = "0x" + bytes(cur_hash).hex()
            raise GenesisMismatchError(
                "existing DB has different genesis hash (wrong network or corrupted DB)",
                expected=expected,
                found=found,
                chain_id=chain_id,
                genesis_path=str(genesis_path) if genesis_path else None,
            )
        if genesis_sha256 is not None and hasattr(block_db, "get_genesis_sha256"):
            try:
                stored_sha = block_db.get_genesis_sha256()
            except Exception:
                stored_sha = None
            if stored_sha is not None and stored_sha != genesis_sha256:
                raise GenesisMismatchError(
                    "existing DB genesis sha256 does not match configured genesis file",
                    expected="0x" + genesis_sha256.hex(),
                    found="0x" + bytes(stored_sha).hex(),
                    chain_id=chain_id,
                    genesis_path=str(genesis_path) if genesis_path else None,
                )
        _persist_chain_meta(
            block_db,
            chain_id=chain_id,
            genesis_hash=h0,
            genesis_sha256=genesis_sha256,
            created_at=created_at,
        )
        # Nothing to do
        return head

    # cur_height > 0. We expect that the stored genesis (reachable ancestor) matches h0.
    # We try a cheap check via any provided helper; else we rely on a stored "genesis hash"
    # in the DB if available; else we conservatively ensure *at least* that our genesis header
    # object is persisted (idempotent), without walking history here.
    _ensure_genesis_header_persisted(block_db, h0, genesis_header)

    # If DB exposes a get_genesis_hash(), use it for a strict check.
    if hasattr(block_db, "get_genesis_hash"):
        try:
            gh = block_db.get_genesis_hash()
        except TypeError:
            gh = None  # method exists but different signature
        if gh is not None and gh != h0:
            expected = "0x" + h0.hex()
            found = "0x" + bytes(gh).hex()
            raise GenesisMismatchError(
                "existing DB genesis hash does not match provided genesis header",
                expected=expected,
                found=found,
                chain_id=chain_id,
                genesis_path=str(genesis_path) if genesis_path else None,
            )

    if genesis_sha256 is not None and hasattr(block_db, "get_genesis_sha256"):
        try:
            stored_sha = block_db.get_genesis_sha256()
        except Exception:
            stored_sha = None
        if stored_sha is not None and stored_sha != genesis_sha256:
            raise GenesisMismatchError(
                "existing DB genesis sha256 does not match configured genesis file",
                expected="0x" + genesis_sha256.hex(),
                found="0x" + bytes(stored_sha).hex(),
                chain_id=chain_id,
                genesis_path=str(genesis_path) if genesis_path else None,
            )

    _persist_chain_meta(
        block_db,
        chain_id=chain_id,
        genesis_hash=h0,
        genesis_sha256=genesis_sha256,
        created_at=created_at,
    )

    # Otherwise we accept the current head as-is.
    return head


def _ensure_genesis_header_persisted(block_db, h0: bytes, hdr: Header) -> None:
    """
    Persist genesis header(0) if missing. This is idempotent and safe even if
    the DB is already advanced.
    """
    try:
        existing = block_db.get_header_by_hash(h0)
        if existing is None:
            if hasattr(block_db, "write_header"):
                try:
                    block_db.write_header(0, hdr)  # type: ignore[attr-defined]
                except TypeError:
                    block_db.write_header(0, h0, hdr)  # type: ignore[arg-type]
            elif hasattr(block_db, "put_header"):
                try:
                    block_db.put_header(0, h0, hdr)  # type: ignore[arg-type]
                except TypeError:
                    block_db.put_header(hdr)  # type: ignore[call-arg]
    except Exception:
        # Be conservative: if the backend doesn't allow inserting retroactively
        # (some exotic impl), we just skip; finalize_genesis will still succeed
        # as long as the canonical head & DB are consistent.
        pass


def finalize_genesis_if_needed(
    block_db, state_db=None, genesis_path: Optional[str] = None
) -> Tuple[int, bytes]:
    """
    Idempotent helper used by P2P to ensure the DB has a finalized genesis.

    Loads the genesis params/header and delegates to finalize_genesis. We avoid
    mutating state beyond the block DB finalization so callers can safely invoke
    this during service startup.
    """
    # Loud, early check: a non-writable chain DB makes every block import fail
    # SILENTLY (head freezes, miner/pool submissions rejected) — see the 10h
    # read-only-DB halt. Surface it at boot so it is never silent again.
    try:
        _probe_db_writable(block_db)
    except Exception:
        pass

    from core.genesis.loader import load_genesis

    params, header = load_genesis(genesis_path)
    try:
        from core.genesis.loader import compute_genesis_identity

        identity = compute_genesis_identity(genesis_path)
        genesis_sha256 = identity.genesis_file_hash
    except Exception:
        genesis_sha256 = None
    head = finalize_genesis(
        block_db,
        params,
        header,
        genesis_sha256=genesis_sha256,
        genesis_path=str(genesis_path) if genesis_path else None,
        created_at=int(time.time()),
    )
    # Self-heal canonical_height -> head height (idempotent, runs once at boot).
    # canonical_height is a running counter; on a head rollback, or after
    # restoring/importing a chain DB, it can drift ABOVE or BELOW the real head.
    # When it does, the node believes it is MINER_TOO_FAR_BEHIND and refuses to
    # mine / chases phantom blocks — the recurring "chain reset" symptom. The
    # manual /root/fix_canonical.py only handled the inflated (above) case;
    # reconciling here on every boot makes nodes auto-recover after updating.
    try:
        head_h = int(head[0])
        get_c = getattr(block_db, "get_canonical_height", None)
        set_c = getattr(block_db, "set_canonical_height", None)
        if callable(get_c) and callable(set_c):
            canon = get_c()
            if canon is None or int(canon) != head_h:
                set_c(head_h)
                kv = getattr(block_db, "kv", None)
                commit = getattr(kv, "commit", None)
                if callable(commit):
                    try:
                        commit()
                    except Exception:
                        pass
    except Exception:
        pass
    # Self-heal a corrupt by-height index (idempotent, runs at boot).
    try:
        _reconcile_height_index(block_db)
    except Exception:
        pass
    # Force convergence for a node wedged on a dead fork. MUST run AFTER the index
    # heal so a healthy tip node (which only had a stale index entry) is a no-op
    # here and is never rolled back; only a node whose head is genuinely on the
    # wrong fork at a pinned height is rolled back to re-sync the canonical chain.
    try:
        _enforce_pinned_checkpoints(block_db, params)
    except Exception:
        pass
    # A checkpoint rollback may have moved the head; return the current value.
    try:
        cur_head = read_head(block_db)
        if cur_head is not None:
            head = cur_head
    except Exception:
        pass
    return head


# By-height-index reconcile scheme. BUMP to force a one-time full deep re-walk +
# repair on the next boot after upgrading. The deep walk is O(chain length); the
# marker makes it run exactly once per scheme bump (afterwards normal head advances
# keep the index correct — they route through _apply_reorg, which rewrites every
# attached height including the fork point — so subsequent boots only do a cheap
# near-tip linkage check). Scheme 1 ships the fix for legacy reorgs that left an
# orphan-sibling block in the by-height index at a fork-point height (the "nodes
# stuck on block N" outage), which the old sparse/height-only probe could not see.
_HIX_RECONCILE_SCHEME = 1
_HIX_TIP_WINDOW = 1024


def _hix_marker_key() -> bytes:
    from core.db.block_db import PFX_META

    return PFX_META + b"hix_reconciled"


def _hix_index_trusted(block_db) -> bool:
    """True if the by-height index has been deep-reconciled under the current scheme.

    When set, the index is consistent with the head's parentHash chain (the deep
    walk rewrote every divergent entry, and normal head advances maintain it), so a
    direct get_canonical_hash(height) is authoritative — letting the checkpoint check
    avoid an O(head - checkpoint) parentHash walk on every boot. Absent/stale marker
    => not trusted => callers must fall back to the authoritative walk.
    """
    try:
        kv = getattr(block_db, "kv", None)
        if kv is None:
            return False
        marker = kv.get(_hix_marker_key())
        return bool(marker) and len(marker) >= 1 and marker[0] == _HIX_RECONCILE_SCHEME
    except Exception:
        return False


def _hix_tip_linkage_ok(block_db, head_height: int, head_hash: bytes) -> bool:
    """Cheap near-tip integrity check after the one-time deep reconcile.

    Verifies the by-height index over the last ``_HIX_TIP_WINDOW`` heights forms an
    unbroken parentHash chain ending exactly at ``head_hash``. Returns False (=>
    escalate to a full deep reconcile) on any hole or mismatch. O(window),
    independent of chain length. A break at a fork point surfaces one height ABOVE
    the orphan (the child's parentHash won't match the orphan), so this window-walk
    catches near-tip regressions; deep legacy corruption is handled by the one-time
    full walk gated on the scheme marker.
    """
    try:
        if block_db.get_canonical_hash(head_height) != head_hash:
            return False
        lo = max(1, head_height - _HIX_TIP_WINDOW + 1)
        for h in range(head_height, lo - 1, -1):
            cur = block_db.get_canonical_hash(h)
            if cur is None:
                return False
            hdr = block_db.get_header_by_hash(bytes(cur))
            if hdr is None or int(hdr.height) != h:
                return False
            prev = block_db.get_canonical_hash(h - 1)
            if prev is None or bytes(hdr.parentHash) != bytes(prev):
                return False
        return True
    except Exception:
        return False


def _reconcile_height_index(block_db) -> None:
    """Repair a corrupt canonical by-height index (k_hix: height -> hash).

    The block DATA can be fully intact (every block retrievable by-hash, head chains
    cleanly to genesis) while the secondary height index is wrong: a restored/
    rolled-back DB, a crash mid-batch, or a LEGACY reorg can leave k_hix(height)
    missing (a hole) OR pointing at an ORPHAN SIBLING at the correct height (a
    losing fork block whose header.height equals the queried height but which is NOT
    on the head's parentHash chain). Because peers sync BY HEIGHT, such an index
    serves a block whose hash does not match the next block's parentHash, so every
    by-height peer freezes at the lowest broken height (the "nodes stuck on block N"
    outage). canonical_height reconciliation above does not touch this index.

    Detection is LINKAGE-CORRECT, not a sample: it walks the canonical chain head ->
    genesis via header.parentHash (the authoritative ordering, read from the head
    pointer which is independent of the possibly-corrupt index) and rewrites EVERY
    height whose k_hix entry disagrees — including an orphan sibling at the right
    height, which the previous sparse/height-only probe could not detect. ABORTS
    without writing if any block is missing by-hash (needs a snapshot restore, not
    an index rebuild). Lossless: it never touches block data, only the index.

    Cost is bounded by a persisted scheme marker (``_HIX_RECONCILE_SCHEME`` ++
    head_hash): the O(chain) walk runs once per scheme bump; afterwards boots do
    only a cheap near-tip linkage check and escalate to a full walk only if it fails.
    """
    from core.db.block_db import k_hix

    # Resolve the head via read_head (with its canonical-index recovery fallback),
    # the SAME path the checkpoint enforcement uses. Using the raw META pointer
    # (block_db.get_head) here would skip the repair on a node whose META head is
    # absent but whose index is populated (prebuilt-DB import / torn head write),
    # while the checkpoint would still recover a head from the index — leaving the
    # two halves disagreeing on the head. Keeping them in lock-step guarantees the
    # in-place index repair runs whenever the checkpoint can see a head.
    head = read_head(block_db)
    if head is None:
        return
    head_height, head_hash = int(head[0]), bytes(head[1])
    if head_height <= 0:
        return

    kv = getattr(block_db, "kv", None)
    if kv is None:
        return

    marker_key = _hix_marker_key()
    marker = None
    try:
        marker = kv.get(marker_key)
    except Exception:
        marker = None
    deep_done = bool(marker) and len(marker) >= 1 and marker[0] == _HIX_RECONCILE_SCHEME

    if deep_done:
        # The one-time deep repair already ran under this scheme; normal head
        # advances keep the index correct, so only verify the near-tip linkage.
        if _hix_tip_linkage_ok(block_db, head_height, head_hash):
            return
        log.warning(
            "height-index: near-tip linkage check failed at head=%d; escalating "
            "to a full deep reconcile",
            head_height,
        )

    # DEEP reconcile: walk the canonical chain by parentHash, verifying every block
    # exists, building the authoritative {height: hash}.
    authoritative: dict[int, bytes] = {}
    cur = head_hash
    steps = 0
    while True:
        steps += 1
        if steps > head_height + 100:
            log.error("height-index reconcile: walk overran (cycle?); aborting")
            return
        hdr = block_db.get_header_by_hash(cur)
        if hdr is None:
            log.error(
                "height-index reconcile: block missing by-hash at ~height %d (%s); "
                "chain data incomplete, needs snapshot restore — aborting",
                head_height - len(authoritative), cur.hex(),
            )
            return
        h = int(hdr.height)
        authoritative[h] = cur
        if h == 0:
            break
        parent = bytes(hdr.parentHash)
        if parent == b"\x00" * len(parent):
            log.error("height-index reconcile: zero parent above genesis; aborting")
            return
        cur = parent

    if 0 not in authoritative:
        log.error("height-index reconcile: walk did not reach genesis; aborting")
        return

    # Compare authoritative vs the stored index at EVERY height; collect divergences
    # (holes AND orphan-sibling mismatches).
    diffs: list[tuple[int, bytes]] = []
    for height, want in authoritative.items():
        have = block_db.get_canonical_hash(height)
        if have is None or bytes(have) != want:
            diffs.append((height, want))

    if diffs:
        log.warning(
            "height-index reconcile: repairing %d of %d entries (head=%d) — stale/"
            "missing by-height index (the 'nodes stuck on a block' outage)",
            len(diffs),
            len(authoritative),
            head_height,
        )
        # Use put()+commit() rather than kv.batch(): boot may already hold an open
        # implicit transaction on the shared connection (finalize_genesis writes
        # genesis meta), and a batch's BEGIN IMMEDIATE would raise "transaction
        # within a transaction". put() runs in the existing transaction; one commit
        # flushes the whole rebuild. Mirrors the canonical-height heal.
        for height, want in diffs:
            kv.put(k_hix(height), want)
        commit = getattr(kv, "commit", None)
        if callable(commit):
            commit()
        log.warning("height-index reconcile: repaired %d entries", len(diffs))

    # Write the marker LAST (index first, marker last) so a crash between the
    # rewrite and the marker just causes a harmless re-run, never a skipped repair.
    try:
        kv.put(marker_key, bytes([_HIX_RECONCILE_SCHEME]) + head_hash)
        commit = getattr(kv, "commit", None)
        if callable(commit):
            commit()
    except Exception:
        pass


def _enforce_pinned_checkpoints(block_db, params) -> None:
    """Force a node wedged on a dead fork back onto the canonical chain.

    MUST run AFTER ``_reconcile_height_index``. A node that is merely on the correct
    chain but had a stale *index* entry has, by then, already repaired its index, so
    its block at every pinned height matches the pin and this is a NO-OP for it. Only
    a node whose HEAD is genuinely on the wrong fork at a pinned height is rolled back
    (to just below the lowest violated checkpoint) so by-height/HTTP sync re-requests
    and re-pulls the corrected canonical block — the by-height (rpc_pull) path only
    ever asks for local_height+1 and would otherwise never re-fetch the fork height.

    Brick-safe: ``validate_head_against_checkpoint`` is a no-op unless the local
    block at the pinned height genuinely differs from the pinned hash, and never
    rolls a node that has not yet reached the checkpoint height. Never raises.
    """
    try:
        from core.network_params import get_pinned_checkpoints
    except Exception:
        return
    try:
        chain_id = int(getattr(params, "chain_id"))
    except Exception:
        return
    checkpoints = get_pinned_checkpoints(chain_id=chain_id)
    if not checkpoints:
        return
    # Validate from the lowest pinned height up; the first genuine violation rolls
    # the head back below it, after which re-sync re-pulls everything above.
    for height in sorted(checkpoints):
        try:
            if validate_head_against_checkpoint(block_db, height, checkpoints[height]):
                break
        except Exception:
            continue


def _probe_db_writable(block_db) -> bool:
    """Boot-time write probe for the chain DB.

    A read-only chain DB makes EVERY block import fail (`put_header` raises
    "attempt to write a readonly database"), so the head freezes and miner/pool
    block submissions are rejected — with almost nothing in the logs. The usual
    cause is a file-ownership mismatch: the DB file (animica.db / -wal / -shm)
    is owned by another user (e.g. root, after a manual or root-run DB op) while
    the node process runs as a non-root uid. Probe once at boot and, if it
    fails, log loudly with the fix. Non-fatal (the node can still serve reads).
    """
    kv = getattr(block_db, "kv", None)
    if kv is None or not hasattr(kv, "put"):
        return True
    probe_key = b"\x00\x00__writable_probe__"
    commit = getattr(kv, "commit", None)
    try:
        kv.put(probe_key, b"1")
        if callable(commit):
            commit()
        # Clean up the probe key (best-effort).
        try:
            delete = getattr(kv, "delete", None)
            if callable(delete):
                delete(probe_key)
                if callable(commit):
                    commit()
        except Exception:
            pass
        return True
    except Exception as exc:
        log.critical(
            "CHAIN DB IS NOT WRITABLE (%s). The node cannot import blocks: the "
            "head will freeze and all miner/pool block submissions will be "
            "rejected. Most likely the DB file is owned by a different user than "
            "the node process (e.g. root-owned after a manual/root DB operation "
            "while the node runs as a non-root uid). FIX: chown the chain DB "
            "(animica.db, animica.db-wal, animica.db-shm) to the node's uid and "
            "restart.", exc,
        )
        return False
