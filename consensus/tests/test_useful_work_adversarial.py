"""Adversarial regression suite for FORK_USEFUL_WORK_VERIFY.

Every test here started life as a working proof-of-concept attack against the
first revision of ``consensus/useful_work_verify.py`` +
``core/chain/block_import._ImporterChainView``. They are kept as tests so the
attacks cannot come back, and — for the one attack that CANNOT be fixed in this
design — so nobody mistakes the rule for something it is not.

Two categories, and the difference matters:

  FIXED   — the attack is now rejected. The test asserts the rejection AND, where
            it is cheap, asserts that the pre-fix path is genuinely gone.
  PINNED  — the attack still works and always will in this shape. The test
            asserts the ATTACK SUCCEEDS, so that a future reader who believes
            "the receipt binding forces real inference" is contradicted by a
            green test rather than by a paragraph of prose.

Attack inventory:

  1. FORGERY (PINNED)   a miner running a null worker signs a receipt for work it
                        never did. Passes every check, by design: check 2
                        REQUIRES worker == coinbase, so the signer is always the
                        miner.
  2. ZERO-COST PAYMENT (FIXED)  a TRANSFER that reverts for insufficient balance
                        is included in a block, indexed, and moved nothing and
                        paid no fee — yet satisfied "the requester paid 1 ANM".
  3. ORPHAN SPLIT (FIXED)  a node that saw an orphan burned the receipt's
                        nullifier in memory and then rejected the canonical block
                        that re-used it, while a node that never saw the orphan
                        accepted. Two honest nodes, permanent partition.
  4. ORPHAN PAYMENT (FIXED)  the global tx index is never pruned on reorg, so a
                        payment that exists only on a detached branch funded a
                        proof on the canonical chain.
  5. RESTART DIVERGENCE (FIXED)  the nullifier store was in-memory, so a
                        restarted node re-accepted what a long-uptime node
                        rejected.
  6. BUILD SPLIT (FIXED)  a node missing the vendored PQ backend rejected every
                        proof with a reason indistinguishable from a bad
                        signature.
"""

from __future__ import annotations

import time

import pytest

from core.types.receipt import ReceiptStatus
from core.utils.hash import sha3_256
from mempool.tx_hash import tx_hash_bytes

from consensus.ai_work_proof import account_digest_for_pubkey
from consensus.useful_work_verify import (
    PAYMENT_EXECUTED,
    POLICY_V1,
    verify_ai_work_proof,
)

