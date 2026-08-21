"""
Useful-work proof verification at block import (PoIES, C04 slice)
=================================================================

This is the verifier that ``core/chain/block_import.py`` calls behind
``FORK_USEFUL_WORK_VERIFY``. It answers one question about a block:

    "Is every useful-work proof this block carries valid, and what is Σψ?"

This module is PURE. Every input is (a) a field of the block, (b) data reached
through the ``ChainView`` the caller supplies, or (c) a constant in this file.
No clock, no environment read, no filesystem access, no network, no ambient
randomness. ``verify_block_useful_work`` called twice with equal inputs returns
an equal report, on any node.

**Purity of the module is not purity of the rule.** The verdict is only as
deterministic as the ``ChainView`` implementation. A ``ChainView`` that answers
from a global index, a node-local cache, or in-memory state makes acceptance a
per-node decision, i.e. a chain split. The protocol below therefore specifies
*ancestry-derived* answers, and the shipped implementation
(``core.chain.block_import._ImporterChainView``) derives all three from the
block's own ancestor chain — with ONE residual exception, payment execution
status, called out under RESIDUAL WEAKNESSES. That exception is a hard blocker
for enforcement, not a footnote.

------------------------------------------------------------------------------
WHAT THIS RULE DOES AND DOES NOT DO — read before planning enforcement
------------------------------------------------------------------------------

**It never requires a proof.** A block with an empty proof list is valid at
every height. This gate is *presence-gated*: it constrains only blocks that DO
carry proofs. On live mainnet that means it is currently a no-op — 95,004 of
95,004 stored blocks carry ``proofs: []`` and every header commits
``proofsRoot = ZERO32`` (recon 2026-08-15). "A block MUST carry a verified AI
proof" is a different, later fork, and it is a 100% hard cutover for the entire
fleet. Do not co-locate the two at one height.

**It never grants ψ credit.** Live acceptance is ``header_hash <= target(Θ)``
(``core/chain/block_import._pow_sanity``), which is ``S = H(u) ≥ Θ`` with
Σψ == 0. This module recomputes Σψ and the ``S = H(u) + Σψ ≥ Θ`` relationship
and reports them, but the caller uses that only for telemetry. Feeding Σψ back
into acceptance would RELAX the work requirement, requires a matching miner-side
change, and would split the network; it is deliberately out of scope. As wired,
this gate can only ever reject a block PoW already accepted — it can never admit
one PoW rejected.

**It does not force real inference, and it never will in this shape.** Read the
FORGERY VERDICT below before presenting this rule to anyone as "serve or don't
mine". It is not that.

**It cannot make self-dealing impossible.** See RESIDUAL WEAKNESSES below.

------------------------------------------------------------------------------
FORGERY VERDICT — the honest answer to "does the receipt binding force work?"
------------------------------------------------------------------------------

**No. A miner running a null worker satisfies every check, and check 2 requires
it to.**

The signature is ``DOMAIN_AI_WORK_SIG || cbor(body minus workerPk/workerSig)``,
made by the holder of the key whose ``sha3_256(pk)`` equals ``worker``. Check 2
requires ``worker == the block's coinbase digest``. So the signer is, by
construction, the miner. The signature proves custody of the payout key and
says nothing whatsoever about inference. ``promptDigest``, ``outputDigest``,
``jobId`` and ``modelId`` are miner-chosen bytes; ``tokensIn``/``tokensOut`` are
self-reported. There is no worker registry, no requester counter-signature, no
trap set, no redundancy, no re-execution. A proof over
``outputDigest = sha3_256(b"garbage")`` verifies in ~21 ms. This is pinned by
``consensus/tests/test_useful_work_adversarial.py::test_forged_proof_for_zero_
inference_is_accepted`` — a regression test that asserts the FORGERY, so nobody
later mistakes the rule for an anti-fake-work defence.

Two structural consequences follow from check 2, and both are costs:

* A miner that legitimately BUYS inference from a third-party GPU worker can
  never attach that worker's receipt — the digests will not match. The rule
  makes honest third-party sourcing invalid and self-dealing the only permitted
  shape.
* The private key controlling the mining payout address must be hot in the
  mining process, signing once per block attempt (the body contains ``slot``,
  which binds chainId+height+parentHash, so signatures cannot be pre-minted in
  bulk for unknown parents). Today miners can point ``--address`` at a cold or
  custodied address; under a required-proof rule they could not. For
  pool.animica.org the coinbase is the pool's address, so only the pool operator
  could sign and there is no path to attribute a receipt to the share submitter
  who actually ran the GPU. That is a real security regression traded for a
  signature that proves nothing about work.

Making a proof mean something needs redundancy, traps, TEE attestation, or
staking with slashing. None of those exist and work in this tree. The
recommended shape if this is ever revisited: a separately registered worker key
with an on-chain delegation to the coinbase, so the payout key stays cold, pools
can attribute per share, and third-party workers become legal.

------------------------------------------------------------------------------
The five checks on an AI proof (see consensus/ai_work_proof.py for the body)
------------------------------------------------------------------------------

1. **Structure + signature.** Canonical CBOR, exact key set, re-encode-identical
   bytes, and an ML-DSA-65 signature over
   ``DOMAIN || cbor(body minus key material)`` that verifies against
   ``workerPk``, with ``sha3_256(workerPk) == worker``. The scheme id is a
   constant in ``consensus/ai_work_proof.py`` — never read from the proof.
   (0x1001/0x1002 are forgeable stubs in this tree; a verifier that lets the
   object name its own algorithm lets the attacker pick a broken one.)
   A MISSING PQ backend is reported as ``worker_sig_backend_unavailable``,
   never as ``worker_sig_invalid``: a build defect and a bad block must not be
   indistinguishable in the logs, because one is fixed by reinstalling and the
   other by banning a peer.

2. **The miner is the worker.** ``worker`` must equal the block's coinbase
   account digest. Compared as 32-byte digests, never as bech32m strings.

3. **The requester is not the miner.** ``requester != worker``. Bypassable with
   a second identity — its only function is to force check 4 to be paid by a
   different account.

4. **The requester paid, and the payment EXECUTED.** ``paymentTxHash`` must
   reference a TRANSFER included in an ANCESTOR of this block at a height in
   ``[H - payment_max_age, anchorHeight]``, whose sender is ``requester``, whose
   recipient is the code-committed AICF treasury digest for this chain, whose
   amount is at least ``payment_floor_base_units``, **and whose execution
   receipt status is SUCCESS**.

   The execution-status half is not optional decoration; without it this check
   costs zero. An under-funded TRANSFER raises ``InsufficientBalance``
   (``execution/runtime/transfers.py``) BEFORE any debit, ``apply_block`` turns
   that into a REVERT with ``gas_used = 0``, the journal rolls it back, the
   block stays valid, and the tx index entry is still written. A miner could
   therefore satisfy "the requester paid 1 ANM" with a transfer from an empty
   account that moved nothing and paid no fee: 0 ANM principal, 0 ANM fee. That
   attack is pinned by ``test_useful_work_adversarial.py::
   test_reverted_payment_is_rejected``.

   Inclusion is resolved by scanning the block's OWN ancestors, never a global
   tx index: the index is a canonical-height view that is never deleted on
   reorg, so a payment that exists only on an orphaned fork would otherwise
   satisfy this check on the canonical chain.

5. **Fresh and single-use.** ``anchorHeight ∈ [H - anchor_window, H - 1]`` and
   ``anchorHash`` equals the header hash of THIS block's ancestor at that height
   (ancestor, not "canonical at that height" — on a fork those differ and the
   canonical index is not a property of the block being validated).

   Single-use is decided by scanning the block's own ancestry for the same
   nullifier, NOT by a node-local nullifier store. That distinction is the
   difference between a replay rule and a chain split: an in-memory store is
   written for side-chain blocks that never become canonical, is never unwound
   on reorg, is emptied by a restart, and is absent entirely on a snapshot-
   synced node — so two honest nodes would disagree about ACCEPTANCE, not just
   about replay. Pinned by ``test_useful_work_adversarial.py::
   test_orphaned_block_does_not_poison_the_canonical_chain``.

   The scan window is exactly ``payment_max_age`` blocks, and that is
   *provably complete* rather than a guess. If nullifier N is used at H1 and
   again at H2 > H1 on one chain, both uses name the same ``paymentTxHash``,
   hence the same inclusion height P. Check 4 gives ``P <= anchorHeight_1 <=
   H1 - 1`` and ``P >= H2 - payment_max_age``; combining,
   ``H1 >= H2 - payment_max_age + 1``. So H1 always lies inside the window
   scanned at H2. (This is why ``payment_max_age`` is pinned equal to
   ``anchor_window`` — it bounds the scan as well as the payment.)

Plus, block-wide: a ``slot`` binding to (chainId, height, parentHash) so a proof
cannot be lifted into another block; a cap on proofs per block; and — when the
header commits a non-zero ``poiesPolicyRoot`` — the committed policy root must
equal this module's ``POLICY_V1_DIGEST``.

A note on ``anchorHeight``/``anchorHash``, so the next reader does not have to
re-derive it: **they add no freshness that ``slot`` does not already provide.**
``slot = sha3(domain || chainId || height || parentHash)`` pins the exact fork
position at H-1, which is strictly stronger than an anchor at H-64. The anchor's
only remaining function is bounding the payment window from above
(``payment.height <= anchorHeight``), which ``H - 1`` would do directly. They are
kept because changing the body shape would churn the codec and every test for no
security gain; a v2 body should drop them and bound the payment by ``H - 1``.

------------------------------------------------------------------------------
RESIDUAL WEAKNESSES — stated plainly, because the honest claim is narrow
------------------------------------------------------------------------------

* **Self-dealing is priced, not prevented — and even the price is soft.** A
  miner can create a second account, fund it, and pay itself for jobs it
  "served". Checks 3 and 4 do not stop that. With the execution-status check in
  place the narrowest defensible claim is: *on a node that can resolve the
  payment receipt, mining with an AI proof requires a SUCCESSFULLY EXECUTED
  transfer of at least the payment floor from a second identity to the AICF
  treasury.* Note what that sentence does not say:

  - it is a ~0.7% tax, not a barrier — 1 ANM against a ~150 ANM/block subsidy;
  - raising the floor above the subsidy would kill self-dealing and
    simultaneously make honest inference absurd (nobody pays 150 ANM for one
    completion). That tension is real and unresolved here; the floor is simply
    an explicit, code-committed number governance can move;
  - **the payment is substantially refundable to the attacker.** The treasury
    is swept into the AICF state pool (``execution/state/aicf_treasury.py``)
    and ``distribute_epoch_to_miners`` (``execution/state/aicf_state.py``) pays
    the epoch budget pro-rata to AICF credit holders — a path live-wired in
    ``core/chain/block_import.py``. A miner that is also the network's only
    credit-earning provider gets its own payment back. On a network with one
    dominant miner, that is the expected case, not the corner case.

  Do not repeat the earlier form of this claim ("costs at least the payment
  floor per block") without those three qualifiers.

* **The work itself is not verified.** ``outputDigest`` binds *an* output, not a
  *correct* one. Nothing here proves the model ran, or ran honestly. The
  TEE/traps/redundancy machinery that would speak to that does not work in this
  tree (see the module docstring of ``consensus/ai_work_proof.py``), and TEE
  attestation on community GPUs is not realistic near-term.

* **Token counts are self-reported.** ``tokensIn``/``tokensOut`` are signed by
  the worker, i.e. by the miner. They are bounded by policy so they cannot
  inflate ψ without limit, and today ψ buys nothing. If ψ is ever given
  consensus credit, self-reported tokens become a direct minting lever and must
  be replaced by something the requester also signs.

* **THE BLOCKER: payment execution status is still a per-node input.** Chain
  headers commit ``receiptsRoot = 0`` (all 73,570 mainnet headers as of
  2026-08-15), so nothing about execution outcome is committed on-chain and
  nothing in a block body reveals whether a transfer succeeded. The shipped
  ``ChainView`` therefore reads the node's local receipt side-table
  (``b"\\x25" || tx_hash``, written by ``BlockImporter._persist_receipts``).
  That table is best-effort — its write is wrapped in a swallow-all except, and
  a node that runs without a state DB never writes it at all — so a node
  missing the entry answers ``unknown``.

  The verifier FAILS CLOSED on ``unknown`` (reason
  ``payment_status_unknown``), which is the safe direction for a rule that is
  disabled by default, but it means an enforcing fleet could split between
  nodes that executed a range and nodes that snapshot-synced past it. **This is
  a hard prerequisite for enforcement, not a caveat**: either receipts must be
  committed in a header root (so status is chain-derived), or the payment check
  must be replaced with something structurally verifiable from block bodies
  alone. Shadow telemetry must show a zero rate for ``payment_status_unknown``
  AND ``payment_not_in_ancestry`` AND ``nullifier_scan_incomplete`` across a
  full observation window before anyone proposes enforcing.

* **The ancestry scan needs the last ``payment_max_age`` blocks.** A node that
  has pruned block bodies inside that window cannot complete the payment or
  nullifier scan and returns ``nullifier_scan_incomplete`` /
  ``payment_unresolved``. That is a much weaker requirement than the complete,
  never-pruned global tx index the previous design needed (64 recent blocks vs
  all history), but it is still a requirement, and it is still fail-closed.

* **A second identity is free to create.** Only funding it costs anything.
  Nothing on a permissionless chain changes that.

* **One residual un-gated import path exists in this tree, pre-existing.**
  ``mining/adapters/core_chain.py`` persists a block and sets the head with NO
  validation when ``core.chain.block_import`` cannot be imported. It is not
  introduced here and not reachable on a healthy build, but it is a bypass of
  every gate in ``import_block``, this one included.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from core.encoding.cbor import cbor_dumps
from core.utils.hash import sha3_256

from consensus.ai_work_proof import (
    AIWorkProofError,
    AIWorkProofV1,
    ML_DSA_65_ALG_ID,
    account_digest_for_pubkey,
    decode_ai_work_proof,
    slot_digest,
)

__all__ = [
    "UsefulWorkPolicy",
    "POLICY_V1",
    "POLICY_V1_DIGEST",
    "PaymentRecord",
    "PAYMENT_EXECUTED",
    "PAYMENT_FAILED",
    "PAYMENT_STATUS_UNKNOWN",
    "ChainView",
    "BlockContext",
    "ProofVerdict",
    "BlockUsefulWorkReport",
    "verify_block_useful_work",
    "verify_ai_work_proof",
    "ai_work_nullifiers_of_body",
    "block_ai_work_nullifiers",
    "block_miner_digest",
    "h_micro_from_hash256",
    "canonical_proof_type_name",
    "pq_backend_available",
    "SUPPORTED_PROOF_TYPE_NAMES",
]


# ═══════════════════════════════════════════════════════════════════════════
# Proof-type normalization — the three-enum landmine
# ═══════════════════════════════════════════════════════════════════════════
#
# There are three ProofType enums in this repo with TWO different numberings:
#
#   core/types/proof.py      HASH_SHARE=0 AI=1 QUANTUM=2 STORAGE=3 VDF=4
#   proofs/types.py          HASH_SHARE=1 AI=2 QUANTUM=3 STORAGE=4 VDF=5 HASH_WORK=6
#   consensus/types.py       HASH_SHARE=1 AI=2 QUANTUM=3 STORAGE=4 VDF=5 HASH_WORK=6
#
# ``consensus/types.py`` says "These IDs are consensus-critical... Do not
# renumber" — and ``core/types/proof.py`` does not match it. A ``core`` envelope
# tagged typeId=1 is AI to ``core`` and HASH_SHARE to ``proofs``/``consensus``.
#
# Blocks carry ``core.types.proof.ProofEnvelope``, so the WIRE numbering is
# ``core``'s and this module treats it as authoritative. Everything downstream
# is keyed by NAME, never by a raw int, so a future renumbering of the other two
# enums cannot silently rescore a block. ``consensus/tests`` asserts this table
# against ``core.types.proof.ProofType`` so the mapping cannot drift unnoticed.
_WIRE_TYPE_NAMES: Mapping[int, str] = {
    0: "HASH_SHARE",
    1: "AI",
    2: "QUANTUM",
    3: "STORAGE",
    4: "VDF",
}

# Only AI has a working verifier. Every other type is rejected when present:
# fail-closed, and deterministic because the supported set is a code constant
# rather than "whatever modules happen to import on this node".
# (``consensus/validator.validate_block`` derives its supported set from
# ``importlib.util.find_spec`` at validation time — a per-node input in an
# acceptance decision. That is why this module does not call it.)
SUPPORTED_PROOF_TYPE_NAMES = frozenset({"AI"})


def canonical_proof_type_name(wire_type_id: int) -> Optional[str]:
    """Map a block-wire proof type id to a stable name, or None if unknown."""
    try:
        return _WIRE_TYPE_NAMES.get(int(wire_type_id))
    except (TypeError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Policy — code-committed, never loaded from a file at validation time
# ═══════════════════════════════════════════════════════════════════════════
#
# ``consensus/policy.load_poies_policy`` reads ``spec/poies_policy.yaml`` from
# disk. A file read inside an acceptance decision means the verdict depends on
# what is on each node's filesystem — the precise class of bug that splits a
# chain. The policy below is a frozen constant compiled into the build, and its
# digest is exported so a later fork can COMMIT it in ``poiesPolicyRoot``.
#
# Live mainnet commits ``poiesPolicyRoot = ZERO32`` in all 73,550 headers, so the
# "policy pinned by root in the header" model of spec/poies_math.md §11 is not
# in effect today. This module self-gates: a zero root is accepted (nothing to
# compare against), a non-zero root must equal POLICY_V1_DIGEST.


@dataclass(frozen=True)
class UsefulWorkPolicy:
    """Frozen PoIES policy inputs for useful-work verification.

    All ψ quantities are integer µ-nats. No floats appear anywhere in the
    scoring path; ``spec/poies_math.md`` §11 requires fixed-point.
    """

    version: int = 1

    # ── structural bounds ────────────────────────────────────────────────────
    max_proofs_per_block: int = 8
    """Bounds the per-block verification cost: at most 8 ML-DSA-65 verifies
    (0.166 s measured, against a ~102 s block interval) and at most 8 tags to
    resolve against the ancestry scan. Verification is PoW-gated — ``_pow_sanity``
    runs long before this gate — so an attacker must find a real block to trigger
    any of it. This bound used to exist for a different reason (an oversized
    proof set could evict entries from a fixed-size in-memory nullifier store and
    re-open replay); that store is gone, and the bound is now purely about
    cost."""

    max_proof_body_bytes: int = 8_192
    """One AIWorkProofV1 is ~5.5 KB (1952 B pubkey + 3309 B signature + fields).
    NOTE the chain-growth consequence before requiring one per block: ~309,000
    blocks/year × 5.5 KB ≈ 1.8 GB/year."""

    max_tokens_per_proof: int = 10_000_000
    """Bounds ψ and rejects absurd claims. Token counts are self-reported."""

    # ── freshness / payment windows (blocks; height is the only time source) ─
    anchor_window: int = 64
    """``H - anchor_window <= anchorHeight <= H - 1``. ~1.8 h at 102 s/block."""

    payment_max_age: int = 64
    """The payment tx must be included no more than this many blocks below H.

    PINNED EQUAL TO ``anchor_window``, and that equality is load-bearing twice
    over, not a coincidence:

    * it is the width of the ancestry scan that decides both payment inclusion
      and nullifier replay (see check 5 in the module docstring for the proof
      that ``payment_max_age`` blocks is exactly sufficient, and
      ``test_policy_windows_are_consistent`` for the invariant);
    * it bounds the per-block work: a proof-carrying block walks at most
      ``payment_max_age`` ancestors, so verification cost is O(64 blocks)
      rather than O(1024) or O(chain).

    It was 1,024 in the first draft, when replay was decided by an in-memory
    ``NullifierStore``; that store was the split bug this window replaces.
    """

    payment_floor_base_units: int = 1_000_000_000
    """1 ANM (1e9 base units). THIS IS A TAX, NOT A BARRIER — see RESIDUAL
    WEAKNESSES in the module docstring. Self-dealing remains profitable while
    this is below the miner subsidy (~150 ANM/block after the 75,000 split)."""

    # ── ψ mapping and caps (µ-nats) ──────────────────────────────────────────
    beta_ai_micro_per_ktoken: int = 1_000
    """ψ_raw = beta * (tokensIn + tokensOut) // 1000."""

    psi_cap_per_proof_micro: int = 1_000_000
    psi_cap_ai_micro: int = 2_000_000
    gamma_total_micro: int = 2_000_000

    # ── code-committed AICF treasury account digests, by chain id ───────────
    # Consensus NEVER reads ANIMICA_AICF_TREASURY_ADDRESS: an env-overridable
    # recipient in an acceptance rule is a per-node input. These digests are the
    # sha3_256(pubkey) payloads of the addresses in
    # rpc/methods/aicf_jobs._DEFAULT_TREASURY_BY_CHAIN, decoded at authoring
    # time and pinned here.
    treasury_digest_by_chain: Mapping[int, bytes] = field(
        default_factory=lambda: {
            # mainnet: anim1zqpf6a5hvup7kggxdutt3tgswz4aa9rwlpsz8ywf73m4l5cmzhfk7pcnh6kgt
            1: bytes.fromhex(
                "9d76976703eb21066f16b8ad1070abde946ef8602391c9f4775fd31b15d36f07"
            ),
        }
    )

    def to_digest_obj(self) -> Mapping[str, Any]:
        return {
            "anchorWindow": int(self.anchor_window),
            "betaAiMicroPerKToken": int(self.beta_ai_micro_per_ktoken),
            "gammaTotalMicro": int(self.gamma_total_micro),
            "maxProofBodyBytes": int(self.max_proof_body_bytes),
            "maxProofsPerBlock": int(self.max_proofs_per_block),
            "maxTokensPerProof": int(self.max_tokens_per_proof),
            "paymentFloorBaseUnits": int(self.payment_floor_base_units),
            "paymentMaxAge": int(self.payment_max_age),
            "psiCapAiMicro": int(self.psi_cap_ai_micro),
            "psiCapPerProofMicro": int(self.psi_cap_per_proof_micro),
            "treasuryByChain": [
                [int(k), bytes(v)] for k, v in sorted(self.treasury_digest_by_chain.items())
            ],
            "v": int(self.version),
        }

    def digest(self) -> bytes:
        """sha3_256 over canonical CBOR — the value a future fork commits in
        ``header.poiesPolicyRoot``."""
        return sha3_256(b"animica/poies/policy/v1" + cbor_dumps(self.to_digest_obj()))

    def treasury_digest(self, chain_id: int) -> Optional[bytes]:
        return self.treasury_digest_by_chain.get(int(chain_id))


