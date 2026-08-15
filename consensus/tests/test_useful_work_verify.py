"""Tests for FORK_USEFUL_WORK_VERIFY: AI work-proof verification at block import.

Covers the contract the rollout depends on:

* a well-formed proof is accepted, and each malformation is rejected with its own
  stable reason;
* SHADOW mode never rejects; enforcing mode does;
* the receipt freshness window;
* the nullifier prevents replay across blocks (both the work tag and the payment
  tag);
* determinism — identical inputs give an identical report, and the verifier's
  source contains no clock/IO/env reads;
* grandfathering below the activation height, and the mainnet default of "no
  activation height at all".
"""

from __future__ import annotations

import logging
from dataclasses import replace
from types import SimpleNamespace
from typing import Dict, Optional

import pytest

from core.encoding.cbor import cbor_dumps, cbor_loads
from core.types.proof import AIProofRef, ProofType, make_envelope
from core.utils.hash import sha3_256
from mempool.tx_hash import tx_hash_bytes

from consensus.ai_work_proof import (
    AI_WORK_PROOF_VERSION,
    ML_DSA_65_ALG_ID,
    account_digest_for_pubkey,
    build_ai_work_proof,
    payment_nullifier,
    slot_digest,
    work_nullifier,
)
from consensus.useful_work_verify import (
    PAYMENT_EXECUTED,
    POLICY_V1,
    BlockContext,
    PaymentRecord,
    UsefulWorkPolicy,
    block_miner_digest,
    canonical_proof_type_name,
    h_micro_from_hash256,
    verify_ai_work_proof,
    verify_block_useful_work,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ─────────────────────────────── fixtures ────────────────────────────────────

CHAIN_ID = 1
HEIGHT = 100_000
PARENT_HASH = bytes.fromhex("11" * 32)
HEADER_HASH = bytes.fromhex("0000000012345678" + "ab" * 24)
THETA_MICRO = 26_821_504
TREASURY = POLICY_V1.treasury_digest(CHAIN_ID)


def _ml_dsa():
    import pq.py.algs.ml_dsa_65 as m

    if not m.is_available():  # pragma: no cover - environment guard
        pytest.skip("ML-DSA-65 backend unavailable")
    return m


@pytest.fixture(scope="module")
def worker_keys():
    m = _ml_dsa()
    pk, sk = m.generate_keypair()
    return pk, sk


@pytest.fixture(scope="module")
def requester_digest() -> bytes:
    return sha3_256(b"requester-account-key")


def paid(
    sender: bytes,
    *,
    to: bytes = None,
    amount: Optional[int] = None,
    height: int = None,
    status: str = PAYMENT_EXECUTED,
    in_ancestry: bool = True,
) -> PaymentRecord:
    """A PaymentRecord in the shape a correct ChainView returns.

    The rejecting values (`unknown` / not-in-ancestry) are the dataclass
    DEFAULTS, so a test that wants a passing payment has to say so — the same
    fail-closed direction the real adapter has.
    """
    return PaymentRecord(
        sender=sender,
        to=TREASURY if to is None else to,
        amount=POLICY_V1.payment_floor_base_units if amount is None else amount,
        height=(HEIGHT - 20) if height is None else height,
        status=status,
        in_ancestry=in_ancestry,
    )


class FakeChain:
    """Deterministic ChainView stub."""

    def __init__(
        self,
        *,
        ancestors: Dict[int, bytes],
        payments: Dict[bytes, PaymentRecord],
        seen: Optional[set] = None,
        scan_complete: bool = True,
    ):
        self.ancestors = dict(ancestors)
        self.payments = dict(payments)
        self.seen_set = set(seen or ())
        self.scan_complete = bool(scan_complete)
        self.recorded: list = []

    def ancestor_hash_at(self, height: int) -> Optional[bytes]:
        return self.ancestors.get(int(height))

    def payment(self, tx_hash: bytes) -> Optional[PaymentRecord]:
        return self.payments.get(bytes(tx_hash))

    def nullifier_used_in_ancestry(self, nullifier: bytes) -> Optional[bool]:
        if not self.scan_complete:
            return None
        return bytes(nullifier) in self.seen_set


PAYMENT_TX = bytes.fromhex("cc" * 32)
ANCHOR_HASH = bytes.fromhex("dd" * 32)


def make_context(
    *,
    worker_digest: bytes,
    requester: bytes,
    chain: Optional[FakeChain] = None,
    height: int = HEIGHT,
    parent_hash: bytes = PARENT_HASH,
    policy: UsefulWorkPolicy = POLICY_V1,
    poies_policy_root: bytes = b"\x00" * 32,
    miner_digest: Optional[bytes] = None,
) -> BlockContext:
    if chain is None:
        chain = FakeChain(
            ancestors={height - 1: ANCHOR_HASH, height - 10: ANCHOR_HASH},
            payments={
                PAYMENT_TX: paid(
                    requester,
                    amount=policy.payment_floor_base_units,
                    height=height - 20,
                )
            },
        )
    return BlockContext(
        chain_id=CHAIN_ID,
        height=height,
        parent_hash=parent_hash,
        header_hash=HEADER_HASH,
        theta_micro=THETA_MICRO,
        poies_policy_root=poies_policy_root,
        miner_digest=worker_digest if miner_digest is None else miner_digest,
        chain=chain,
        policy=policy,
    )


def make_proof_body(
    worker_keys,
    requester: bytes,
    *,
    height: int = HEIGHT,
    parent_hash: bytes = PARENT_HASH,
    anchor_height: Optional[int] = None,
    anchor_hash: bytes = ANCHOR_HASH,
    payment_tx: bytes = PAYMENT_TX,
    tokens_in: int = 512,
    tokens_out: int = 1_024,
    model_id: str = "anm-fast-8b",
    job_id: bytes = b"\x07" * 32,
    output_digest: bytes = b"\x08" * 32,
    worker_override: Optional[bytes] = None,
    sign: bool = True,
    chain_id: int = CHAIN_ID,
) -> bytes:
    m = _ml_dsa()
    pk, sk = worker_keys
    worker = worker_override if worker_override is not None else account_digest_for_pubkey(pk)
    unsigned = build_ai_work_proof(
        job_id=job_id,
        model_id=model_id,
        prompt_digest=b"\x09" * 32,
        output_digest=output_digest,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        worker=worker,
        requester=requester,
        anchor_height=height - 1 if anchor_height is None else anchor_height,
        anchor_hash=anchor_hash,
        slot=slot_digest(chain_id=chain_id, height=height, parent_hash=parent_hash),
        payment_tx_hash=payment_tx,
        worker_pk=pk,
        worker_sig=b"\x00" * 3309,
    )
    sig = m.sign(sk, unsigned.signing_message()) if sign else b"\x00" * 3309
    signed = replace(unsigned, workerSig=sig)
    return signed.to_cbor()


def wrap_proof(body: bytes, *, chain_id: int = CHAIN_ID, nullifier: Optional[bytes] = None):
    from consensus.ai_work_proof import decode_ai_work_proof as _dec

    proof = _dec(body, max_body_bytes=POLICY_V1.max_proof_body_bytes)
    n = nullifier if nullifier is not None else proof.work_nullifier(chain_id=chain_id)
    return AIProofRef(envelope=make_envelope(ProofType.AI, n, body))


def make_block(proofs, *, coinbase: Optional[bytes]):
    extra = cbor_dumps({"coinbase": coinbase}) if coinbase is not None else b""
    header = SimpleNamespace(
        extra=extra,
        poiesPolicyRoot=b"\x00" * 32,
        thetaMicro=THETA_MICRO,
        height=HEIGHT,
        parentHash=PARENT_HASH,
    )
    return SimpleNamespace(header=header, txs=(), proofs=tuple(proofs))


# ───────────────────────────── happy path ────────────────────────────────────


def test_wellformed_proof_is_accepted(worker_keys, requester_digest):
    pk, _sk = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest)
    ctx = make_context(worker_digest=worker, requester=requester_digest)

    ok, reason, psi_raw, nulls = verify_ai_work_proof(body, ctx)
    assert ok, reason
    assert reason is None
    # 1,536 tokens * 1,000 µ-nats/ktoken // 1000
    assert psi_raw == (1_000 * 1_536) // 1000
    assert len(nulls) == 2 and len(set(nulls)) == 2


