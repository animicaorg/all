"""END-TO-END self-heal reproduction for the 44854 / 38728 fork wedge.

Question under test: on CURRENT code (8.0.x, which already contains the 7.2.0
fork-sibling ingest), does a node that accepted the LOSING orphan sibling at a
fork height N self-heal during ORDINARY p2p sync — no pinned checkpoint, no
rpc-pull HTTP fallback — once the network is on the winning sibling and has
advanced past N?

This drives the REAL header-processing entry point `_process_headers` with a
peer that serves exactly what a winning-chain peer WOULD serve to a node whose
locator's highest on-peer-chain hash is canonical N-1: the winning sibling at N
(fork point) followed by the canonical children N+1, N+2. It then drains the
resulting block queue through a faithful stand-in importer that models the real
`core/chain/block_import.py` semantics that matter here:

  * a block whose parent is missing is buffered as an ORPHAN (import_block ->
    _remember_orphan), and re-imported when its parent lands (_process_orphans),
  * fork choice is longest-branch-wins with first-seen tie-break, reorg depth
    bounded by DEFAULT_MAX_REORG_DEPTH=96 (here depth 1),

so the assertion is on the SAME head-advance the real importer performs. No
sockets/ports are bound (other p2p suites collide on ports); the service is
built bare via P2PService.__new__ exactly like test_fork_sibling_recovery.py.
"""
from __future__ import annotations

import collections
import time

from p2p.node.p2p_service import P2PService, _SyncHeader, _max_reorg_depth


N = 44854  # fork height (matches the 2026-07-14 natural 1-block fork)
# canonical prefix hashes are a single repeated byte; the special markers below
# are deliberately NON-repeated so they can never collide with a CANON value.
CANON = {h: bytes([1 + (h % 250)]) * 32 for h in range(N - 200, N)}  # ..N-1 shared
ORPHAN_N = b"ORPHAN_N" + b"\x00" * 24   # local (losing) head at N
SIBLING_N = b"SIBLING_N" + b"\x00" * 23  # winning sibling at N the network built on
BLOCK_N1 = b"BLOCK_N+1" + b"\x00" * 23   # canonical N+1 (parent = SIBLING_N)
BLOCK_N2 = b"BLOCK_N+2" + b"\x00" * 23   # canonical N+2 (parent = BLOCK_N1)


def _hdr(height, hash_, parent, ts=None):
    return _SyncHeader(
        hash=hash_,
        parent_hash=parent,
        height=height,
        theta_micro=1,
        timestamp=ts if ts is not None else height,
    )


class _Broadcast:
    def __init__(self):
        self.successful_headers_served = 0
        self.last_head_advancement_at = 0.0
        self.duplicate_header_batches = 0
        self.errors = 0
        self.tip_matches = 0


class _Peer:
    def __init__(self):
        self.remote = "9.9.9.9:1"
        self.anchored = True
        self.hello = {}
        self.empty_header_responses = 0
        self.broadcast = _Broadcast()