POLICY_V1 = UsefulWorkPolicy()
POLICY_V1_DIGEST = POLICY_V1.digest()

# The completeness proof for the replay scan (module docstring, check 5) needs
# payment_max_age >= anchor_window. Assert it at import so a future tweak to one
# constant cannot silently open a replay window.
assert POLICY_V1.payment_max_age >= POLICY_V1.anchor_window, (
    "payment_max_age must be >= anchor_window or the ancestry replay scan is "
    "no longer complete"
)


# ═══════════════════════════════════════════════════════════════════════════
# Chain view — the only way this module reaches committed state
# ═══════════════════════════════════════════════════════════════════════════


# Execution outcome of a resolved payment, as far as the node can tell.
PAYMENT_EXECUTED = "executed"
"""The payment's receipt says SUCCESS: value actually moved."""
PAYMENT_FAILED = "failed"
"""The payment's receipt says REVERT/OOG: included, but moved nothing."""
PAYMENT_STATUS_UNKNOWN = "unknown"
"""No receipt available on this node. FAIL-CLOSED, and a split risk under
enforcement — see THE BLOCKER in the module docstring."""


@dataclass(frozen=True)
class PaymentRecord:
    """A TRANSFER included in the chain, as the verifier needs it.

    ``status`` and ``in_ancestry`` default to the REJECTING values on purpose: a
    ``ChainView`` written against the old three-field shape now fails closed
    instead of silently accepting a reverted or off-chain payment.
    """

    sender: bytes  # 32-byte account digest
    to: bytes  # 32-byte account digest
    amount: int  # base units (1 ANM = 1e9)
    height: int  # inclusion height
    status: str = PAYMENT_STATUS_UNKNOWN
    in_ancestry: bool = False
    """True only if the including block is an ancestor of the block under test.
    A global tx index cannot answer this — it is a canonical-height view whose
    entries survive a reorg — so an implementation that cannot prove ancestry
    must leave this False."""