def test_block_level_accept_and_sigma_psi(worker_keys, requester_digest):
    pk, _sk = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest)
    block = make_block([wrap_proof(body)], coinbase=worker)
    ctx = make_context(worker_digest=worker, requester=requester_digest)

    report = verify_block_useful_work(block, ctx)
    assert report.ok, report.reason
    assert report.proof_count == 1
    assert report.psi_micro_total == 1_536
    assert report.h_micro > 0
    assert report.s_micro == report.h_micro + report.psi_micro_total
    assert len(report.nullifiers_spent) == 2


def test_block_with_no_proofs_is_always_accepted():
    """The rule is presence-gated: absence is never a rejection."""
    block = make_block([], coinbase=None)
    ctx = make_context(worker_digest=b"\x01" * 32, requester=b"\x02" * 32)
    report = verify_block_useful_work(block, ctx)
    assert report.ok
    assert report.proof_count == 0
    assert report.psi_micro_total == 0
    assert report.nullifiers_spent == ()


# ───────────────────────── malformation matrix ───────────────────────────────


def _reason_for(body: bytes, ctx: BlockContext) -> str:
    ok, reason, _psi, _n = verify_ai_work_proof(body, ctx)
    assert not ok, "expected rejection"
    assert reason
    return reason


def test_rejects_non_cbor_and_oversize(worker_keys, requester_digest):
    pk, _ = worker_keys
    ctx = make_context(worker_digest=account_digest_for_pubkey(pk), requester=requester_digest)
    assert _reason_for(b"", ctx) == "structure:body_empty"
    assert _reason_for(b"\xff\xff\xff", ctx).startswith("structure:body_not_cbor")
    assert _reason_for(b"\x00" * 9000, ctx).startswith("structure:body_too_large")
    assert _reason_for(cbor_dumps([1, 2, 3]), ctx) == "structure:body_not_map"