def _svc():
    """Bare P2PService wired with faithful, socket-free stand-ins.

    Local chain: canonical prefix ..N-1, and the ORPHAN at N is the head.
    The node knows headers/blocks for the canonical prefix + its own orphan; it
    does NOT know the winning sibling, N+1 or N+2 until the peer serves them.
    """
    svc = P2PService.__new__(P2PService)

    local_canon = dict(CANON)
    local_canon[N] = ORPHAN_N  # our canonical (losing) head at N

    # header meta the node holds: canonical prefix + own orphan head
    known_meta = {h: (height, height) for height, h in CANON.items()}
    known_meta[ORPHAN_N] = (N, N)

    svc._canonical_hash_at_height = lambda h: local_canon.get(int(h))
    svc._local_head = lambda: (N, "0x" + ORPHAN_N.hex())
    svc._parse_hash_bytes = lambda s: bytes.fromhex(s[2:] if s.startswith("0x") else s)
    svc._header_meta = lambda h: known_meta.get(bytes(h))
    svc._has_header = lambda h: bytes(h) in known_meta
    svc._checkpoint_parent_meta = lambda h: None
    svc._anchor_candidates = lambda: {}
    svc._anchor_candidates_summary = lambda: {}
    svc._should_enforce_checkpoint_anchor = lambda: False
    svc._peer_is_anchored = lambda peer: True
    svc._is_sync_target_ahead = lambda h: False
    svc._genesis_header_hash = lambda: b"\x00" * 32
    svc._genesis_block_hash = lambda: b"\x00" * 32
    svc._canon_hash0x = lambda b: ("0x" + bytes(b).hex()) if b else None
    svc._max_headers_per_message = 512

    # header/compact passthrough (we feed _SyncHeader directly, as the sibling test does)
    svc._header_from_compact = lambda hc: hc
    svc._sync_header_by_hash = lambda h: svc._sync_headers.get(bytes(h))

    # no-op telemetry / anchor bookkeeping
    svc._record_header_vote = lambda h, remote: None
    svc._note_header_progress = lambda peer, reason: None
    svc._mark_peer_anchored = lambda peer, reason=None: None
    svc._sync_update_best_header = lambda h: None
    svc._update_peer_head_table = lambda peer, **kw: None
    svc._update_matched_ancestor = lambda *a, **kw: None
    svc._update_checkpoint_validation = lambda contiguous: None
    svc._has_block = lambda h: bytes(h) in known_meta
    svc._needs_local_block_replay = lambda h, height_hint=None: False

    # If the pipeline ever bails to the not-anchored / reject paths, the batch is
    # rejected and NOTHING gets enqueued -> that IS the wedge. Record it loudly.
    svc.wedged_calls = []

    def _note_not_anchored(peer, *, header, anchor_height, anchor_hash, reason, allow_probe=False):
        svc.wedged_calls.append(("not_anchored", reason, int(header.height)))
        return [], reason

    def _log_header_reject(peer, header, *, reason, parent_ts=None):
        svc.wedged_calls.append(("reject", reason, int(header.height)))

    svc._note_not_anchored = _note_not_anchored
    svc._log_header_reject = _log_header_reject
    svc._penalize_peer = lambda peer, reason, severity=1: None

    # sync/header state
    svc._sync_headers = {}
    svc._sync_header_sources = {}
    svc._sync_headers_seen_total = 0
    svc._sync_headers_accepted_total = 0
    svc._sync_best_header = None
    svc._sync_last_anchor_check = {}
    svc._sync_locator_depth_hint = 0
    svc._sync_anchor_probe_hash = None
    svc._sync_anchor_probe_peer = None
    svc._sync_anchor_probe_until = 0.0
    svc._sync_not_anchored_attempts = 0
    svc._sync_recovery_attempts = 0
    svc._sync_last_recovery_action = None
    svc._sync_target_height = None
    svc._network_best_height = lambda: N + 2
    svc._stats = collections.Counter()
    svc._sync_overlap_full_batches = 0

    # block-queue state (shape mirrors __init__)
    svc._sync_block_queue = collections.deque()
    svc._sync_block_queue_set = set()
    svc._sync_block_queue_heights = {}
    svc._sync_block_queue_limit = 1024
    svc._sync_inflight_blocks = {}
    svc._sync_block_buffer = {}

    class _Ev:
        def set(self):
            pass

    svc._sync_wakeup = _Ev()
    svc._sibling_enqueue_counts = {}
    return svc


class _StandInImporter:
    """Faithful model of the block_import head-advance path for this scenario.

    blocks: hash -> (height, parent_hash). canonical head = longest branch,
    first-seen tie-break. Missing-parent blocks are buffered as orphans and
    replayed when their parent arrives (import_block/_remember_orphan/
    _process_orphans). Reorg is unconditional up to depth 96 (here depth 1).
    """

    def __init__(self):
        self.blocks = {}
        self.orphans_by_parent = collections.defaultdict(list)
        # seed canonical prefix ..N-1 and the losing orphan head at N
        for height, h in CANON.items():
            parent = CANON.get(height - 1, b"\x00" * 32)
            self.blocks[h] = (height, parent)
        self.blocks[ORPHAN_N] = (N, CANON[N - 1])
        self.head_hash = ORPHAN_N
        self.head_height = N

    def import_block(self, block_hash, height, parent_hash):
        if block_hash in self.blocks:
            return "duplicate"
        if parent_hash not in self.blocks:
            self.orphans_by_parent[parent_hash].append((block_hash, height, parent_hash))
            return "orphan"
        # store + fork choice (longest branch wins; strictly greater height only)
        self.blocks[block_hash] = (height, parent_hash)
        if height > self.head_height and (N + 2 - height) <= _max_reorg_depth():
            self.head_hash = block_hash
            self.head_height = height
        # cascade any orphans that were waiting on this block
        for child in self.orphans_by_parent.pop(block_hash, []):
            self.import_block(*child)
        return "accepted"