class ChainView(Protocol):
    """Chain lookups for the verifier.

    Implementations MUST answer from the block's own ancestry. "Same chain, same
    argument -> same answer on every node" is not enough; the answer has to be
    the same for every node validating THIS block, which during a reorg is a
    different question. Anything resolved from a canonical-height index, a
    node-local cache, or process memory belongs to the node, not to the chain,
    and putting it in an acceptance decision splits the network.
    """

    def ancestor_hash_at(self, height: int) -> Optional[bytes]:
        """Header hash of THIS block's ancestor at ``height``.

        Must walk the block's own parent chain, not a canonical-height index:
        during a reorg the two disagree and only the ancestor is a property of
        the block being validated. Returns None when unreachable (too deep,
        pruned, missing)."""

    def payment(self, tx_hash: bytes) -> Optional[PaymentRecord]:
        """Resolve a transfer included in an ANCESTOR of this block.

        Returns None when the tx is not found in the scanned ancestry. A record
        with ``in_ancestry=False`` means "found, but not on this block's chain"
        and is rejected with its own reason string."""

    def nullifier_used_in_ancestry(self, nullifier: bytes) -> Optional[bool]:
        """Was this nullifier already spent by an ancestor of this block?

        Returns True/False from an ancestry scan, or None when the scan could
        not be completed (pruned bodies) — which the verifier treats as a
        rejection, never as "not seen".

        Deliberately NOT ``nullifier_seen(store)``: a node-local store is
        written for side-chain blocks, never unwound on reorg, emptied by a
        restart, and empty on a snapshot-synced node. Every one of those makes
        two honest nodes disagree about acceptance."""


