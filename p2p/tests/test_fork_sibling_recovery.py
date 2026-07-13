"""Regression tests for the 38728 same-height-fork wedge (2026-07-07 outage).

A node that accepted the LOSING side of a 1-block natural fork could never
import the winning sibling: the headers pipeline discarded a header at/below
the local head at every layer (overlap trim, first-header anchor_mismatch,
not-actionable reuse, below-head enqueue skip), so fork choice never saw the
competing branch base and the node wedged at the fork height forever while the
network advanced (observed live: three mainnet peers parked at 38728 for six
days, headers_seen_total ~74k / headers_accepted_total 0).

These tests exercise the recovery pieces in isolation:
  * _is_fork_sibling_header       — the predicate
  * _enqueue_missing_blocks       — fetches a sibling block at/below head
  * _reuse_known_headers          — treats a re-served sibling as actionable
  * _should_reset_from_highest_next_height — recovery no longer disarmed when
    _network_best_height() is None (the stuck nodes all reported null)
"""
from __future__ import annotations

import asyncio
import collections
import time

import pytest

from p2p.node.p2p_service import P2PService, _SyncHeader, _max_reorg_depth


HEAD_HEIGHT = 38728
CANON = {h: bytes([h % 251]) * 32 for h in range(HEAD_HEIGHT - 200, HEAD_HEIGHT + 1)}
ORPHAN_38728 = b"\xAA" * 32  # local (losing) head at 38728
SIBLING_38728 = CANON[HEAD_HEIGHT]  # winning sibling the network built on


def _svc() -> P2PService:
    """Bare service with only the attributes the tested methods touch."""
    svc = P2PService.__new__(P2PService)
    # local chain view: canonical prefix ..38727 is shared; 38728 is the orphan
    local = dict(CANON)
    local[HEAD_HEIGHT] = ORPHAN_38728
    svc._canonical_hash_at_height = lambda h: local.get(int(h))
    svc._local_head = lambda: (HEAD_HEIGHT, "0x" + ORPHAN_38728.hex())
    # block-queue state (shape mirrors __init__)
    svc._sync_block_queue = collections.deque()
    svc._sync_block_queue_set = set()
    svc._sync_block_queue_heights = {}
    svc._sync_block_queue_limit = 1024
    svc._sync_inflight_blocks = {}
    svc._sync_block_buffer = {}
    svc._sync_wakeup = asyncio.Event()
    svc._has_block = lambda h: False
    svc._needs_local_block_replay = lambda h, height_hint=None: False
    # wedge-recovery telemetry/limits
    svc._sync_overlap_full_batches = 0
    svc._sibling_enqueue_counts = {}
    return svc


def _hdr(height: int, hash_: bytes, parent: bytes) -> _SyncHeader:
    return _SyncHeader(
        hash=hash_, parent_hash=parent, height=height, theta_micro=1, timestamp=height
    )


def test_sibling_predicate_accepts_winning_sibling():
    svc = _svc()
    sibling = _hdr(HEAD_HEIGHT, SIBLING_38728, CANON[HEAD_HEIGHT - 1])
    assert svc._is_fork_sibling_header(sibling) is True


def test_sibling_predicate_rejects_own_canonical_and_above_head():
    svc = _svc()
    own = _hdr(HEAD_HEIGHT, ORPHAN_38728, CANON[HEAD_HEIGHT - 1])
    assert svc._is_fork_sibling_header(own) is False
    above = _hdr(HEAD_HEIGHT + 1, b"\xBB" * 32, SIBLING_38728)
    assert svc._is_fork_sibling_header(above) is False


def test_sibling_predicate_rejects_unknown_parent_and_deep_forks():
    svc = _svc()
    # parent not on our chain -> not a sibling base (could be junk/spam)
    junk = _hdr(HEAD_HEIGHT, b"\xCC" * 32, b"\xDD" * 32)
    assert svc._is_fork_sibling_header(junk) is False
    # deeper than the importer's reorg bound -> not ingestible anyway
    deep_h = HEAD_HEIGHT - _max_reorg_depth() - 1
    deep = _hdr(deep_h, b"\xEE" * 32, CANON[deep_h - 1])
    assert svc._is_fork_sibling_header(deep) is False