def _drain(svc, importer):
    """Fetch+import queued blocks in height order, like the block worker loop."""
    hdr_by_hash = {
        SIBLING_N: (N, CANON[N - 1]),
        BLOCK_N1: (N + 1, SIBLING_N),
        BLOCK_N2: (N + 2, BLOCK_N1),
    }
    # order by height as _enqueue_missing_blocks / the drain loop do
    ordered = sorted(svc._sync_block_queue, key=lambda hh: svc._sync_block_queue_heights[hh])
    for h in ordered:
        height, parent = hdr_by_hash[h]
        importer.import_block(h, height, parent)


def test_node_self_heals_from_losing_orphan_via_ordinary_sync():
    svc = _svc()
    peer = _Peer()

    # What a winning-chain peer serves a node whose locator's highest on-peer
    # hash is canonical N-1: the winning sibling at N, then N+1, N+2. (See
    # _headers_after_locator: serves anchor_height+1.. from ITS canonical chain.)
    served = [
        _hdr(N, SIBLING_N, CANON[N - 1]),
        _hdr(N + 1, BLOCK_N1, SIBLING_N),
        _hdr(N + 2, BLOCK_N2, BLOCK_N1),
    ]

    order, reason, discard = svc._process_headers(peer, served)

    # The header pipeline must NOT bail to the not-anchored/reject path.
    assert not svc.wedged_calls, f"header pipeline wedged: {svc.wedged_calls}"
    assert reason is None, f"unexpected header reason: {reason} discard={discard}"

    # All three winning-branch blocks must be queued for fetch, including the
    # sibling at the fork height (the piece the legacy pipeline dropped).
    assert SIBLING_N in svc._sync_block_queue_set, "winning sibling at N not enqueued"
    assert BLOCK_N1 in svc._sync_block_queue_set, "canonical N+1 not enqueued"
    assert BLOCK_N2 in svc._sync_block_queue_set, "canonical N+2 not enqueued"

    # Drain the queue through the faithful importer and assert the reorg lands.
    importer = _StandInImporter()
    assert importer.head_hash == ORPHAN_N and importer.head_height == N
    _drain(svc, importer)

    assert importer.head_height == N + 2, "node did not advance past the fork height"
    assert importer.head_hash == BLOCK_N2, "node did not converge onto the winning branch"


def test_out_of_order_block_arrival_still_self_heals():
    """N+1 fetched before the sibling at N: importer buffers it as an orphan and
    the _process_orphans cascade lands it once the sibling imports."""
    importer = _StandInImporter()
    # deliberately import N+1 first (parent = sibling, not yet present)
    assert importer.import_block(BLOCK_N1, N + 1, SIBLING_N) == "orphan"
    assert importer.head_height == N  # still wedged on the orphan
    # now the sibling at N lands -> cascade replays N+1
    importer.import_block(SIBLING_N, N, CANON[N - 1])
    assert importer.head_height == N + 1
    assert importer.head_hash == BLOCK_N1


def test_reuse_path_also_enqueues_winning_sibling():
    """If a peer RE-serves an already-buffered batch, _reuse_known_headers must
    still surface the winning sibling as actionable (the other live wedge vector)."""
    svc = _svc()
    peer = _Peer()
    sibling = _hdr(N, SIBLING_N, CANON[N - 1])
    reused = svc._reuse_known_headers(peer, [sibling])
    assert reused, "re-served winning sibling must be actionable"
    assert SIBLING_N in svc._sync_block_queue_set