@dataclass(frozen=True)
class BlockContext:
    """Everything the verifier needs about the block under test."""

    chain_id: int
    height: int
    parent_hash: bytes
    header_hash: bytes
    theta_micro: int
    poies_policy_root: bytes
    miner_digest: Optional[bytes]
    chain: ChainView
    policy: UsefulWorkPolicy = POLICY_V1


# ═══════════════════════════════════════════════════════════════════════════
# Verdicts
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ProofVerdict:
    index: int
    type_id: int
    type_name: str
    ok: bool
    reason: Optional[str]
    psi_micro_raw: int = 0
    psi_micro_capped: int = 0
    nullifiers: Tuple[bytes, ...] = ()

    def describe(self) -> str:
        return (
            f"proof[{self.index}] type={self.type_name}({self.type_id}) "
            f"{'ok' if self.ok else 'REJECT:' + str(self.reason)}"
        )


@dataclass(frozen=True)
class BlockUsefulWorkReport:
    """The complete, deterministic outcome for one block."""

    height: int
    proof_count: int
    ok: bool
    reason: Optional[str]
    verdicts: Tuple[ProofVerdict, ...]
    psi_micro_total: int
    h_micro: int
    theta_micro: int
    s_micro: int
    theta_ok: bool
    nullifiers_spent: Tuple[bytes, ...]
    """Tags this block's proofs consume. INFORMATIONAL — nothing records them.
    Replay is decided by re-deriving these tags from the block's ancestors
    (``block_ai_work_nullifiers``), so there is no store to write to, nothing to
    unwind on reorg, and nothing to lose on restart. Kept in the report because
    telemetry wants it."""
    policy_digest: bytes

    def failures(self) -> Tuple[ProofVerdict, ...]:
        return tuple(v for v in self.verdicts if not v.ok)

    def log_extra(self) -> Dict[str, Any]:
        """Structured fields for the shadow log — every number an operator needs
        to decide go/no-go, and nothing that varies per node."""
        return {
            "height": self.height,
            "proofs": self.proof_count,
            "ok": self.ok,
            "reason": self.reason,
            "psi_micro_total": self.psi_micro_total,
            "h_micro": self.h_micro,
            "theta_micro": self.theta_micro,
            "s_micro": self.s_micro,
            "theta_ok": self.theta_ok,
            "failures": [v.describe() for v in self.failures()],
            "policy_digest": self.policy_digest.hex()[:16],
        }