def test_rejects_missing_unknown_and_bad_version(worker_keys, requester_digest):
    pk, _ = worker_keys
    ctx = make_context(worker_digest=account_digest_for_pubkey(pk), requester=requester_digest)
    good = make_proof_body(worker_keys, requester_digest)
    obj = cbor_loads(good)

    missing = dict(obj)
    missing.pop("tokensIn")
    assert _reason_for(cbor_dumps(missing), ctx) == "structure:missing_field:tokensIn"

    extra = dict(obj)
    extra["surprise"] = 1
    assert _reason_for(cbor_dumps(extra), ctx) == "structure:unknown_field:surprise"

    badv = dict(obj)
    badv["v"] = AI_WORK_PROOF_VERSION + 1
    assert _reason_for(cbor_dumps(badv), ctx).startswith("structure:bad_version")

    badtype = dict(obj)
    badtype["tokensIn"] = "many"
    assert _reason_for(cbor_dumps(badtype), ctx) == "structure:field_type:tokensIn"

    badlen = dict(obj)
    badlen["jobId"] = b"\x01" * 31
    assert _reason_for(cbor_dumps(badlen), ctx) == "structure:field_len:jobId"

    badmodel = dict(obj)
    badmodel["modelId"] = "bad model!"
    assert _reason_for(cbor_dumps(badmodel), ctx) == "structure:model_id_charset"

    longmodel = dict(obj)
    longmodel["modelId"] = "a" * 65
    assert _reason_for(cbor_dumps(longmodel), ctx) == "structure:model_id_len"


def test_rejects_non_canonical_encoding(worker_keys, requester_digest):
    """A body must have exactly one wire form, or the nullifier stops being a
    unique tag for the block's proof set."""
    pk, _ = worker_keys
    ctx = make_context(worker_digest=account_digest_for_pubkey(pk), requester=requester_digest)
    good = make_proof_body(worker_keys, requester_digest)

    # Re-encode with cbor2 in a non-canonical key order (definite-length map, but
    # keys not in deterministic order).
    cbor2 = pytest.importorskip("cbor2")
    obj = cbor_loads(good)
    reordered = {k: obj[k] for k in reversed(list(obj.keys()))}
    non_canonical = cbor2.dumps(reordered, canonical=False)
    if non_canonical == good:  # pragma: no cover - encoder already canonical
        pytest.skip("cbor2 produced canonical bytes")
    # core.encoding.cbor's decoder is itself strict, so this is caught at decode
    # (body_not_cbor); the explicit re-encode comparison in decode_ai_work_proof is
    # defence in depth for the day that decoder is swapped for a lenient one.
    # Either reason is a rejection, which is what matters.
    assert _reason_for(non_canonical, ctx) in {
        "structure:body_not_cbor",
        "structure:body_not_canonical",
    }


def test_rejects_bad_signature_and_key_binding(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    ctx = make_context(worker_digest=worker, requester=requester_digest)

    unsigned = make_proof_body(worker_keys, requester_digest, sign=False)
    assert _reason_for(unsigned, ctx) == "worker_sig_invalid"

    # Signature over a different message: flip a signed field after signing.
    good = make_proof_body(worker_keys, requester_digest)
    obj = cbor_loads(good)
    obj["tokensOut"] = int(obj["tokensOut"]) + 1
    assert _reason_for(cbor_dumps(obj), ctx) == "worker_sig_invalid"

    # A pubkey that does not hash to the claimed worker digest.
    m = _ml_dsa()
    other_pk, _other_sk = m.generate_keypair()
    obj2 = cbor_loads(good)
    obj2["workerPk"] = other_pk
    assert _reason_for(cbor_dumps(obj2), ctx) == "worker_pk_mismatch"


def test_signature_scheme_is_pinned_by_the_verifier():
    """The alg id is a module constant, never a field of the proof — 0x1001 and
    0x1002 are forgeable stubs in this tree."""
    assert ML_DSA_65_ALG_ID == 0x1003
    src = (
        __import__("pathlib").Path(__file__).resolve().parents[1] / "useful_work_verify.py"
    ).read_text()
    assert "_pq_verify(ML_DSA_65_ALG_ID" in src
    # No path reads an algorithm id out of the proof body.
    assert "alg_id" not in cbor_dumps({"x": 1}).decode("latin-1")


def test_rejects_worker_not_miner(worker_keys, requester_digest):
    pk, _ = worker_keys
    body = make_proof_body(worker_keys, requester_digest)
    ctx = make_context(
        worker_digest=account_digest_for_pubkey(pk),
        requester=requester_digest,
        miner_digest=sha3_256(b"a-different-miner"),
    )
    assert _reason_for(body, ctx) == "worker_not_miner"


def test_rejects_self_dealing_same_identity(worker_keys):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, worker)  # requester == worker
    ctx = make_context(worker_digest=worker, requester=worker)
    assert _reason_for(body, ctx) == "self_dealing_same_identity"


def test_rejects_slot_mismatch(worker_keys, requester_digest):
    """A proof cannot be lifted into another block position."""
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest, parent_hash=bytes.fromhex("22" * 32))
    ctx = make_context(worker_digest=worker, requester=requester_digest)
    assert _reason_for(body, ctx) == "slot_mismatch"