from consensus.tests.test_useful_work_verify import (
    HEADER_HASH,
    HEIGHT,
    PARENT_HASH,
    TREASURY,
    _FakeChainDB,
    _gate_block,
    _make_importer,
    _transfer_tx,
    make_proof_body,
    paid,
    wrap_proof,
    FakeChain,
    make_context,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ═════════════════════════════ shared harness ════════════════════════════════
#
# The fixtures are re-declared rather than imported: pytest resolves fixtures by
# name in the collecting module, so importing the function object from the sister
# test module would give a plain function, not a fixture.


@pytest.fixture(scope="module")
def worker_keys():
    import pq.py.algs.ml_dsa_65 as m

    if not m.is_available():  # pragma: no cover - environment guard
        pytest.skip("ML-DSA-65 backend unavailable")
    return m.generate_keypair()


@pytest.fixture(scope="module")
def requester_digest() -> bytes:
    return sha3_256(b"requester-account-key")


def _code_of(obj) -> str:
    """Source with docstrings and comments stripped.

    Structural guards below assert that certain APIs are not CALLED. Grepping raw
    source would match the paragraphs that explain why those APIs must not be
    called, which is the opposite of useful.
    """
    import ast
    import inspect
    import textwrap

    def _is_doc(stmt) -> bool:
        return (
            isinstance(stmt, ast.Expr)
            and isinstance(getattr(stmt, "value", None), ast.Constant)
            and isinstance(stmt.value.value, str)
        )

    tree = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            # Drops module/class/function docstrings AND the attribute docstrings
            # PEP 257 allows after a field — both are prose, not calls.
            node.body = [s for s in body if not _is_doc(s)] or [ast.Pass()]
    return ast.unparse(tree)


def _chain_with_payment(requester, *, pay_height=HEIGHT - 20, status=None, depth=80):
    """A walkable fake chain whose block at ``pay_height`` carries a 1 ANM
    transfer from ``requester`` to the AICF treasury."""
    db = _FakeChainDB(tip_height=HEIGHT - 1, depth=depth)
    tx = _transfer_tx(requester, amount=POLICY_V1.payment_floor_base_units)
    db.include_tx(tx, height=pay_height, status=status)
    return db, bytes(tx_hash_bytes(tx))


def _gate(imp, block, *, height=HEIGHT, parent_hash=PARENT_HASH):
    return imp._verify_block_useful_work_gated(
        block=block,
        header=block.header,
        header_hash=HEADER_HASH,
        parent_hash=parent_hash,
        height=height,
    )


@pytest.fixture
def armed(monkeypatch):
    """Fork armed, enforcing (no shadow)."""
    monkeypatch.setenv("ANIMICA_FORK_USEFUL_WORK_VERIFY_HEIGHT", str(HEIGHT - 1))
    monkeypatch.delenv("ANIMICA_USEFUL_WORK_SHADOW", raising=False)
    return monkeypatch


# ═══════════════ 1. FORGERY — PINNED, this is what the rule does ═════════════


def test_forged_proof_for_zero_inference_is_accepted(worker_keys, requester_digest, armed):
    """PINNED FAILURE, NOT A BUG REPORT.

    A miner runs NO model. It invents a jobId, sets outputDigest to
    sha3_256(b"garbage"), self-reports 999,999 output tokens, signs with its own
    coinbase key, and pays itself 1 ANM through a second identity. The block is
    ACCEPTED, and check 2 (worker == coinbase) is what makes that the only
    permitted shape: a miner buying inference from a real third-party GPU worker
    could not attach that worker's receipt at all.

    If this test ever starts failing because the rule got stricter, good — but
    delete it deliberately, and update the FORGERY VERDICT in the verifier
    docstring at the same time.
    """
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    db, pay_hash = _chain_with_payment(requester_digest)
    imp = _make_importer(db)

    body = make_proof_body(
        worker_keys,
        requester_digest,
        anchor_hash=db.hash_at(HEIGHT - 1),
        payment_tx=pay_hash,
        job_id=sha3_256(b"a job i invented"),
        output_digest=sha3_256(b"garbage"),  # no model produced this
        tokens_in=1,
        tokens_out=999_999,  # self-reported
    )
    block = _gate_block(worker, [wrap_proof(body)])

    assert _gate(imp, block) is None, (
        "the forgery is expected to pass — this rule does not verify inference"
    )


def test_a_third_party_workers_receipt_can_never_be_attached(worker_keys, requester_digest, armed):
    """The flip side of check 2, also pinned: honest sourcing is INVALID.

    A miner that genuinely buys inference from someone else's GPU holds a receipt
    signed by that worker's key, whose digest is not the coinbase. The rule
    rejects it. Self-dealing is not merely permitted, it is mandatory.
    """
    pk, _ = worker_keys
    real_miner = sha3_256(b"the actual coinbase of this block")
    db, pay_hash = _chain_with_payment(requester_digest)
    imp = _make_importer(db)

    body = make_proof_body(
        worker_keys,  # a genuine third-party worker's key
        requester_digest,
        anchor_hash=db.hash_at(HEIGHT - 1),
        payment_tx=pay_hash,
    )
    block = _gate_block(real_miner, [wrap_proof(body)])
    reason = _gate(imp, block)
    assert reason is not None and "worker_not_miner" in reason, reason


# ═════════════ 2. ZERO-COST PAYMENT — FIXED (was the 0 ANM attack) ═══════════


def test_reverted_payment_is_rejected(worker_keys, requester_digest, armed):
    """An under-funded TRANSFER raises InsufficientBalance BEFORE any debit, so
    apply_block records REVERT with gas_used=0 and the journal rolls it back —
    the block stays valid and the tx index entry is still written. Reading only
    the transaction, the old check saw sender=requester, to=treasury,
    amount=1 ANM and passed. Cost to the attacker: 0 ANM principal, 0 ANM fee.
    """
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    db, pay_hash = _chain_with_payment(requester_digest, status=ReceiptStatus.REVERT)
    imp = _make_importer(db)

    body = make_proof_body(
        worker_keys,
        requester_digest,
        anchor_hash=db.hash_at(HEIGHT - 1),
        payment_tx=pay_hash,
    )
    block = _gate_block(worker, [wrap_proof(body)])
    reason = _gate(imp, block)
    assert reason is not None and "payment_reverted" in reason, reason


def test_out_of_gas_payment_is_rejected(worker_keys, requester_digest, armed):
    """Same hole, other non-SUCCESS status."""
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    db, pay_hash = _chain_with_payment(requester_digest, status=ReceiptStatus.OOG)
    imp = _make_importer(db)
    body = make_proof_body(
        worker_keys,
        requester_digest,
        anchor_hash=db.hash_at(HEIGHT - 1),
        payment_tx=pay_hash,
    )
    reason = _gate(imp, _gate_block(worker, [wrap_proof(body)]))
    assert reason is not None and "payment_reverted" in reason, reason


def test_missing_receipt_fails_closed(worker_keys, requester_digest, armed):
    """No receipt on this node -> ``payment_status_unknown``, never "assume it
    worked". This is THE BLOCKER: a non-zero rate of this reason in shadow
    telemetry means an enforcing fleet would split between nodes that executed a
    range and nodes that snapshot-synced past it.
    """
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    db, pay_hash = _chain_with_payment(requester_digest)
    # Simulate a node that has the block but not the receipt side-table entry.
    for h_by, raw in list(db._receipts.items()):
        db._receipts.pop(h_by)
    imp = _make_importer(db)
    body = make_proof_body(
        worker_keys,
        requester_digest,
        anchor_hash=db.hash_at(HEIGHT - 1),
        payment_tx=pay_hash,
    )
    reason = _gate(imp, _gate_block(worker, [wrap_proof(body)]))
    assert reason is not None and "payment_status_unknown" in reason, reason


def test_payment_record_defaults_reject(requester_digest):
    """A ChainView written against the old three-field PaymentRecord shape must
    fail CLOSED, not silently accept. The dataclass defaults are the rejecting
    values, so forgetting to set them cannot re-open the hole."""
    from consensus.useful_work_verify import PaymentRecord

    rec = PaymentRecord(
        sender=requester_digest,
        to=TREASURY,
        amount=POLICY_V1.payment_floor_base_units,
        height=HEIGHT - 20,
    )
    assert rec.in_ancestry is False
    assert rec.status != PAYMENT_EXECUTED


# ═══════ 3./5. ORPHAN SPLIT + RESTART DIVERGENCE — FIXED (no local state) ════


def test_orphaned_block_does_not_poison_the_canonical_chain(
    worker_keys, requester_digest, armed
):
    """The chain-split PoC, inverted into a regression test.

    Node A imported an orphan carrying receipt R at (H, P1); node B never saw it.
    The canonical block at (H, P2) re-uses R. With an in-memory nullifier store
    node A rejected and node B accepted — a permanent partition from an ordinary
    orphan, no attacker required.

    Now both nodes derive replay from the CANONICAL block's own ancestry, so the
    orphan is invisible to both and they agree.
    """
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)

    # Node A: same DB object reused across imports, i.e. maximal "memory".
    db_a, pay_hash = _chain_with_payment(requester_digest)
    node_a = _make_importer(db_a)

    # The orphan: a valid block at HEIGHT on a competing parent. Node A imports
    # it (the gate accepts), which under the old design burned the tag.
    orphan_parent = db_a.hash_at(HEIGHT - 1)
    orphan_body = make_proof_body(
        worker_keys,
        requester_digest,
        anchor_hash=orphan_parent,
        payment_tx=pay_hash,
    )
    orphan = _gate_block(worker, [wrap_proof(orphan_body)])
    assert _gate(node_a, orphan) is None

    # The canonical winner at the same height re-uses the same receipt.
    canonical = _gate_block(worker, [wrap_proof(orphan_body)])
    verdict_a = _gate(node_a, canonical)

    # Node B is a fresh process that never saw the orphan.
    db_b, pay_hash_b = _chain_with_payment(requester_digest)
    assert pay_hash_b == pay_hash
    node_b = _make_importer(db_b)
    verdict_b = _gate(node_b, canonical)

    assert verdict_a == verdict_b, (
        f"nodes disagree about the same canonical block: A={verdict_a!r} B={verdict_b!r}"
    )
    assert verdict_a is None