# ═══════════════════════════════════════════════════════════════════════════
# Deterministic math
# ═══════════════════════════════════════════════════════════════════════════

_UINT256 = 1 << 256
_MICRO = 1_000_000
# Reported when the hash is so small that H(u) is astronomically large; matches
# the miner-side convention in python/animica/animica_cpu_miner_repoexact.py.
_H_MICRO_MAX = 100 * _MICRO


def h_micro_from_hash256(header_hash: bytes) -> int:
    """H(u) = -ln(u) in µ-nats, where u = (hash + 1) / (2^256 + 1).

    Decimal with a pinned context (prec=80, ROUND_HALF_EVEN), mirroring
    ``core/utils/pow.micro_threshold_to_target256`` — the inverse direction of
    the same relation, so the two agree at the acceptance boundary. Float ``ln``
    would be platform-dependent at the last bits and has no place in a consensus
    number.
    """
    if not isinstance(header_hash, (bytes, bytearray)) or len(header_hash) != 32:
        raise ValueError("header_hash must be 32 bytes")
    x = int.from_bytes(bytes(header_hash), "big")
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_EVEN
        u = (Decimal(x) + 1) / (Decimal(_UINT256) + 1)
        if u <= 0:  # pragma: no cover - unreachable given the +1
            return _H_MICRO_MAX
        h = -(u.ln())
        micro = int((h * _MICRO).to_integral_value(rounding=ROUND_HALF_EVEN))
    if micro < 0:
        return 0
    return min(micro, _H_MICRO_MAX)


def _psi_ai_micro_raw(proof: AIWorkProofV1, policy: UsefulWorkPolicy) -> int:
    """ψ_raw for an AI work proof, in µ-nats. Integer arithmetic only.

    Only token count feeds ψ. The other PoIES AI signals of
    ``spec/poies_math.md`` §3 — qos, traps_ratio, redundancy — have no
    trustworthy source in this design: there is no trap machinery that works and
    no redundancy protocol, so treating them as 1.0 and multiplying would be
    dressing up a constant as a measurement.
    """
    tokens = proof.total_tokens()
    if tokens < 0:
        return 0
    return (int(policy.beta_ai_micro_per_ktoken) * tokens) // 1000


def _apply_caps(
    contributions: Sequence[Tuple[int, str, int]], policy: UsefulWorkPolicy
) -> Tuple[List[int], int]:
    """spec/poies_math.md §15 acceptance pseudocode, in integers.

    Greedy budget in the block's proof order (a deterministic order fixed by the
    block itself), applying per-proof, then per-type, then global caps. Returns
    (per-proof capped ψ, Σψ).

    NOTE this is the §15 greedy form, not ``consensus/caps.apply_all_caps``'s
    proportional downscale. §15 is what the acceptance predicate is specified
    against, and it needs no float division.
    """
    type_budget: Dict[str, int] = {"AI": int(policy.psi_cap_ai_micro)}
    total_budget = int(policy.gamma_total_micro)
    out: List[int] = []
    taken_total = 0
    for _idx, type_name, psi_raw in contributions:
        psi = min(int(psi_raw), int(policy.psi_cap_per_proof_micro))
        psi = min(psi, type_budget.get(type_name, 0))
        psi = min(psi, total_budget)
        if psi < 0:
            psi = 0
        out.append(psi)
        if psi > 0:
            type_budget[type_name] = type_budget.get(type_name, 0) - psi
            total_budget -= psi
            taken_total += psi
    return out, taken_total