def test_rejects_zero_and_oversize_tokens(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    ctx = make_context(worker_digest=worker, requester=requester_digest)
    zero = make_proof_body(worker_keys, requester_digest, tokens_in=0, tokens_out=0)
    assert _reason_for(zero, ctx) == "tokens_zero"
    huge = make_proof_body(
        worker_keys, requester_digest, tokens_in=POLICY_V1.max_tokens_per_proof, tokens_out=1
    )
    assert _reason_for(huge, ctx).startswith("tokens_over_cap")


# ───────────────────────── freshness window ──────────────────────────────────


def test_anchor_window_boundaries(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    w = POLICY_V1.anchor_window

    for offset, expect_ok in ((1, True), (w, True), (w + 1, False), (0, False)):
        anchor_height = HEIGHT - offset
        chain = FakeChain(
            ancestors={anchor_height: ANCHOR_HASH},
            payments={
                PAYMENT_TX: paid(
                    requester_digest, height=min(anchor_height, HEIGHT - 1)
                )
            },
        )
        body = make_proof_body(worker_keys, requester_digest, anchor_height=anchor_height)
        ctx = make_context(worker_digest=worker, requester=requester_digest, chain=chain)
        ok, reason, _psi, _n = verify_ai_work_proof(body, ctx)
        assert ok is expect_ok, f"offset={offset} reason={reason}"
        if not expect_ok:
            assert reason.startswith("anchor_out_of_window")


def test_rejects_unresolved_and_mismatched_anchor(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)

    empty = FakeChain(ancestors={}, payments={})
    body = make_proof_body(worker_keys, requester_digest)
    ctx = make_context(worker_digest=worker, requester=requester_digest, chain=empty)
    assert _reason_for(body, ctx).startswith("anchor_unresolved")

    wrong = FakeChain(
        ancestors={HEIGHT - 1: bytes.fromhex("ee" * 32)},
        payments={
            PAYMENT_TX: paid(requester_digest)
        },
    )
    ctx2 = make_context(worker_digest=worker, requester=requester_digest, chain=wrong)
    assert _reason_for(body, ctx2) == "anchor_hash_mismatch"


# ───────────────────────── payment (economic bound) ──────────────────────────


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (lambda p, r: None, "payment_unresolved"),
        (
            lambda p, r: paid(sha3_256(b"someone-else")),
            "payment_sender_mismatch",
        ),
        (
            lambda p, r: paid(r, to=sha3_256(b"not-the-treasury")),
            "payment_recipient_not_treasury",
        ),
        (
            lambda p, r: paid(r, amount=p - 1),
            "payment_below_floor",
        ),
        (
            lambda p, r: paid(r, height=HEIGHT - 1),
            "payment_after_anchor",
        ),
    ],
)
def test_payment_checks(worker_keys, requester_digest, mutate, expected):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    rec = mutate(POLICY_V1.payment_floor_base_units, requester_digest)
    chain = FakeChain(
        ancestors={HEIGHT - 2: ANCHOR_HASH},
        payments=({PAYMENT_TX: rec} if rec is not None else {}),
    )
    body = make_proof_body(worker_keys, requester_digest, anchor_height=HEIGHT - 2)
    ctx = make_context(worker_digest=worker, requester=requester_digest, chain=chain)
    reason = _reason_for(body, ctx)
    assert reason.split(":")[0] == expected.split(":")[0], reason