def test_restart_does_not_change_the_verdict(worker_keys, requester_digest, armed):
    """A long-uptime node and a just-restarted node must agree. The old store was
    per-BlockImporter memory, so they did not."""
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    db, pay_hash = _chain_with_payment(requester_digest)
    body = make_proof_body(
        worker_keys,
        requester_digest,
        anchor_hash=db.hash_at(HEIGHT - 1),
        payment_tx=pay_hash,
    )
    block = _gate_block(worker, [wrap_proof(body)])

    long_uptime = _make_importer(db)
    for _ in range(3):  # plenty of chances to accumulate node-local state
        first = _gate(long_uptime, block)
    just_restarted = _make_importer(db)
    assert first == _gate(just_restarted, block) == None  # noqa: E711


def test_importer_holds_no_useful_work_replay_state():
    """Structural guard: if a nullifier store is ever re-added to the importer,
    the orphan-split and restart-divergence bugs come back with it."""
    from core.chain.block_import import BlockImporter

    assert "_useful_work_nullifiers" not in getattr(BlockImporter, "__slots__", ())
    assert not hasattr(BlockImporter, "_useful_work_nullifier_store")


# ═════════════ 4. ORPHAN PAYMENT — FIXED (ancestry-scoped lookup) ════════════


def test_payment_outside_the_ancestry_is_rejected(worker_keys, requester_digest, armed):
    """``block_db.get_transaction_by_hash`` reads a global, canonical-height index
    that is never deleted on reorg, so a payment living only on a detached branch
    used to fund a proof on the canonical chain. The view now reads the block's
    own ancestors, so a tx that is not in them is simply not found."""
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)

    db = _FakeChainDB(tip_height=HEIGHT - 1, depth=80)
    # The payment exists as an object and would be resolvable through a global
    # index, but it is included in NO ancestor block.
    orphan_tx = _transfer_tx(requester_digest, amount=POLICY_V1.payment_floor_base_units)
    imp = _make_importer(db)

    body = make_proof_body(
        worker_keys,
        requester_digest,
        anchor_hash=db.hash_at(HEIGHT - 1),
        payment_tx=bytes(tx_hash_bytes(orphan_tx)),
    )
    reason = _gate(imp, _gate_block(worker, [wrap_proof(body)]))
    assert reason is not None and "payment_unresolved" in reason, reason