# ═══════════════════════════════════════════════════════════════════════════
# Miner identity
# ═══════════════════════════════════════════════════════════════════════════

_COINBASE_KIND = 3
_ZERO32 = b"\x00" * 32


def block_miner_digest(block: Any) -> Tuple[Optional[bytes], Optional[str]]:
    """Return (miner_account_digest, reason) for a block.

    Two independent sources exist and both are used:

    * ``header.extra`` = ``cbor({"coinbase": <32-byte digest>})``. This is what
      ``execution/runtime/env.make_block_env`` resolves and what
      ``_credit_block_rewards`` actually pays, so it is the authoritative
      "who got paid".
    * the coinbase transaction's ``payload.to``.

    When both are present they must agree; a block whose reward recipient is
    ambiguous is rejected rather than guessed at, because the whole point of
    check 2 is to name one miner.
    """
    extra_cb: Optional[bytes] = None
    extra = getattr(block, "header", None)
    extra = getattr(extra, "extra", None) if extra is not None else None
    if isinstance(extra, (bytes, bytearray)) and len(extra) > 0:
        try:
            from core.encoding.cbor import cbor_loads

            decoded = cbor_loads(bytes(extra))
            if isinstance(decoded, dict):
                cb = decoded.get("coinbase")
                if isinstance(cb, (bytes, bytearray)) and len(cb) == 32:
                    extra_cb = bytes(cb)
        except Exception:
            extra_cb = None

    tx_cb: Optional[bytes] = None
    for tx in getattr(block, "txs", ()) or ():
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is None:
            continue
        try:
            if int(getattr(unsigned, "kind", -1)) != _COINBASE_KIND:
                continue
        except (TypeError, ValueError):
            continue
        payload = getattr(unsigned, "payload", None)
        to = getattr(payload, "to", None) if payload is not None else None
        if isinstance(to, (bytes, bytearray)) and len(to) == 32:
            tx_cb = bytes(to)
            break

    if extra_cb is not None and tx_cb is not None and extra_cb != tx_cb:
        return None, "miner_ambiguous"
    miner = extra_cb if extra_cb is not None else tx_cb
    if miner is None:
        return None, "miner_unknown"
    if miner == _ZERO32:
        return None, "miner_zero"
    return miner, None


# ═══════════════════════════════════════════════════════════════════════════
# AI proof verification
# ═══════════════════════════════════════════════════════════════════════════


def pq_backend_available() -> bool:
    """True iff this build can actually verify an ML-DSA-65 signature.

    There is exactly one ML-DSA-65 implementation in-tree (the vendored
    pure-Python FIPS-204 reference; no liboqs fast path), so every node that HAS
    the backend agrees bit-for-bit. A node that lacks it agrees with nobody —
    ``pq/py/algs/ml_dsa_65.verify`` returns False for every input when the
    vendored package is missing, which is indistinguishable from "the miner
    signed badly" unless it is checked separately. That is exactly this repo's
    recurring failure mode (untracked packages that publish fine to PyPI and
    break every fresh clone), so callers should assert this at startup rather
    than discovering it one rejected block at a time.
    """
    try:
        import pq.py.algs.ml_dsa_65 as _m
    except Exception:
        return False
    try:
        return bool(_m.is_available())
    except Exception:
        return False


def _verify_ml_dsa_65(*, pubkey: bytes, signature: bytes, message: bytes) -> Tuple[bool, bool]:
    """Detached ML-DSA-65 verify with the algorithm id pinned by the caller.

    Returns ``(backend_ok, signature_ok)``. Never raises. The two failures are
    kept apart on purpose: ``backend_ok=False`` is a broken build (fix by
    reinstalling; every proof would fail on this node and nowhere else) while
    ``signature_ok=False`` is a bad block (fix by rejecting it). Collapsing them
    into one boolean turns a silent, permanent chain split into a reason string
    that reads like an ordinary invalid signature.
    """
    if not pq_backend_available():
        return False, False
    try:
        from pq.py.verify import verify as _pq_verify
    except Exception:
        return False, False
    try:
        ok = bool(
            _pq_verify(ML_DSA_65_ALG_ID, bytes(pubkey), bytes(signature), bytes(message))
        )
    except Exception:
        return True, False
    return True, ok