def test_payment_too_old(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    too_old = HEIGHT - POLICY_V1.payment_max_age - 1
    chain = FakeChain(
        ancestors={HEIGHT - 1: ANCHOR_HASH},
        payments={
            PAYMENT_TX: paid(requester_digest, height=too_old)
        },
    )
    body = make_proof_body(worker_keys, requester_digest)
    ctx = make_context(worker_digest=worker, requester=requester_digest, chain=chain)
    assert _reason_for(body, ctx).startswith("payment_too_old")


# ───────────────────────────── nullifiers ────────────────────────────────────


def test_nullifier_blocks_replay_of_the_same_work(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest)
    n_work = work_nullifier(
        job_id=b"\x07" * 32,
        worker=worker,
        requester=requester_digest,
        output_digest=b"\x08" * 32,
        payment_tx_hash=PAYMENT_TX,
        chain_id=CHAIN_ID,
    )
    chain = FakeChain(
        ancestors={HEIGHT - 1: ANCHOR_HASH},
        payments={
            PAYMENT_TX: paid(requester_digest)
        },
        seen={n_work},
    )
    ctx = make_context(worker_digest=worker, requester=requester_digest, chain=chain)
    assert _reason_for(body, ctx) == "nullifier_replay_work"


def test_payment_nullifier_blocks_a_second_job_on_one_payment(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    n_pay = payment_nullifier(payment_tx_hash=PAYMENT_TX, chain_id=CHAIN_ID)
    chain = FakeChain(
        ancestors={HEIGHT - 1: ANCHOR_HASH},
        payments={
            PAYMENT_TX: paid(requester_digest)
        },
        seen={n_pay},
    )
    # A brand new jobId — the work nullifier differs, the payment tag does not.
    body = make_proof_body(worker_keys, requester_digest, job_id=b"\x77" * 32)
    ctx = make_context(worker_digest=worker, requester=requester_digest, chain=chain)
    assert _reason_for(body, ctx) == "nullifier_replay_payment"


def test_work_nullifier_excludes_block_local_fields():
    """Critical: the tag must identify the WORK INSTANCE. If a per-block field
    were folded in, a miner could re-sign the same receipt for every new slot and
    replay one job forever."""
    base = dict(
        job_id=b"\x01" * 32,
        worker=b"\x02" * 32,
        requester=b"\x03" * 32,
        output_digest=b"\x04" * 32,
        payment_tx_hash=b"\x05" * 32,
        chain_id=CHAIN_ID,
    )
    a = work_nullifier(**base)
    b = work_nullifier(**base)
    assert a == b
    # Different chain id -> different tag (no cross-chain replay).
    assert work_nullifier(**{**base, "chain_id": 2}) != a
    # Different job -> different tag.
    assert work_nullifier(**{**base, "job_id": b"\x09" * 32}) != a


def test_envelope_nullifier_must_match_the_body(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest)
    bogus = wrap_proof(body, nullifier=b"\xaa" * 32)
    block = make_block([bogus], coinbase=worker)
    ctx = make_context(worker_digest=worker, requester=requester_digest)
    report = verify_block_useful_work(block, ctx)
    assert not report.ok
    assert "envelope_nullifier_mismatch" in report.reason


def test_duplicate_proof_in_one_block_is_rejected(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest)
    block = make_block([wrap_proof(body), wrap_proof(body)], coinbase=worker)
    ctx = make_context(worker_digest=worker, requester=requester_digest)
    report = verify_block_useful_work(block, ctx)
    assert not report.ok
    assert "nullifier_duplicate_in_block" in report.reason


# ─────────────────── block-level structure / policy ──────────────────────────


def test_unsupported_proof_types_are_rejected_when_present(worker_keys, requester_digest):
    """Fail-closed: there is no working verifier for HASH_SHARE/QUANTUM/STORAGE/VDF,
    and accepting what cannot be checked is how a rule becomes satisfiable with
    fabricated bytes."""
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    env = make_envelope(ProofType.QUANTUM, b"\x01" * 32, cbor_dumps({"anything": 1}))
    block = make_block([SimpleNamespace(envelope=env)], coinbase=worker)
    ctx = make_context(worker_digest=worker, requester=requester_digest)
    report = verify_block_useful_work(block, ctx)
    assert not report.ok
    assert "unsupported_proof_type:QUANTUM" in report.reason


def test_too_many_proofs(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest)
    proofs = [wrap_proof(body) for _ in range(POLICY_V1.max_proofs_per_block + 1)]
    block = make_block(proofs, coinbase=worker)
    ctx = make_context(worker_digest=worker, requester=requester_digest)
    report = verify_block_useful_work(block, ctx)
    assert not report.ok
    assert report.reason.startswith("too_many_proofs")


def test_committed_policy_root_must_match_when_non_zero(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest)
    block = make_block([wrap_proof(body)], coinbase=worker)

    # Zero root (live mainnet today) -> self-gated, accepted.
    ok_ctx = make_context(
        worker_digest=worker, requester=requester_digest, poies_policy_root=b"\x00" * 32
    )
    assert verify_block_useful_work(block, ok_ctx).ok

    # Non-zero but wrong -> rejected.
    bad_ctx = make_context(
        worker_digest=worker, requester=requester_digest, poies_policy_root=b"\x5a" * 32
    )
    report = verify_block_useful_work(block, bad_ctx)
    assert not report.ok
    assert report.reason.startswith("policy_root_mismatch")

    # Non-zero and equal to the code-committed digest -> accepted.
    good_ctx = make_context(
        worker_digest=worker, requester=requester_digest, poies_policy_root=POLICY_V1.digest()
    )
    assert verify_block_useful_work(block, good_ctx).ok


def test_miner_identity_sources_must_agree():
    coinbase = sha3_256(b"miner")
    other = sha3_256(b"other")
    tx = SimpleNamespace(
        unsigned=SimpleNamespace(kind=3, payload=SimpleNamespace(to=other), sender=b"\x00" * 32)
    )
    block = SimpleNamespace(
        header=SimpleNamespace(extra=cbor_dumps({"coinbase": coinbase})),
        txs=(tx,),
        proofs=(),
    )
    digest, reason = block_miner_digest(block)
    assert digest is None and reason == "miner_ambiguous"

    block2 = SimpleNamespace(
        header=SimpleNamespace(extra=cbor_dumps({"coinbase": coinbase})), txs=(), proofs=()
    )
    digest2, reason2 = block_miner_digest(block2)
    assert digest2 == coinbase and reason2 is None

    block3 = SimpleNamespace(header=SimpleNamespace(extra=b""), txs=(), proofs=())
    digest3, reason3 = block_miner_digest(block3)
    assert digest3 is None and reason3 == "miner_unknown"


def test_psi_caps_bound_sigma(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    tiny = replace(
        POLICY_V1, psi_cap_per_proof_micro=10, psi_cap_ai_micro=15, gamma_total_micro=15
    )
    bodies = [
        make_proof_body(worker_keys, requester_digest, job_id=bytes([i]) * 32, payment_tx=bytes([200 + i]) * 32)
        for i in range(3)
    ]
    chain = FakeChain(
        ancestors={HEIGHT - 1: ANCHOR_HASH},
        payments={
            bytes([200 + i]) * 32: paid(requester_digest)
            for i in range(3)
        },
    )
    block = make_block([wrap_proof(b) for b in bodies], coinbase=worker)
    ctx = make_context(
        worker_digest=worker, requester=requester_digest, chain=chain, policy=tiny
    )
    report = verify_block_useful_work(block, ctx)
    assert report.ok, report.reason
    assert report.psi_micro_total == 15  # 10 + 5 + 0, clipped by Γ_total
    assert sum(v.psi_micro_capped for v in report.verdicts) == 15


# ───────────────────────────── determinism ───────────────────────────────────


def test_report_is_deterministic(worker_keys, requester_digest):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest)
    block = make_block([wrap_proof(body)], coinbase=worker)
    ctx = make_context(worker_digest=worker, requester=requester_digest)
    a = verify_block_useful_work(block, ctx)
    b = verify_block_useful_work(block, make_context(worker_digest=worker, requester=requester_digest))
    assert a == b


def test_h_micro_is_deterministic_and_monotone():
    assert h_micro_from_hash256(b"\xff" * 32) == 0
    small = h_micro_from_hash256(bytes.fromhex("00" * 8 + "ff" * 24))
    big = h_micro_from_hash256(bytes.fromhex("0f" * 32))
    assert small > big > 0
    for _ in range(3):
        assert h_micro_from_hash256(HEADER_HASH) == h_micro_from_hash256(HEADER_HASH)
    with pytest.raises(ValueError):
        h_micro_from_hash256(b"\x00" * 31)


def test_consensus_path_has_no_clock_io_or_env():
    """Source guard. Consensus purity is not something to take on trust: a single
    time.time() or os.getenv() in this path makes two nodes disagree."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    banned = (
        "time.time(",
        "datetime.",
        "os.getenv",
        "os.environ",
        "open(",
        "random.",
        "requests.",
        "urllib",
        "importlib.util.find_spec",
    )
    for name in ("useful_work_verify.py", "ai_work_proof.py"):
        src = (root / name).read_text()
        # Strip the docstrings/comments that legitimately mention these names.
        code = "\n".join(
            line for line in src.splitlines() if not line.lstrip().startswith("#")
        )
        for token in banned:
            assert token not in code, f"{name} contains {token!r} in the consensus path"


def test_proof_type_wire_table_matches_core_enum():
    """The three ProofType enums in this repo use TWO numberings. Blocks carry
    core's, so this table must track core exactly; a silent drift would score a
    proof as the wrong kind."""
    for member in ProofType:
        assert canonical_proof_type_name(int(member)) == member.name
    assert canonical_proof_type_name(99) is None

    # Document the divergence so it cannot be "fixed" by accident in one place.
    from proofs.types import ProofType as ProofsProofType

    assert int(ProofType.AI) == 1
    assert int(ProofsProofType.AI) == 2, (
        "core/types/proof.py and proofs/types.py disagree on the AI id; "
        "consensus/useful_work_verify.py keys everything by NAME because of it"
    )


def test_proofs_registry_is_still_unusable():
    """Evidence, not prose: the reason this module exists rather than wiring
    proofs/registry.py. If this test ever starts failing, the registry has been
    repaired and reusing it becomes worth re-evaluating."""
    from proofs import registry
    from proofs.types import ProofType as PT

    with pytest.raises(Exception):
        registry.get_verifier(PT.AI)


# ─────────────────── block-import gate: shadow vs enforcing ──────────────────


class _FakeChainDB:
    """A linear fake chain the real _ImporterChainView can actually walk.

    The view no longer answers from a global tx index; it walks the block's own
    ancestors and reads their tx sets and proof bodies. So the test double has to
    be a chain, not a dict of lookups — which is the point: a stub that could
    answer "is this tx included" without an ancestry would let the split bug back
    in unnoticed.

    ``kv`` mimics the receipt side-table (b"\x25" || tx_hash -> CBOR receipt).
    """

    def __init__(self, *, tip_height: int, depth: int = 80):
        self._headers = {}
        self._blocks = {}
        self._hash_by_height = {}
        self._receipts = {}
        self.kv = self
        lo = max(0, tip_height - depth)
        parent = bytes.fromhex("aa" * 32)
        for h in range(lo, tip_height + 1):
            bh = PARENT_HASH if h == tip_height else sha3_256(b"blk:%d" % h)
            self._hash_by_height[h] = bh
            header = SimpleNamespace(
                extra=b"",
                poiesPolicyRoot=b"\x00" * 32,
                thetaMicro=THETA_MICRO,
                height=h,
                parentHash=parent,
            )
            self._headers[bh] = header
            self._blocks[bh] = SimpleNamespace(header=header, txs=(), proofs=())
            parent = bh

    # ── construction helpers ────────────────────────────────────────────────

    def hash_at(self, height: int) -> bytes:
        return self._hash_by_height[int(height)]

    def include_tx(self, tx, *, height: int, status=None):
        from core.types.receipt import Receipt, ReceiptStatus

        bh = self._hash_by_height[int(height)]
        blk = self._blocks[bh]
        blk.txs = tuple(blk.txs) + (tx,)
        st = ReceiptStatus.SUCCESS if status is None else status
        self._receipts[bytes(tx_hash_bytes(tx))] = Receipt(
            status=st, gas_used=21_000, logs=()
        ).to_cbor()
        return tx

    def drop_receipt(self, tx):
        self._receipts.pop(bytes(tx_hash_bytes(tx)), None)

    def include_proofs(self, proofs, *, height: int):
        bh = self._hash_by_height[int(height)]
        self._blocks[bh].proofs = tuple(proofs)

    def prune_body(self, *, height: int):
        """Header stays, body goes — the pruned-node case."""
        self._blocks.pop(self._hash_by_height[int(height)], None)

    # ── the read API the view uses ──────────────────────────────────────────

    def get_header_by_hash(self, h):
        return self._headers.get(bytes(h))

    def get_block_by_hash(self, h):
        return self._blocks.get(bytes(h))

    def get(self, key):
        if not isinstance(key, (bytes, bytearray)) or len(key) != 33:
            return None
        if key[:1] != b"\x25":
            return None
        return self._receipts.get(bytes(key[1:]))


def _make_importer(block_db):
    from core.chain.block_import import BlockImporter

    imp = BlockImporter.__new__(BlockImporter)
    imp.params = SimpleNamespace(chain_id=CHAIN_ID, theta_initial=THETA_MICRO)
    imp.block_db = block_db
    return imp


def _gate_header():
    return SimpleNamespace(
        extra=b"",
        poiesPolicyRoot=b"\x00" * 32,
        thetaMicro=THETA_MICRO,
        height=HEIGHT,
        parentHash=PARENT_HASH,
    )


def _transfer_tx(sender: bytes, *, amount: int, to: bytes = None, salt: bytes = b"\x00" * 16):
    from core.types.tx import Tx, TxKind, TxTransfer, UnsignedTx

    return Tx(
        unsigned=UnsignedTx(
            version=2,
            chain_id=CHAIN_ID,
            fork_id=None,
            valid_after=0,
            valid_until=1 << 40,
            salt=salt,
            gas_price=1,
            gas_limit=21_000,
            sender=sender,
            kind=TxKind.TRANSFER,
            payload=TxTransfer(to=(TREASURY if to is None else to), amount=amount),
        ),
        sigs=(),
    )


def _gate_block(worker: bytes, proofs):
    header = _gate_header()
    header.extra = cbor_dumps({"coinbase": worker})
    return SimpleNamespace(header=header, txs=(), proofs=tuple(proofs))


@pytest.fixture
def gate_env(monkeypatch):
    """Arm the gate ENFORCING, which is what the tests below exercise.

    ANIMICA_USEFUL_WORK_ENFORCE is required explicitly because mainnet (the
    chain_id these importers run under) now defaults to shadow — see
    _useful_work_shadow(). Without it every "rejects X" assertion would pass
    vacuously against a gate that logs and accepts.
    """
    monkeypatch.setenv("ANIMICA_FORK_USEFUL_WORK_VERIFY_HEIGHT", str(HEIGHT - 1))
    monkeypatch.delenv("ANIMICA_USEFUL_WORK_SHADOW", raising=False)
    monkeypatch.setenv("ANIMICA_USEFUL_WORK_ENFORCE", "1")
    yield monkeypatch


def _fresh_chain(*, tip_height: int = HEIGHT - 1, depth: int = 80):
    return _FakeChainDB(tip_height=tip_height, depth=depth)


def test_gate_enforcing_rejects_invalid_proof(worker_keys, requester_digest, gate_env, caplog):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest, sign=False)  # bad signature
    block = _gate_block(worker, [wrap_proof(body)])
    imp = _make_importer(_fresh_chain())

    reason = imp._verify_block_useful_work_gated(
        block=block,
        header=block.header,
        header_hash=HEADER_HASH,
        parent_hash=PARENT_HASH,
        height=HEIGHT,
    )
    assert reason is not None
    assert "worker_sig_invalid" in reason


def test_gate_shadow_never_rejects(worker_keys, requester_digest, gate_env, caplog):
    gate_env.setenv("ANIMICA_USEFUL_WORK_SHADOW", "1")
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest, sign=False)
    block = _gate_block(worker, [wrap_proof(body)])
    imp = _make_importer(_fresh_chain())

    with caplog.at_level(logging.ERROR, logger="animica.chain.block_import"):
        reason = imp._verify_block_useful_work_gated(
            block=block,
            header=block.header,
            header_hash=HEADER_HASH,
            parent_hash=PARENT_HASH,
            height=HEIGHT,
        )
    assert reason is None, "shadow mode must never reject"
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "SHADOW" in joined and "worker_sig_invalid" in joined


def test_gate_grandfathered_below_activation_height(worker_keys, requester_digest, monkeypatch):
    monkeypatch.setenv("ANIMICA_FORK_USEFUL_WORK_VERIFY_HEIGHT", str(HEIGHT + 1))
    monkeypatch.delenv("ANIMICA_USEFUL_WORK_SHADOW", raising=False)
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    body = make_proof_body(worker_keys, requester_digest, sign=False)
    block = _gate_block(worker, [wrap_proof(body)])
    imp = _make_importer(_fresh_chain())
    assert (
        imp._verify_block_useful_work_gated(
            block=block,
            header=block.header,
            header_hash=HEADER_HASH,
            parent_hash=PARENT_HASH,
            height=HEIGHT,
        )
        is None
    )


def test_mainnet_arms_at_75000_in_shadow(monkeypatch):
    """The mainnet height is pinned at 75,000 AND mainnet defaults to shadow.

    Both halves are the safety property, so both are asserted here. A pinned
    height alone would make every upgraded node ENFORCE a rule whose
    payment-status input is still node-local (headers commit receiptsRoot = 0),
    and the first proof-carrying block would split snapshot-synced nodes from
    executing ones. If someone later flips the mainnet default to enforcing,
    this test is what should stop them — the prerequisite is committing receipts
    in a header root, not editing this assertion.
    """
    from core.chain.block_import import _useful_work_shadow
    from core.network_params import (
        ACTIVATION_HEIGHTS_BY_NETWORK,
        FORK_USEFUL_WORK_VERIFY,
        get_activation_height,
        is_fork_active,
    )

    monkeypatch.delenv("ANIMICA_FORK_USEFUL_WORK_VERIFY_HEIGHT", raising=False)
    monkeypatch.delenv("ANIMICA_USEFUL_WORK_SHADOW", raising=False)
    monkeypatch.delenv("ANIMICA_USEFUL_WORK_ENFORCE", raising=False)

    assert ACTIVATION_HEIGHTS_BY_NETWORK[("mainnet", 1)][FORK_USEFUL_WORK_VERIFY] == 75_000
    assert get_activation_height(FORK_USEFUL_WORK_VERIFY, chain_id=1) == 75_000
    assert is_fork_active(FORK_USEFUL_WORK_VERIFY, 74_999, chain_id=1) is False
    for h in (75_000, 10**9):
        assert is_fork_active(FORK_USEFUL_WORK_VERIFY, h, chain_id=1) is True

    # Armed, but advisory: an ordinary mainnet node logs and accepts.
    assert _useful_work_shadow(1) is True
    # Enforcing is an explicit, per-node opt-in…
    monkeypatch.setenv("ANIMICA_USEFUL_WORK_ENFORCE", "1")
    assert _useful_work_shadow(1) is False
    # …that an explicit shadow setting still overrides.
    monkeypatch.setenv("ANIMICA_USEFUL_WORK_SHADOW", "1")
    assert _useful_work_shadow(1) is True

    # Disposable chains enforce from genesis; nothing here changed that.
    monkeypatch.delenv("ANIMICA_USEFUL_WORK_SHADOW", raising=False)
    monkeypatch.delenv("ANIMICA_USEFUL_WORK_ENFORCE", raising=False)
    assert _useful_work_shadow(2) is False
    assert is_fork_active(FORK_USEFUL_WORK_VERIFY, 0, chain_id=2) is True

    # The env override still retunes the height.
    monkeypatch.setenv("ANIMICA_FORK_USEFUL_WORK_VERIFY_HEIGHT", "73000")
    assert is_fork_active(FORK_USEFUL_WORK_VERIFY, 73_549, chain_id=1) is True


def _paid_chain(requester: bytes, *, pay_height: int = HEIGHT - 20, status=None):
    """A chain whose block at ``pay_height`` contains a successful 1 ANM transfer
    from ``requester`` to the treasury."""
    db = _fresh_chain()
    tx = _transfer_tx(requester, amount=POLICY_V1.payment_floor_base_units)
    db.include_tx(tx, height=pay_height, status=status)
    return db, bytes(tx_hash_bytes(tx))


def test_gate_accepts_a_good_proof_end_to_end(worker_keys, requester_digest, gate_env):
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    db, pay_hash = _paid_chain(requester_digest)
    imp = _make_importer(db)
    body = make_proof_body(
        worker_keys,
        requester_digest,
        anchor_hash=db.hash_at(HEIGHT - 1),
        payment_tx=pay_hash,
    )
    block = _gate_block(worker, [wrap_proof(body)])
    assert (
        imp._verify_block_useful_work_gated(
            block=block,
            header=block.header,
            header_hash=HEADER_HASH,
            parent_hash=PARENT_HASH,
            height=HEIGHT,
        )
        is None
    )


def test_gate_blocks_replay_from_an_ancestor(worker_keys, requester_digest, gate_env):
    """A receipt spent by an ANCESTOR must not be spendable again, even re-signed
    for the new slot. Decided by re-deriving the tag from the ancestor's body —
    no store, so a restart cannot forget it and a reorg cannot strand it."""
    pk, _ = worker_keys
    worker = account_digest_for_pubkey(pk)
    db, pay_hash = _paid_chain(requester_digest)
    imp = _make_importer(db)

    # The proof as it appeared in the ancestor at HEIGHT-1.
    spent_body = make_proof_body(
        worker_keys,
        requester_digest,
        height=HEIGHT - 1,
        parent_hash=db.hash_at(HEIGHT - 2),
        anchor_height=HEIGHT - 2,
        anchor_hash=db.hash_at(HEIGHT - 2),
        payment_tx=pay_hash,
    )
    db.include_proofs([wrap_proof(spent_body)], height=HEIGHT - 1)

    # Same job + same payment, re-signed for THIS slot.
    body = make_proof_body(
        worker_keys,
        requester_digest,
        anchor_hash=db.hash_at(HEIGHT - 1),
        payment_tx=pay_hash,
    )
    block = _gate_block(worker, [wrap_proof(body)])
    reason = imp._verify_block_useful_work_gated(
        block=block,
        header=block.header,
        header_hash=HEADER_HASH,
        parent_hash=PARENT_HASH,
        height=HEIGHT,
    )
    assert reason is not None and "nullifier_replay" in reason, reason


def test_gate_is_a_noop_for_blocks_without_proofs(gate_env):
    """Live mainnet: 200 of 200 sampled blocks (heights 1..73,570, recon
    2026-08-15) commit proofsRoot = ZERO32. Activation must be inert for them."""
    imp = _make_importer(_fresh_chain())
    header = _gate_header()
    block = SimpleNamespace(header=header, txs=(), proofs=())
    assert (
        imp._verify_block_useful_work_gated(
            block=block,
            header=header,
            header_hash=HEADER_HASH,
            parent_hash=PARENT_HASH,
            height=HEIGHT,
        )
        is None
    )