def test_payment_older_than_the_window_is_rejected(worker_keys, requester_digest, armed):
    """The scan window is exactly ``payment_max_age``; a payment below it is out
    of policy AND out of scan range, and both agree on rejecting."""
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    too_old = HEIGHT - POLICY_V1.payment_max_age - 5
    db, pay_hash = _chain_with_payment(requester_digest, pay_height=too_old, depth=120)
    imp = _make_importer(db)
    body = make_proof_body(
        worker_keys,
        requester_digest,
        anchor_hash=db.hash_at(HEIGHT - 1),
        payment_tx=pay_hash,
    )
    reason = _gate(imp, _gate_block(worker, [wrap_proof(body)]))
    assert reason is not None and (
        "payment_unresolved" in reason or "payment_too_old" in reason
    ), reason


def test_pruned_ancestor_body_fails_closed(worker_keys, requester_digest, armed):
    """"Could not check" must never read as "not seen"."""
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    db, pay_hash = _chain_with_payment(requester_digest)
    db.prune_body(height=HEIGHT - 30)  # header stays, body gone
    imp = _make_importer(db)
    body = make_proof_body(
        worker_keys,
        requester_digest,
        anchor_hash=db.hash_at(HEIGHT - 1),
        payment_tx=pay_hash,
    )
    reason = _gate(imp, _gate_block(worker, [wrap_proof(body)]))
    assert reason is not None and "nullifier_scan_incomplete" in reason, reason


def test_incomplete_scan_is_not_treated_as_unseen(worker_keys, requester_digest):
    """Same rule at the pure-verifier level: a None from the ChainView rejects."""
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    from consensus.tests.test_useful_work_verify import ANCHOR_HASH, PAYMENT_TX

    chain = FakeChain(
        ancestors={HEIGHT - 1: ANCHOR_HASH},
        payments={PAYMENT_TX: paid(requester_digest)},
        scan_complete=False,
    )
    ctx = make_context(worker_digest=worker, requester=requester_digest, chain=chain)
    body = make_proof_body(worker_keys, requester_digest)
    ok, reason, _psi, _n = verify_ai_work_proof(body, ctx)
    assert ok is False and reason == "nullifier_scan_incomplete"


# ══════════════════ 6. BUILD SPLIT — FIXED (distinct reason) ═════════════════