def verify_ai_work_proof(
    body: bytes, ctx: BlockContext
) -> Tuple[bool, Optional[str], int, Tuple[bytes, ...]]:
    """Verify one AI proof body. Returns (ok, reason, psi_raw_micro, nullifiers).

    Reason strings are stable identifiers, safe to aggregate in metrics.
    """
    policy = ctx.policy

    # ── check 1a: structure ─────────────────────────────────────────────────
    try:
        proof = decode_ai_work_proof(body, max_body_bytes=policy.max_proof_body_bytes)
    except AIWorkProofError as e:
        return False, f"structure:{e.reason}", 0, ()

    if proof.total_tokens() <= 0:
        return False, "tokens_zero", 0, ()
    if proof.total_tokens() > policy.max_tokens_per_proof:
        return False, f"tokens_over_cap:{proof.total_tokens()}", 0, ()

    # ── slot binding: this proof belongs to THIS block position ─────────────
    try:
        expected_slot = slot_digest(
            chain_id=ctx.chain_id, height=ctx.height, parent_hash=ctx.parent_hash
        )
    except AIWorkProofError as e:
        return False, f"slot:{e.reason}", 0, ()
    if proof.slot != expected_slot:
        return False, "slot_mismatch", 0, ()

    # ── checks 2 and 3 run BEFORE the signature ─────────────────────────────
    # Both are 32-byte comparisons; the ML-DSA-65 verify below is ~21 ms of
    # pure Python. Measured on this box: 8 full verifies = 0.170 s, 8 rejects
    # via these comparisons = 0.0012 s — 141x cheaper to throw away a garbage
    # proof. Neither check depends on the signature being valid (they constrain
    # fields the signature covers, so a forged field is caught here and a
    # tampered one at the signature).
    if ctx.miner_digest is None:
        return False, "miner_unknown", 0, ()
    if proof.worker != ctx.miner_digest:
        return False, "worker_not_miner", 0, ()
    if proof.requester == proof.worker:
        return False, "self_dealing_same_identity", 0, ()
    if proof.requester == _ZERO32:
        return False, "requester_zero", 0, ()

    # ── check 1b: key binds to the claimed worker, then the signature ───────
    if account_digest_for_pubkey(proof.workerPk) != proof.worker:
        return False, "worker_pk_mismatch", 0, ()
    backend_ok, sig_ok = _verify_ml_dsa_65(
        pubkey=proof.workerPk,
        signature=proof.workerSig,
        message=proof.signing_message(),
    )
    if not backend_ok:
        # A build defect, not a bad block. Distinct reason so an operator sees
        # "reinstall the PQ backend", not "some miner is sending junk".
        return False, "worker_sig_backend_unavailable", 0, ()
    if not sig_ok:
        return False, "worker_sig_invalid", 0, ()

    # ── check 5a: freshness ─────────────────────────────────────────────────
    lo = ctx.height - int(policy.anchor_window)
    hi = ctx.height - 1
    if not (lo <= proof.anchorHeight <= hi):
        return (
            False,
            f"anchor_out_of_window:{proof.anchorHeight}~[{lo},{hi}]",
            0,
            (),
        )
    anchor_hash = ctx.chain.ancestor_hash_at(int(proof.anchorHeight))
    if anchor_hash is None:
        return False, f"anchor_unresolved:{proof.anchorHeight}", 0, ()
    if bytes(anchor_hash) != proof.anchorHash:
        return False, "anchor_hash_mismatch", 0, ()

    # ── check 4: the requester actually paid, and the payment EXECUTED ──────
    payment = ctx.chain.payment(proof.paymentTxHash)
    if payment is None:
        return False, "payment_unresolved", 0, ()
    if not bool(payment.in_ancestry):
        # Found somewhere, but not on this block's chain. A global tx index is
        # never pruned on reorg, so without this a payment that exists only on
        # an orphaned fork would fund a proof on the canonical chain.
        return False, "payment_not_in_ancestry", 0, ()
    status = str(payment.status)
    if status == PAYMENT_FAILED:
        # An under-funded TRANSFER reverts BEFORE any debit and pays no fee, yet
        # stays in the block and in the tx index. Accepting it makes the whole
        # payment check cost the attacker exactly zero.
        return False, "payment_reverted", 0, ()
    if status != PAYMENT_EXECUTED:
        # No receipt on this node. Fail closed; see THE BLOCKER in the module
        # docstring — a non-zero rate here means enforcement would split.
        return False, "payment_status_unknown", 0, ()
    if bytes(payment.sender) != proof.requester:
        return False, "payment_sender_mismatch", 0, ()
    treasury = policy.treasury_digest(ctx.chain_id)
    if treasury is None:
        return False, f"treasury_unknown_chain:{ctx.chain_id}", 0, ()
    if bytes(payment.to) != treasury:
        return False, "payment_recipient_not_treasury", 0, ()
    if int(payment.amount) < int(policy.payment_floor_base_units):
        return False, f"payment_below_floor:{int(payment.amount)}", 0, ()
    if int(payment.height) > int(proof.anchorHeight):
        return False, "payment_after_anchor", 0, ()
    if int(payment.height) < ctx.height - int(policy.payment_max_age):
        return False, f"payment_too_old:{int(payment.height)}", 0, ()

    # ── check 5b: single use, decided by the block's own ancestry ───────────
    nulls = (
        proof.work_nullifier(chain_id=ctx.chain_id),
        proof.payment_nullifier(chain_id=ctx.chain_id),
    )
    for tag, reason in ((nulls[0], "nullifier_replay_work"), (nulls[1], "nullifier_replay_payment")):
        used = ctx.chain.nullifier_used_in_ancestry(tag)
        if used is None:
            # The scan could not be completed (pruned bodies). "Could not check"
            # is not "not seen".
            return False, "nullifier_scan_incomplete", 0, ()
        if used:
            return False, reason, 0, ()

    return True, None, _psi_ai_micro_raw(proof, policy), nulls


# ═══════════════════════════════════════════════════════════════════════════
# Block-level entry point
# ═══════════════════════════════════════════════════════════════════════════


def ai_work_nullifiers_of_body(
    body: Any, *, chain_id: int, max_body_bytes: int = POLICY_V1.max_proof_body_bytes
) -> Tuple[bytes, ...]:
    """Both nullifiers of one AI proof body, or () if it does not decode.

    Used by the ancestry replay scan, which must derive the tags from ancestor
    proof BODIES rather than trust the envelope's declared ``nullifier`` field:
    ancestors below the fork activation height were never verified, so their
    declared tags are unconstrained, and the payment nullifier never appears in
    an envelope at all. Signatures are deliberately NOT re-verified here — an
    ancestor's signature was already checked at its own import, and re-checking
    ``payment_max_age * max_proofs_per_block`` ML-DSA verifies per block would
    cost seconds for no added safety.

    Pure and total: any decode failure yields (), which is conservative — an
    undecodable ancestor proof simply spends no tag.
    """
    if not isinstance(body, (bytes, bytearray)) or not body:
        return ()
    try:
        proof = decode_ai_work_proof(bytes(body), max_body_bytes=int(max_body_bytes))
    except Exception:
        return ()
    try:
        return (
            proof.work_nullifier(chain_id=int(chain_id)),
            proof.payment_nullifier(chain_id=int(chain_id)),
        )
    except Exception:  # pragma: no cover - defensive
        return ()


def block_ai_work_nullifiers(
    block: Any, *, chain_id: int, policy: "UsefulWorkPolicy" = POLICY_V1
) -> Tuple[bytes, ...]:
    """Every AI-work nullifier spent by ``block``. Pure; never raises."""
    out: List[bytes] = []
    proofs = tuple(getattr(block, "proofs", ()) or ())
    for proof_like in proofs[: int(policy.max_proofs_per_block)]:
        env = _envelope_of(proof_like)
        if env is None:
            continue
        if canonical_proof_type_name(getattr(env, "type_id", None)) != "AI":
            continue
        out.extend(
            ai_work_nullifiers_of_body(
                getattr(env, "body", None),
                chain_id=chain_id,
                max_body_bytes=policy.max_proof_body_bytes,
            )
        )
    return tuple(out)