def test_enqueue_missing_blocks_fetches_sibling_at_head_height():
    svc = _svc()
    sibling = _hdr(HEAD_HEIGHT, SIBLING_38728, CANON[HEAD_HEIGHT - 1])
    ordinary_below_head = _hdr(
        HEAD_HEIGHT - 1, CANON[HEAD_HEIGHT - 1], CANON[HEAD_HEIGHT - 2]
    )
    queued = svc._enqueue_missing_blocks([ordinary_below_head, sibling])
    assert queued == 1
    assert SIBLING_38728 in svc._sync_block_queue_set
    assert CANON[HEAD_HEIGHT - 1] not in svc._sync_block_queue_set


def test_sibling_enqueue_capped_per_height():
    svc = _svc()
    fakes = [
        _hdr(HEAD_HEIGHT, bytes([0xF0 + i]) * 32, CANON[HEAD_HEIGHT - 1])
        for i in range(6)
    ]
    queued = svc._enqueue_missing_blocks(fakes)
    assert queued == 4, "fabricated sibling floods must be capped per height"


def test_reuse_known_headers_marks_sibling_actionable():
    svc = _svc()
    svc._sync_headers = {}
    svc._sync_header_sources = {}
    svc._record_header_vote = lambda h, remote: None
    svc._sync_header_by_hash = lambda h: None
    svc._header_from_compact = lambda hc: hc  # tests pass _SyncHeader directly
    svc._sync_headers_accepted_total = 0
    noted = []
    svc._note_header_progress = lambda peer, reason: noted.append(reason)
    svc._sync_anchor_probe_hash = None
    svc._sync_anchor_probe_peer = None
    svc._sync_anchor_probe_until = 0.0
    svc._sync_not_anchored_attempts = 0
    svc._sync_recovery_attempts = 0
    svc._sync_last_recovery_action = None
    svc._sync_best_header = None
    svc._sync_update_best_header = lambda h: None
    svc._update_peer_head_table = lambda peer, **kw: None
    svc._mark_peer_anchored = lambda peer, reason=None: None

    class _Broadcast:
        successful_headers_served = 0
        last_head_advancement_at = 0.0
        tip_matches = 0

    class _Peer:
        remote = "1.2.3.4:1"
        anchored = True
        hello = {}

        def __init__(self):
            self.broadcast = _Broadcast()

    svc._stats = collections.Counter()
    sibling = _hdr(HEAD_HEIGHT, SIBLING_38728, CANON[HEAD_HEIGHT - 1])
    reused = svc._reuse_known_headers(_Peer(), [sibling])
    assert reused, "re-served winning sibling must be treated as actionable"
    assert SIBLING_38728 in svc._sync_block_queue_set


def test_reset_recovery_armed_without_network_best_height():
    svc = _svc()
    svc._network_best_height = lambda: None
    svc._sync_last_progress_at = time.time() - 10_000
    svc._sync_stall_timeout = 60.0
    svc._sync_last_header_error = None
    svc._sync_last_headers_discard_reason_counts = {"overlap_headers": 512}
    # Hard evidence the network extends past us: queued blocks above our head
    # (the live stuck nodes held ~10) — previously ignored, recovery disarmed.
    svc._sync_block_queue_heights = {b"\x01" * 32: HEAD_HEIGHT + 10}
    assert (
        svc._should_reset_from_highest_next_height(
            now=time.time(), head_height=HEAD_HEIGHT
        )
        is True
    )
    # ...but with NO evidence at all it must stay disarmed (no false resets).
    svc._sync_block_queue_heights = {}
    svc._sync_max_seen_header_height = 0
    assert (
        svc._should_reset_from_highest_next_height(
            now=time.time(), head_height=HEAD_HEIGHT
        )
        is False
    )