def test_missing_pq_backend_is_distinguishable_from_a_bad_signature(
    worker_keys, requester_digest, monkeypatch
):
    """A node missing ``animica._vendor.dilithium_py_v2`` rejects every proof.
    That is a BUILD DEFECT that splits this node from the network, and it must
    not be reported with the same string as an ordinary invalid signature — one
    is fixed by reinstalling, the other by rejecting the block."""
    import consensus.useful_work_verify as uwv

    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    ctx = make_context(worker_digest=worker, requester=requester_digest)
    body = make_proof_body(worker_keys, requester_digest)

    ok, reason, _psi, _n = verify_ai_work_proof(body, ctx)
    assert ok is True, reason

    monkeypatch.setattr(uwv, "pq_backend_available", lambda: False)
    ok2, reason2, _psi2, _n2 = verify_ai_work_proof(body, ctx)
    assert ok2 is False
    assert reason2 == "worker_sig_backend_unavailable"
    assert reason2 != "worker_sig_invalid"


def test_pq_backend_probe_reports_this_build_as_usable():
    """If this ever fails in CI, the build cannot verify a single proof."""
    from consensus.useful_work_verify import pq_backend_available

    assert pq_backend_available() is True


# ════════════════════════ cost / ordering / invariants ══════════════════════


def test_cheap_identity_checks_run_before_the_signature_verify(
    worker_keys, requester_digest
):
    """ML-DSA-65 verification is ~20 ms of pure Python; ``worker == coinbase`` is
    a 32-byte compare. A proof that fails the compare must not pay for the
    signature — otherwise 8 junk proofs per block cost ~1000x more to reject than
    they cost to make."""
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest)

    good_ctx = make_context(worker_digest=worker, requester=requester_digest)
    t0 = time.perf_counter()
    ok, _r, _p, _n = verify_ai_work_proof(body, good_ctx)
    full = time.perf_counter() - t0
    assert ok

    wrong_miner = make_context(
        worker_digest=worker,
        requester=requester_digest,
        miner_digest=sha3_256(b"a different miner"),
    )
    t0 = time.perf_counter()
    ok2, reason2, _p2, _n2 = verify_ai_work_proof(body, wrong_miner)
    early = time.perf_counter() - t0
    assert ok2 is False and reason2 == "worker_not_miner"
    assert early < full / 2, (
        f"early reject took {early:.4f}s vs full {full:.4f}s — the signature "
        "verify is probably still running first"
    )


def test_ancestor_walk_happens_once_per_block(worker_keys, requester_digest, armed):
    """Eight proofs must cost ONE ancestry traversal, not eight. Without
    memoization a block forces max_proofs_per_block * payment_max_age header
    reads."""
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    db, pay_hash = _chain_with_payment(requester_digest)

    calls = {"n": 0}
    real = db.get_block_by_hash

    def counting(h):
        calls["n"] += 1
        return real(h)

    db.get_block_by_hash = counting
    imp = _make_importer(db)

    proofs = [
        wrap_proof(
            make_proof_body(
                worker_keys,
                requester_digest,
                anchor_hash=db.hash_at(HEIGHT - 1),
                payment_tx=pay_hash,
                job_id=bytes([i]) * 32,
            )
        )
        for i in range(4)
    ]
    _gate(imp, _gate_block(worker, proofs))
    # One walk over the window, and nothing more.
    assert calls["n"] <= POLICY_V1.payment_max_age + 2, calls["n"]


def test_policy_windows_are_consistent():
    """The replay-scan completeness proof (verifier docstring, check 5) needs
    payment_max_age >= anchor_window. A silent tweak to either constant would
    re-open a replay window that no test would otherwise notice."""
    assert POLICY_V1.payment_max_age >= POLICY_V1.anchor_window
    assert POLICY_V1.anchor_window > 0


def test_verifier_never_consults_a_nullifier_store():
    """Structural guard against the old API coming back."""
    import consensus.useful_work_verify as uwv

    code = _code_of(uwv)
    assert "nullifier_seen" not in code
    assert "NullifierStore" not in code
    assert "nullifier_used_in_ancestry" in code


def test_chain_view_reads_no_global_transaction_index():
    """The ancestry-scoped fix, guarded structurally: the view must not reach for
    ``get_transaction_by_hash`` (a canonical-height index whose entries survive a
    reorg) or ``get_canonical_hash``."""
    from core.chain.block_import import _ImporterChainView

    code = _code_of(_ImporterChainView)
    assert "get_transaction_by_hash" not in code
    assert "get_canonical_hash" not in code
    assert "get_block_by_hash" in code