def _envelope_of(proof_like: Any) -> Optional[Any]:
    """Blocks hold typed wrappers (AIProofRef, HashShare, …) around a
    ``core.types.proof.ProofEnvelope``; some paths hold the envelope directly."""
    env = getattr(proof_like, "envelope", None)
    if env is not None:
        return env
    if hasattr(proof_like, "type_id") and hasattr(proof_like, "body"):
        return proof_like
    return None


def verify_block_useful_work(block: Any, ctx: BlockContext) -> BlockUsefulWorkReport:
    """Verify every proof a block carries and recompute Σψ.

    Pure. Never raises for a malformed block — a structural problem becomes a
    verdict with a reason, so the caller can log it identically in shadow and
    enforcing mode.
    """
    policy = ctx.policy
    proofs = tuple(getattr(block, "proofs", ()) or ())
    h_micro = 0
    try:
        h_micro = h_micro_from_hash256(ctx.header_hash)
    except Exception:
        h_micro = 0

    def _report(
        ok: bool,
        reason: Optional[str],
        verdicts: Sequence[ProofVerdict],
        psi_total: int,
        nulls: Sequence[bytes],
    ) -> BlockUsefulWorkReport:
        s_micro = h_micro + int(psi_total)
        return BlockUsefulWorkReport(
            height=int(ctx.height),
            proof_count=len(proofs),
            ok=ok,
            reason=reason,
            verdicts=tuple(verdicts),
            psi_micro_total=int(psi_total),
            h_micro=h_micro,
            theta_micro=int(ctx.theta_micro),
            s_micro=s_micro,
            theta_ok=s_micro >= int(ctx.theta_micro),
            nullifiers_spent=tuple(nulls),
            policy_digest=POLICY_V1_DIGEST if policy is POLICY_V1 else policy.digest(),
        )

    # The overwhelmingly common case on the live chain: no proofs at all. This
    # rule NEVER rejects for absence — see the module docstring.
    if not proofs:
        return _report(True, None, (), 0, ())

    # Policy-root agreement. Self-gating on zero: today every mainnet header
    # commits ZERO32 here, so this can only bite once a fork starts committing
    # a real root.
    committed_policy_root = bytes(ctx.poies_policy_root or b"")
    if committed_policy_root and committed_policy_root != _ZERO32:
        expected = POLICY_V1_DIGEST if policy is POLICY_V1 else policy.digest()
        if committed_policy_root != expected:
            return _report(
                False,
                f"policy_root_mismatch:header={committed_policy_root.hex()[:16]},"
                f"code={expected.hex()[:16]}",
                (),
                0,
                (),
            )

    if len(proofs) > int(policy.max_proofs_per_block):
        return _report(
            False, f"too_many_proofs:{len(proofs)}>{policy.max_proofs_per_block}", (), 0, ()
        )

    verdicts: List[ProofVerdict] = []
    contributions: List[Tuple[int, str, int]] = []
    all_nulls: List[bytes] = []
    seen_nulls: set[bytes] = set()
    first_failure: Optional[str] = None

    for idx, proof_like in enumerate(proofs):
        env = _envelope_of(proof_like)
        if env is None:
            verdicts.append(
                ProofVerdict(idx, -1, "UNKNOWN", False, "envelope_missing")
            )
            first_failure = first_failure or f"proof[{idx}]:envelope_missing"
            continue

        raw_type = getattr(env, "type_id", None)
        type_name = canonical_proof_type_name(raw_type) if raw_type is not None else None
        try:
            type_int = int(raw_type)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            type_int = -1

        if type_name is None:
            verdicts.append(
                ProofVerdict(idx, type_int, "UNKNOWN", False, f"unknown_proof_type:{type_int}")
            )
            first_failure = first_failure or f"proof[{idx}]:unknown_proof_type:{type_int}"
            continue
        if type_name not in SUPPORTED_PROOF_TYPE_NAMES:
            # Fail-closed. There is no working verifier for HASH_SHARE/QUANTUM/
            # STORAGE/VDF in this tree, and "accept what we cannot check" is how
            # a rule becomes satisfiable with fabricated bytes.
            verdicts.append(
                ProofVerdict(idx, type_int, type_name, False, f"unsupported_proof_type:{type_name}")
            )
            first_failure = first_failure or f"proof[{idx}]:unsupported_proof_type:{type_name}"
            continue

        body = getattr(env, "body", None)
        ok, reason, psi_raw, nulls = verify_ai_work_proof(
            body if isinstance(body, (bytes, bytearray)) else b"", ctx
        )

        if ok:
            # The envelope's declared nullifier must be the one the body
            # derives. Otherwise a miner could attach an arbitrary tag, and the
            # nullifier committed in proofsRoot would not be the tag the store
            # records — replay protection would be decorative.
            declared = getattr(env, "nullifier", None)
            if not isinstance(declared, (bytes, bytearray)) or bytes(declared) != nulls[0]:
                ok, reason = False, "envelope_nullifier_mismatch"

        if ok:
            # Two proofs in one block cannot share a tag; the store is only
            # consulted for previously-accepted blocks.
            for n in nulls:
                if n in seen_nulls:
                    ok, reason = False, "nullifier_duplicate_in_block"
                    break

        if ok:
            for n in nulls:
                seen_nulls.add(n)
            all_nulls.extend(nulls)
            contributions.append((idx, type_name, psi_raw))
            verdicts.append(
                ProofVerdict(idx, type_int, type_name, True, None, psi_raw, 0, tuple(nulls))
            )
        else:
            verdicts.append(ProofVerdict(idx, type_int, type_name, False, reason))
            first_failure = first_failure or f"proof[{idx}]:{reason}"

    capped, psi_total = _apply_caps(contributions, policy)
    # Re-stitch the capped values onto the successful verdicts, in order.
    cap_iter = iter(capped)
    final_verdicts: List[ProofVerdict] = []
    for v in verdicts:
        if v.ok:
            final_verdicts.append(
                ProofVerdict(
                    v.index,
                    v.type_id,
                    v.type_name,
                    True,
                    None,
                    v.psi_micro_raw,
                    next(cap_iter, 0),
                    v.nullifiers,
                )
            )
        else:
            final_verdicts.append(v)

    if first_failure is not None:
        # A block with ANY invalid proof is invalid: partial credit would let a
        # miner pad a block with junk and keep whatever happens to verify.
        return _report(False, first_failure, final_verdicts, psi_total, ())

    return _report(True, None, final_verdicts, psi_total, all_nulls)
