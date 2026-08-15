from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from typing import Optional

from core.utils.hash import sha3_256


@dataclass(frozen=True)
class NetworkParams:
    name: str
    chain_id: int
    expected_genesis_block_hash: Optional[bytes] = None


BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
GENESIS_DIR = BASE_DIR / "genesis"

MAINNET_GENESIS_HASH_HEX = (
    "0xa0892158cf997c56e91d0aa12e60c36037dae34800a2b54111a8fa17ec88b7de"
)
TESTNET_GENESIS_HASH_HEX = (
    "0xc0add9a3db2602b2d721d4cabd1681a5877842c66a6cf137b1d2f21c1af8dcf7"
)
DEVNET_GENESIS_HASH_HEX = (
    "0x5b9b0d304c5a1134d34b318bccada6089750868d904b3d16ba0225d356c2cc8d"
)

MAINNET_PARAMS = NetworkParams(
    name="mainnet",
    chain_id=1,
    expected_genesis_block_hash=bytes.fromhex(MAINNET_GENESIS_HASH_HEX[2:]),
)

TESTNET_PARAMS = NetworkParams(name="testnet", chain_id=2)
DEVNET_PARAMS = NetworkParams(name="devnet", chain_id=1337)

_BY_CHAIN_ID = {
    MAINNET_PARAMS.chain_id: MAINNET_PARAMS,
    TESTNET_PARAMS.chain_id: TESTNET_PARAMS,
    DEVNET_PARAMS.chain_id: DEVNET_PARAMS,
}

_BY_NAME = {
    MAINNET_PARAMS.name: MAINNET_PARAMS,
    TESTNET_PARAMS.name: TESTNET_PARAMS,
    DEVNET_PARAMS.name: DEVNET_PARAMS,
}

NETWORK_NAME_ALIASES = {
    "main": "mainnet",
    "test": "testnet",
    "dev": "devnet",
}

PINNED_GENESIS_BY_NETWORK: dict[tuple[str, int], bytes] = {
    ("mainnet", 1): bytes.fromhex(MAINNET_GENESIS_HASH_HEX[2:]),
    ("testnet", 2): bytes.fromhex(TESTNET_GENESIS_HASH_HEX[2:]),
    ("devnet", 1337): bytes.fromhex(DEVNET_GENESIS_HASH_HEX[2:]),
}

GENESIS_PATH_BY_NETWORK: dict[tuple[str, int], Path] = {
    ("mainnet", 1): GENESIS_DIR / "mainnet.json",
    ("testnet", 2): GENESIS_DIR / "testnet.json",
    ("devnet", 1337): GENESIS_DIR / "devnet.json",
}

# Pinned canonical checkpoints: (network, chain_id) -> {height: canonical_block_hash}.
# A node whose local block at a pinned height differs from the pinned hash is on a
# dead fork; at boot it rolls its head back to just below the lowest violated
# checkpoint and re-syncs the canonical chain (see core.chain.head
# .validate_head_against_checkpoint / _enforce_pinned_checkpoints). This is the
# force-convergence tool for the "nodes stuck on block N" outage: a legacy reorg
# left an orphan-sibling block in some nodes' by-height index at fork point 28167,
# and the by-height/HTTP (rpc_pull) sync path only ever requests local_height+1, so
# a node wedged on the orphan never re-requests 28167 — only a head rollback below
# it forces the corrected block to be re-pulled. The pinned hash MUST be the
# canonical block (the one the heaviest chain descends from), never the orphan.
PINNED_CHECKPOINTS_BY_NETWORK: dict[tuple[str, int], dict[int, bytes]] = {
    ("mainnet", 1): {
        # Fork point of the 06-2026 by-height-index corruption. Canonical block B
        # (head at 31908+ descends from it via 28168.parentHash == B); the orphan
        # sibling A = 0x0000001a81db6856ff09760994585ce5efd6b17014c63644f7b758a461311b12
        # is what wedged nodes were stuck on.
        28167: bytes.fromhex(
            "00000022379a38be2a2bd6e410a8a720044305483ccf239c4a385b29dfcd08f8"
        ),
        # Fork point of the 2026-07-07 natural 1-block fork. Canonical block B =
        # 0x00000000190117cd… (the head at 38729+ descends from it via
        # 38729.parentHash == B); the orphan sibling A =
        # 0x000000000ccc40684b6b2552666f3ad6ac89a3914bfac9f276bde5b033357b27
        # wedged every node that accepted it first: the headers pipeline
        # discarded the winning sibling at every layer (overlap trim /
        # anchor_mismatch / not-actionable reuse / below-head enqueue skip), so
        # the losing branch could never reorg and nodes sat at 38728 for days
        # while the network advanced. 7.2.0 also ships the generic fork-sibling
        # ingest in p2p_service; this pin force-converges already-wedged nodes
        # at boot (same remedy as 28167).
        38728: bytes.fromhex(
            "00000000190117cd360d56179f88d8d03474e1ab396d90d4ecdc69e6d1e4bc45"
        ),
        # Fork point of the 2026-07-14 natural 1-block fork. Canonical block B =
        # 0x0000000004c045379a4e1d049e7b225e951aa30ee9346718155dfb57a2ec44c9 (the
        # live head at 45204+ descends from it via 44855.parentHash == B, verified
        # against the pool-backed mainnet node on the majority-hashpower chain); the
        # orphan sibling A wedged nodes that accepted it first — same headers-pipeline
        # class as 38728 but recurring on each new natural fork because the fix only
        # covered the live p2p_service gossip path, not initial block download.
        # This pin force-converges already-wedged nodes at boot (rolls the head back
        # below 44854 so the canonical block is re-pulled) and rejects the orphan at
        # import. Kill-switch: ANIMICA_DISABLE_PINNED_CHECKPOINTS=1.
        44854: bytes.fromhex(
            "0000000004c045379a4e1d049e7b225e951aa30ee9346718155dfb57a2ec44c9"
        ),
    },
}

logger = logging.getLogger(__name__)


def get_network_params(
    *, chain_id: Optional[int] = None, network_name: Optional[str] = None
) -> Optional[NetworkParams]:
    if network_name:
        network_name = NETWORK_NAME_ALIASES.get(
            network_name.strip().lower(), network_name.strip().lower()
        )
    if chain_id is not None:
        return _BY_CHAIN_ID.get(int(chain_id))
    if network_name:
        return _BY_NAME.get(network_name.strip().lower())
    return None


def get_pinned_genesis_hash(
    *, chain_id: Optional[int] = None, network_name: Optional[str] = None
) -> Optional[bytes]:
    params = get_network_params(chain_id=chain_id, network_name=network_name)
    if params is None:
        return None
    if params.name == "devnet":
        pin_devnet = os.getenv("ANIMICA_PIN_DEVNET_GENESIS", "").strip().lower()
        if pin_devnet not in {"1", "true", "yes", "on"}:
            return None
    return PINNED_GENESIS_BY_NETWORK.get((params.name, params.chain_id))


def get_pinned_checkpoints(
    *, chain_id: Optional[int] = None, network_name: Optional[str] = None
) -> dict[int, bytes]:
    """Return {height: canonical_block_hash} pinned checkpoints for a network.

    Empty dict when none are pinned (the common case). Used at boot to force a
    node that is wedged on a dead fork back onto the canonical chain.

    Kill-switch: set ANIMICA_DISABLE_PINNED_CHECKPOINTS=1 to disable enforcement
    entirely (both the boot head-rollback and the import-time rejection), so a bad
    pin can never wedge a node beyond an operator override.
    """
    if os.getenv("ANIMICA_DISABLE_PINNED_CHECKPOINTS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return {}
    params = get_network_params(chain_id=chain_id, network_name=network_name)
    if params is None:
        return {}
    return dict(PINNED_CHECKPOINTS_BY_NETWORK.get((params.name, params.chain_id), {}))


# ── Consensus rule-activation heights (forward-only soft tightenings) ──────────
# (network, chain_id) -> {fork_name: activation_height}. A tightening activates at
# height >= its activation height and is grandfathered below it, so existing
# history stays valid: forward-only, never a genesis reset, never rejects a block
# produced before the height. Mainnet heights sit a safe margin above the head at
# ship time so all nodes AND the miner upgrade first (a non-upgraded miner that
# mined a now-invalid block after the height would simply be orphaned by upgraded
# nodes). Per-fork env override: ANIMICA_FORK_<UPPER>_HEIGHT (retune before ship).
#
#   pq_hardening: at/after this height, block import verifies every transaction
#     signature (mandatory) and accepts ONLY the non-forgeable production scheme
#     ml_dsa_65 (0x1003), closing the ANM-C01/C02 forgeable-stub fund-drain. The
#     node-local mempool/relay/mining rejection of forgeable schemes is always-on
#     and independent of this height (animica.tx.signing.ACCEPTED_TX_SIG_ALG_IDS).
FORK_PQ_HARDENING = "pq_hardening"

#   root_commitment: at/after this height, block import verifies the header's
#     committed txsRoot against the block's transactions (and, once the miner seals
#     them, stateRoot/receiptsRoot), closing the ANM-C03 silent-divergence hole
#     where a PoW-valid header could carry a different tx set or an invalid state
#     transition undetected. Self-gating (only enforces a root the header commits
#     non-zero) + grandfathered below H; the post-execution stateRoot check honours
#     ANIMICA_ROOT_COMMITMENT_SHADOW=1 (observe-only) for pre-activation validation.
FORK_ROOT_COMMITMENT = "root_commitment"

#   address_freeze: at/after this height, block import REJECTS any block that
#     contains a non-coinbase transaction whose authoritative sender OR recipient
#     is a consensus-frozen account (execution.migrations.address_freeze_2026).
#     This upgrades the node-local, trivially-bypassable mempool freeze
#     (mempool/address_denylist.py — a per-node env/file control that only stops
#     the operator's own nodes from ADMITTING or MINING a frozen spend) into a
#     hard, network-wide consensus rule: an upgraded node will not build on a
#     chain that moves frozen funds, so a stolen/leaked-key address cannot be
#     drained by taking the operator's node offline. The frozen set is a
#     code-committed constant (identical on every node — never env/file), so the
#     reject decision is deterministic; it writes NO state (stateRoot is only
#     shadow-enforced), so a rule-violating block is loudly orphaned rather than
#     silently forking balances. Forward-only + grandfathered below H; DAO-fork
#     shape — every block producer MUST run this build before the height or a
#     post-height frozen-spend block they mine will be orphaned by upgraded nodes.
FORK_ADDRESS_FREEZE = "address_freeze"

#   foundation_split: at/after this height, the block subsidy is split 85% miner /
#     15% foundation treasury instead of 100% miner. The base subsidy is UNCHANGED
#     (still 300 ANM/block at the current epoch, halving on the same schedule) — the
#     15% is a redistribution of the SAME total, not new issuance, so total emission
#     and the MAX_MONEY supply cap are unaffected (miner+treasury == the old miner
#     amount, every block). The 15% is credited to a code-committed foundation
#     address (consensus.rewards.FOUNDATION_TREASURY_ADDRESS — identical on every
#     node, never env/params) so the credit is deterministic and byte-identical.
#     UNLIKE address_freeze (a reject-only rule that writes no state and is loudly
#     orphaned on violation), this is a STATE-MUTATING emission change: this chain
#     commits no enforced stateRoot, so a node that does NOT run this build will keep
#     crediting 100% to the miner and silently under-credit the foundation — a
#     balance divergence, not a chain split (the same PoW-valid blocks are accepted
#     by both). There is therefore NO loud reject available; correctness depends on
#     coordinated upgrade. Every full node, the pool/miner, and every exchange or
#     explorer that tracks balances MUST run this build (or set
#     ANIMICA_FORK_FOUNDATION_SPLIT_HEIGHT) before this height, or their balances
#     diverge from the network. Forward-only + grandfathered below H (pre-H rewards
#     stay 100% miner; history is never re-credited). Activation is its OWN height,
#     one block after address_freeze, so the two rule changes are never co-located.
FORK_FOUNDATION_SPLIT = "foundation_split"

# FORK_STATE_COMMITMENT (7.1.9) — the deterministic "inclusion ⇒ execution" cure.
# From H, a block that commits a NON-ZERO stateRoot must commit the REAL
# post-execution root: every node re-applies the block to its parent state (the
# path _apply_block_state already runs for BOTH the tip and reorg) and REJECTS on
# mismatch. Self-gating on non-zero (a zero/uncommitted root is accepted, so a
# pre-fork or not-yet-upgraded miner's zero-root block never false-rejects — no
# split from adoption; only a WRONG non-zero root is rejected). The miner seals
# the root via BlockImporter.compute_sealed_state_root (identical apply → equal
# root by construction). Forward-only + grandfathered below H. This is state-
# VERIFYING, not state-producing, so it cannot change emission; but a determinism
# bug in compute_state_root would split, hence it enables only after a live
# shadow window with zero mismatches (ANIMICA_ROOT_COMMITMENT_SHADOW=1) and an
# adversarial review. Retunable via ANIMICA_FORK_STATE_COMMITMENT_HEIGHT.
FORK_STATE_COMMITMENT = "state_commitment"

# FORK_TREASURY_25 (9.4.0) — the foundation-treasury share of the per-block subsidy
# goes from 15% to 25%. Operator decision, activation height 70,000.
#
# This changes only the DIVISION of the subsidy, never its total: miner + treasury
# still equals the pre-split subsidy every block, the halving schedule is untouched
# and MAX_MONEY is unaffected — exactly like FORK_FOUNDATION_SPLIT before it. The
# percentage is a code-committed constant read through a height-gated helper (see
# consensus/rewards.foundation_split_pct) so emission is byte-identical on every
# node; it is never taken from params, env or wallclock at validation time.
#
# WHY IT IS SAFE TO GATE THIS WAY: every input is the block height, so two honest
# nodes on the same chain always compute the same split. Forward-only and
# grandfathered — blocks below H keep 85/15 forever and history is never
# re-credited.
#
# WHAT AN OPERATOR MUST DO: run >= 9.4.0 (or set ANIMICA_FORK_TREASURY_25_HEIGHT)
# BEFORE height 70,000. A node still on 9.3.x computes a 15% treasury output at
# H and will reject the network's 25% coinbase — it diverges on the first
# post-activation block. That is the entire upgrade obligation, and it is why the
# activation height needs enough runway for miners and exchanges to take the
# release. Retunable via ANIMICA_FORK_TREASURY_25_HEIGHT if adoption slips.
FORK_TREASURY_25 = "treasury_25"

# FORK_BOUNDED_RETARGET (9.5.0) — close a ONE-WAY TRAPDOOR in the difficulty
# retarget. Activation height 75,000.
#
# THE BUG, in the deployed code: `update_theta`'s emergency valve fires when a block
# interval exceeds max_block_time_s (mainnet 3600s) and returns
# `theta_micro = theta_min_micro` DIRECTLY — bypassing step_clamp_micro entirely. On
# live mainnet params that is theta 26,821,504 -> 12,000,000 µnats in a single block.
# theta is log-work, so per-block work falls by e^(26.82-12.00) ≈ 2.7 MILLION times at
# once.
#
# Then it cannot climb back. At the floor, blocks are produced as fast as the pool
# will emit them, but min_block_spacing_ms (60,000) pins dt at exactly the 60s target,
# so r_k = ln(dt/T) = ln(1) = 0. The error signal is identically zero, tau never rises,
# and theta stays on the floor FOREVER. One slow block permanently destroys the chain's
# work level — and with a single block producer (100% of recent blocks come from one
# coinbase) one stalled pool is all it takes.
#
# THE FIX, from H:
#   1. The emergency reduction is CLAMPED. It may fall by at most
#      EMERGENCY_STEP_MULTIPLE * step_clamp_micro per block instead of teleporting to
#      the floor, so a stall still recovers in a handful of blocks but cannot erase the
#      work level in one.
#   2. A FLOOR ESCAPE. When theta sits at/near theta_min and blocks are arriving no
#      slower than target, theta ratchets UP by the step clamp. "dt == target while
#      pinned at the floor" is evidence that the floor is too low, not evidence of
#      equilibrium — which is exactly the reading that made the trapdoor permanent.
#
# Both are gated on the height, forward-only and grandfathered: below H the function is
# byte-identical to today, so replaying history cannot change any theta. The rule is a
# pure function of (state, dt, height), so two honest nodes always agree.
# Retunable via ANIMICA_FORK_BOUNDED_RETARGET_HEIGHT.
FORK_BOUNDED_RETARGET = "bounded_retarget"

# FORK_VALUE_CALL (9.5.0) — a CALL may carry ANM. Activation height 75,000.
#
# TxKind.CALL had no amount field at all, which is why Animica Pay cannot perform an
# atomic 98/2 merchant split (it settles accounting-exact instead) and why a valueless
# CALL can grief a payment: every contract that wants to be paid has to fake it.
#
# BELOW H a CALL carrying a non-zero amount is INVALID. That is today's behaviour by
# construction — the field could not be expressed — so history is untouched and no
# existing transaction changes meaning. FROM H the amount is debited from the caller and
# credited to the callee before execution, and returned by revert semantics.
#
# The encoding is backward-compatible BY OMISSION: TxCall.to_obj() emits "amount" only
# when non-zero, and to_obj() is the canonical form the signing preimage and txid are
# computed over. Emitting it unconditionally would have changed the bytes of every CALL
# ever signed.
#
# This is a CAPABILITY ADDITION, not a redistribution: nobody's revenue changes, so
# adoption pressure is low and the risk is contained to the execution path rather than
# emission. Retunable via ANIMICA_FORK_VALUE_CALL_HEIGHT.
FORK_VALUE_CALL = "value_call"

# FORK_FINALITY_DEPTH (9.5.0) — make the reorg bound UNIFORM. Activation height 75,000.
#
# A depth bound already exists and is already enforced: DEFAULT_MAX_REORG_DEPTH = 96,
# checked in consensus.fork_choice.WeightForkChoice.add_block, which simply declines to
# make a tip canonical when the reorg would be deeper. What does NOT exist is agreement
# about the number — ANIMICA_MAX_REORG_DEPTH is an unbounded operator override, so today
# one node can be set to 0 (refuses EVERY reorg, and therefore strands itself on the
# next natural one-block fork — exactly the self-wedge that pinned checkpoints keep
# having to clean up) while another is set to 10^9 (accepts an arbitrarily deep reorg).
#
# From H the effective bound is CLAMPED into [MIN_REORG_DEPTH, FINALITY_DEPTH]:
#   * the ceiling gives the finality property — a block FINALITY_DEPTH deep is
#     irreversible on every node regardless of local configuration;
#   * the floor makes the self-wedge misconfiguration impossible.
#
# WHAT THIS DOES NOT DO, stated plainly because it is the reason it was requested: it
# would NOT have prevented the 28,167 / 38,728 / 44,854 wedges. Every one of those was
# a natural ONE-BLOCK fork (see the comments on PINNED_CHECKPOINTS_BY_NETWORK above), so
# a depth-96 guard never fired and a depth-100 guard never would. Those were caused by
# the headers pipeline discarding the winning sibling, not by a deep reorg. Finality is
# worth having on its own terms; it is not a fix for that outage class.
#
# It also never REJECTS a block — the guard only declines to make a tip canonical — so
# it cannot turn a node that is merely behind into a permanently split one. Retunable
# via ANIMICA_FORK_FINALITY_DEPTH_HEIGHT.
FORK_FINALITY_DEPTH = "finality_depth"

# FORK_QUANTUM_BEACON (9.5.0) — the canonical chain MAY commit to attested
# quantum-sourced randomness. Activation height 75,000, and DORMANT BY DESIGN.
#
# PRESENCE-GATED, NEVER REQUIRED. A block that carries no beacon commitment is valid at
# every height, forever. Only a block that DOES commit one must commit a correct one.
# That is deliberate and it is the whole safety argument: a liveness dependency on
# beacon availability would mean a QRNG outage halts the chain, so there is no such
# dependency to fail open — there is simply nothing to be unavailable. The rule can sit
# activated and inert indefinitely, and is expected to.
#
# Same self-gating shape as FORK_STATE_COMMITMENT (a zero/uncommitted root is accepted,
# so a not-yet-upgraded miner never false-rejects), for the same reason.
#
# WHAT IS CHECKED when a commitment is present: the beacon value is recomputed as
# H(BEACON_DOMAIN || round || prev || aggregate_commitment) — the same construction as
# randomness.qrng.public.build_quantum_beacon — and the committed `prev` must equal the
# beacon value committed by the nearest ancestor that carried one. Recomputation makes it
# unforgeable from block bytes alone; the prev-chain is what makes the CANONICAL CHAIN,
# rather than an individual block, commit to the randomness.
#
# Below H a commitment is IGNORED entirely, so history is untouched and a block that
# happened to carry a malformed one stays valid forever. Retunable via
# ANIMICA_FORK_QUANTUM_BEACON_HEIGHT.
FORK_QUANTUM_BEACON = "quantum_beacon"

# FORK_SERVICE_CARVE (9.5.0) — reserve a fixed slice of the miner subsidy for service
# providers whether or not one claims it. Activation height 75,000.
#
# This is the only rule in the 9.5.0 set that changes WHO GETS PAID, so it is the one to
# suspect first if balances look wrong after activation. The others at this height
# (bounded retarget, value CALL, finality, beacon) cannot move a coin.
#
# It is emission-CONSERVING: the miner loses exactly the carve, and paid + residual
# equals the carve, so nothing is minted or burned. See consensus/rewards.py
# service_carve_pct for why it cannot instead be keyed on "did this miner serve"
# (nothing in a block can prove that), and consensus/service_carve.py for the split.
#
# NOTE ON THE FAILURE MODE, which is unusual: coinbase AMOUNTS are never validated
# against the schedule (`_compute_block_reward_amount` has zero callers), yet state
# application credits balances from the node's OWN compute_block_reward. So an
# un-upgraded node does not fork or stall — it accepts identical blocks and credits
# different balances, silently and permanently. Liveness is safe; the ledger is not.
# Every full node, pool and balance-tracker must be on >=9.5.0 before H.
# Retunable via ANIMICA_FORK_SERVICE_CARVE_HEIGHT.
FORK_SERVICE_CARVE = "service_carve"

# FORK_VM_EXEC (9.6.0) — on-chain execution of vm_py contract CALLs turns on.
#
# Before H, a CALL to a deployed contract deterministically REVERTs (charging only
# intrinsic gas) — which is exactly today's behaviour, because the metered VM was
# never wired: every historical CALL reverted. That makes activation history-safe
# by construction: no past block changes meaning, because below H the outcome is
# byte-identical to what it has always been (REVERT).
#
# FROM H, a CALL is executed by the deterministic, gas-metered tree interpreter
# (vm_py.runtime.tree_engine) against a compiled-and-cached IR of the contract's
# source, with storage and treasury bound to chain state. The interpreter is a
# closed sandbox — no import/exec/eval/attribute escape — so arbitrary deployed
# code can at worst revert, burn gas (→ OOG), or touch its OWN namespaced storage;
# it can neither run host code nor read another contract's state. Contract DEPLOY
# is unchanged (it only stores code and has always succeeded).
#
# This is the enabler for the on-chain token launcher + DEX (the standard
# animica_token / animica_dex_* contracts). It is a STATE-MUTATING change: from H,
# calls that used to revert now succeed and move balances/storage, so every full
# node, pool and balance-tracking explorer/exchange MUST run >= 9.6.0 (or set
# ANIMICA_FORK_VM_EXEC_HEIGHT) before H, or it will compute a divergent state root.
# Read-only RPC simulate_call is NOT gated (it is a query, never consensus), so
# wallets can quote/preview before activation. Retunable via
# ANIMICA_FORK_VM_EXEC_HEIGHT.
FORK_VM_EXEC = "vm_exec"

# FORK_VPN_RELAY_REWARDS (8.0.1, REALIZED in 9.0.0 as IOU settlement) — from H,
# each block MAY settle service IOUs (dVPN relay/exit, AICF inference, media,
# hosting — any operator-issued IOU ledger) with REAL per-block payouts, capped
# at VPN_RELAY_REWARD_CAP (50 ANM, halving exactly when the block subsidy
# halves) and CARVED from the miner subsidy (emission-conserving: miner +
# settlement == the pre-fork miner output; never minted above the subsidy).
#
# MECHANISM (9.0.0, consensus/iou_settlement.py): a "settlement anchor" is an
# ordinary signed TRANSFER from the code-committed settlement authority (the
# foundation treasury account) carrying ANMSETL1 + a strict JSON distribution
# in TxTransfer.data. A block containing valid anchors pays the anchored
# entries in THAT block (cap-scaled). Pre-9.0.0 nodes accept the anchor tx as
# a plain transfer, so inclusion itself never splits the chain.
#
# SAFETY — the Sybil/inflation surface of the 8.0.1 design is closed by
# construction: consensus never measures off-chain contribution; it only
# rate-limits (cap) and executes distributions signed by the code-committed
# authority, which is settling ITS OWN off-chain IOU liabilities. SELF-GATING:
# with no anchors posted, behaviour at/after H is byte-identical to 8.0.x, so
# the operator arms settlement only after network adoption (the first anchor
# is the activation switch, exactly like FORK_STATE_COMMITMENT's sealed roots).
# Forward-only; grandfathered below H. NON-UPGRADED nodes credit the full
# subsidy to the miner once anchors flow — operators MUST run >= 9.0.0 before
# the first anchor posts. Retunable via ANIMICA_FORK_VPN_RELAY_REWARDS_HEIGHT.
FORK_VPN_RELAY_REWARDS = "vpn_relay_rewards"

# Readable 9.0.0 alias (same fork key, same height, same env override).
FORK_IOU_SETTLEMENT = FORK_VPN_RELAY_REWARDS

# FORK_USEFUL_WORK_VERIFY (10.2.0) — VERIFY the useful-work proofs a block carries.
# DELIBERATELY UNSET ON MAINNET (see ACTIVATION_HEIGHTS_BY_NETWORK below): the
# activation height is absent, so get_activation_height() returns None and
# is_fork_active() is False at every height. The rule cannot fire on mainnet until
# an operator sets ANIMICA_FORK_USEFUL_WORK_VERIFY_HEIGHT. That is the point — the
# spec wants a SHADOW window first, and a default height would have removed the
# operator's decision.
#
# WHAT IT DOES: at/after H, every proof envelope in an imported block is verified
# (structure, canonical CBOR, ML-DSA-65 signature with the scheme id pinned by the
# VERIFIER not read from the proof, miner==worker, requester!=worker, receipt
# freshness against an ancestor anchor, single-use nullifier, payment reference) and
# Σψ is recomputed under code-committed policy caps. An invalid proof rejects the
# block. See consensus/useful_work_verify.py.
#
# WHAT IT DOES *NOT* DO — read this before planning step (3):
#   1. It NEVER REQUIRES A PROOF. A block carrying zero proofs is valid at every
#      height, forever. This rule is PRESENCE-GATED, exactly like
#      FORK_QUANTUM_BEACON: it only constrains blocks that DO carry proofs. Live
#      mainnet blocks carry zero proofs in 95,004 of 95,004 stored blocks (recon
#      2026-08-15), so activating this alone is a no-op on today's fleet and CANNOT
#      halt it. "A block MUST carry a verified AI proof" is a SEPARATE, later fork —
#      that one is a 100% hard cutover and must not be co-located with this height.
#   2. It NEVER GRANTS ψ CREDIT. Live acceptance is `header_hash <= target(Θ)`
#      (core/chain/block_import._pow_sanity), i.e. S = H(u) with Σψ == 0. Crediting
#      Σψ toward Θ would RELAX the work requirement and would require a matching
#      change on the miner side; that is out of scope here and would be a split.
#      The recomputed Σψ and the S = H(u) + Σψ ≥ Θ relationship are computed and
#      LOGGED for telemetry, never used to accept a block that PoW rejected. This
#      gate can only ever REJECT (tighten), never admit.
#   3. It cannot make self-dealing impossible. See the "residual weakness" docstring
#      in consensus/useful_work_verify.py — a permissionless miner can always fund a
#      second identity. The design goal is a priced floor, not a barrier.
#   4. IT DOES NOT FORCE REAL INFERENCE, and cannot in this shape. Check 2 requires
#      worker == coinbase, so the receipt is ALWAYS self-signed by the miner; a
#      miner running a null worker satisfies every check with a fabricated
#      outputDigest. That is asserted by a regression test
#      (consensus/tests/test_useful_work_adversarial.py::
#      test_forged_proof_for_zero_inference_is_accepted) precisely so it cannot be
#      quietly re-described as "serve or don't mine". Do not present it that way to
#      operators or miners.
#
# ENFORCEMENT PREREQUISITE (not yet met, 2026-08-15): payment execution status is
# read from the node-local receipt side-table because headers commit
# receiptsRoot = 0, so the outcome of a transfer is not chain-derived. The verifier
# fails closed with `payment_status_unknown`, which is safe while the gate is
# disabled or observe-only but would split an enforcing fleet between nodes that
# executed a range and nodes that snapshot-synced past it. Fix that (commit
# receipts in a header root, or replace the payment check) before enforcing.
#
# SHADOW SWITCH: ANIMICA_USEFUL_WORK_SHADOW=1 makes the gate observe-only (log the
# would-be rejection with reason/height/proof index/type, accept anyway), modelled on
# ANIMICA_PQ_HARDENING_SHADOW. A shadow node and an enforcing node DISAGREE about a
# block carrying an invalid proof, so the shadow valve is a rollout tool for a window
# in which no such block exists — never a permanent per-node setting. Run shadow
# until the logs show a real fleet attaching well-formed proofs, THEN unset it.
FORK_USEFUL_WORK_VERIFY = "useful_work_verify"

ACTIVATION_HEIGHTS_BY_NETWORK: dict[tuple[str, int], dict[str, int]] = {
    # Mainnet consensus activation = 40,000 (operator-chosen coordinated height).
    # This MUST match on every node — the live node and every operator's pip install
    # — or nodes that enforce diverge from nodes that don't the moment a rule-violating
    # block appears. It is shipped in pip 6.0.1 (6.0.0 carried a placeholder 100,000);
    # operators must run 6.0.1 (or set ANIMICA_FORK_PQ_HARDENING_HEIGHT /
    # _ROOT_COMMITMENT_HEIGHT=40000) before this height. Normal empty/coinbase-only
    # zero-root blocks pass the gates at activation (C02 skips coinbase, C03 self-gates
    # on zero roots, emission is value-preserving), so activation does not fork honest
    # miners; only rule-violating blocks are rejected — the point. P2P-transparent: the
    # height is NOT folded into the pinned mainnet params-hash, so upgraded and legacy
    # nodes still peer during the runway.
    ("mainnet", 1): {
        FORK_PQ_HARDENING: 40_000,
        FORK_ROOT_COMMITMENT: 40_000,
        # Coordinated consensus address-freeze activation. Ships in pip 7.0.0.
        # Head was ~40,834 at ship time, giving a ~1-day upgrade runway; every
        # miner + node operator MUST run 7.0.0 (or set
        # ANIMICA_FORK_ADDRESS_FREEZE_HEIGHT) before this height. Retunable via
        # that env override if adoption slips.
        FORK_ADDRESS_FREEZE: 42_000,
        # Foundation subsidy split (85% miner / 15% foundation treasury). Ships in
        # pip 7.1.0 (a strict superset of 7.0.0). Its own height, one block after
        # the freeze so the two consensus changes never activate on the same block.
        # Head was ~40,920 at ship time (~1-day runway). STATE-MUTATING emission
        # change: every node, the pool, and every balance-tracking exchange/explorer
        # MUST run 7.1.0 (or set ANIMICA_FORK_FOUNDATION_SPLIT_HEIGHT) before this
        # height or they will silently under-credit the foundation. Retunable via
        # that env override if adoption slips.
        FORK_FOUNDATION_SPLIT: 42_001,
        # State-commitment enforcement (7.1.9). Operator-chosen height 44,444.
        # DORMANT until the enforcement path is shadow-validated + reviewed and the
        # miner is sealing real roots network-wide; retune via
        # ANIMICA_FORK_STATE_COMMITMENT_HEIGHT. Self-gating on non-zero root means a
        # premature activation cannot split honest zero-root miners.
        FORK_STATE_COMMITMENT: 44_444,
        # Treasury share 15% -> 25% (9.7.0). MOVED to 75,000 so the full reward-
        # split change activates in ONE step with FORK_SERVICE_CARVE and
        # FORK_VM_EXEC: at 75,000 the split goes 85/15 -> 50% miner / 25% treasury
        # / 25% inference (unclaimed inference rolls to the treasury -> up to 50%).
        # It was never applied at 70,000 (the reward code never read the flag), so
        # moving it changes no realized history. Retune with
        # ANIMICA_FORK_TREASURY_25_HEIGHT.
        FORK_TREASURY_25: 75_000,
        # Bounded retarget + floor escape (9.5.0). Operator-chosen height 75,000.
        # Grandfathered below H, so no historical theta is recomputed. Retune with
        # ANIMICA_FORK_BOUNDED_RETARGET_HEIGHT.
        FORK_BOUNDED_RETARGET: 75_000,
        # Value-carrying CALL (9.5.0). Operator-chosen height 75,000.
        FORK_VALUE_CALL: 75_000,
        # Uniform reorg bound / finality (9.5.0). Operator-chosen height 75,000.
        FORK_FINALITY_DEPTH: 75_000,
        # Quantum beacon binding (9.5.0). Activated but DORMANT: presence-gated, so
        # until miners choose to commit a beacon this rule does nothing at all.
        FORK_QUANTUM_BEACON: 75_000,
        # Service carve (9.5.0). The ONLY emission change at this height.
        FORK_SERVICE_CARVE: 75_000,
        # On-chain vm_py contract CALL execution (9.6.0). Operator-chosen height
        # 75,000. History-safe: below H every CALL reverted (the VM was never
        # wired), so no past block changes meaning. STATE-MUTATING from H — every
        # full node / pool / balance-tracker MUST run >= 9.6.0 (or set
        # ANIMICA_FORK_VM_EXEC_HEIGHT) before H. Retunable via that env override.
        FORK_VM_EXEC: 75_000,
        # dVPN relay block rewards (8.0.1). Operator-chosen height 50,000 (shared with
        # the consensus ANS fork gate). SELF-GATING + INERT: emits zero relay outputs
        # until an on-chain relay-contribution root is sealed, which requires the
        # (designed, not-yet-enabled) on-chain relay-registration + usage-anchoring
        # mechanism AND an adversarial review to clear Sybil/inflation. Retune via
        # ANIMICA_FORK_VPN_RELAY_REWARDS_HEIGHT. Until sealed, activation cannot mint
        # or change emission — behaviour is byte-identical to no-fork.
        FORK_VPN_RELAY_REWARDS: 50_000,
        # FORK_USEFUL_WORK_VERIFY is DELIBERATELY ABSENT for mainnet. An absent key
        # makes get_activation_height() return None and is_fork_active() False at
        # every height, so the rule is disabled by default and no code path can turn
        # it on by accident. Enabling it is an explicit, documented operator action:
        #
        #   ANIMICA_FORK_USEFUL_WORK_VERIFY_HEIGHT=<height>   # arm the gate
        #   ANIMICA_USEFUL_WORK_SHADOW=1                       # observe-only first
        #
        # Recommended rollout (see docs/USEFUL_WORK_SHADOW_RUNBOOK.md):
        #   1. Set the height to a value BELOW the current head with the shadow env
        #      ON. The gate then evaluates every incoming block immediately and logs;
        #      because it is presence-gated and live blocks carry no proofs, the
        #      expected reading is "proofs=0" on every block and zero would-be
        #      rejections.
        #   2. Leave it running until the first non-zero proof count appears — that is
        #      the signal that a miner has upgraded and is attaching real evidence.
        #   3. Only after a clean window on a fleet that actually attaches proofs,
        #      unset the shadow env and PIN a future height here in code so every node
        #      agrees without env configuration.
        # Do NOT ship a code-pinned mainnet height in the same release that first
        # introduces the verifier.
    },
    # Testnet + devnet enforce from genesis (no legacy history to grandfather).
    ("testnet", 2): {
        FORK_PQ_HARDENING: 0,
        FORK_ROOT_COMMITMENT: 0,
        FORK_ADDRESS_FREEZE: 0,
        FORK_FOUNDATION_SPLIT: 0,
        FORK_STATE_COMMITMENT: 0,
        FORK_TREASURY_25: 0,
        FORK_BOUNDED_RETARGET: 0,
        FORK_VALUE_CALL: 0,
        FORK_FINALITY_DEPTH: 0,
        FORK_QUANTUM_BEACON: 0,
        FORK_SERVICE_CARVE: 0,
        FORK_VM_EXEC: 0,
        # Safe from genesis because the rule is presence-gated: a block with no
        # proofs is never rejected, and testnet/devnet have no proof-carrying
        # history to grandfather. This is where the verifier gets exercised.
        FORK_USEFUL_WORK_VERIFY: 0,
    },
    ("devnet", 1337): {
        FORK_PQ_HARDENING: 0,
        FORK_ROOT_COMMITMENT: 0,
        FORK_ADDRESS_FREEZE: 0,
        FORK_FOUNDATION_SPLIT: 0,
        FORK_STATE_COMMITMENT: 0,
        FORK_TREASURY_25: 0,
        FORK_BOUNDED_RETARGET: 0,
        FORK_VALUE_CALL: 0,
        FORK_FINALITY_DEPTH: 0,
        FORK_QUANTUM_BEACON: 0,
        FORK_SERVICE_CARVE: 0,
        FORK_VM_EXEC: 0,
        # See the testnet note above: presence-gated, so genesis activation is inert
        # until a block actually carries a proof.
        FORK_USEFUL_WORK_VERIFY: 0,
    },
}


def get_activation_height(
    fork: str, *, chain_id: Optional[int] = None, network_name: Optional[str] = None
) -> Optional[int]:
    """Return the activation height for a consensus `fork` on a network, or None.

    An env override ``ANIMICA_FORK_<FORK>_HEIGHT`` (integer, accepts 0x…) takes
    precedence so operators can retune the height before shipping.
    """
    env = os.getenv(f"ANIMICA_FORK_{fork.upper()}_HEIGHT", "").strip()
    if env:
        try:
            return int(env, 0)
        except ValueError:
            logger.warning(
                "ignoring non-integer ANIMICA_FORK_%s_HEIGHT=%r", fork.upper(), env
            )
    params = get_network_params(chain_id=chain_id, network_name=network_name)
    if params is None:
        return None
    return ACTIVATION_HEIGHTS_BY_NETWORK.get((params.name, params.chain_id), {}).get(fork)


def is_fork_active(
    fork: str,
    height: int,
    *,
    chain_id: Optional[int] = None,
    network_name: Optional[str] = None,
) -> bool:
    """True iff consensus `fork` is active at `height` on the given network.

    Forward-only: returns False below the activation height (grandfathered) and
    for unknown networks/forks, so a new rule never retroactively rejects an
    unrecognized chain's history.
    """
    h = get_activation_height(fork, chain_id=chain_id, network_name=network_name)
    if h is None:
        return False
    try:
        return int(height) >= int(h)
    except (TypeError, ValueError):
        return False


def get_network_genesis_path(
    *, chain_id: Optional[int] = None, network_name: Optional[str] = None
) -> Optional[Path]:
    params = get_network_params(chain_id=chain_id, network_name=network_name)
    if params is None:
        return None
    return GENESIS_PATH_BY_NETWORK.get((params.name, params.chain_id))


def get_expected_genesis_hash(chain_id: int) -> Optional[bytes]:
    params = get_network_params(chain_id=chain_id)
    if params is None:
        return None
    return params.expected_genesis_block_hash


# ANM-6.0.0 P2P deploy blocker: compute_network_params_hash() below hashes the RAW
# BYTES of this module (+ params.py / consensus/types.py). That made the P2P
# fingerprint drift on ANY edit to this file — even a comment or a *dormant* forward
# fork (e.g. adding FORK_ROOT_COMMITMENT with activation H=37000) — which fragmented an
# upgraded node from every peer still on the prior release: the handshake rejects on
# network_params_mismatch, and also on consensus_mismatch because
# core/chain/identity.py::_consensus_id_fingerprint folds this same hash into the
# consensus-id. So the forward-only / height-gated strategy did NOT survive at the P2P
# layer. To let upgraded and legacy nodes COEXIST during the rollout runway before a
# fork activates, the fingerprint for a live network is PINNED to its canonical value,
# decoupling P2P identity from source edits — the intended "future forks do not change
# the P2P identity" behavior. Update a pin ONLY for a deliberate, coordinated
# network-identity change (cf. PINNED_GENESIS_BY_NETWORK).
PINNED_NETWORK_PARAMS_HASH_BY_CHAIN: dict[int, bytes] = {
    # mainnet (chain 1): the value the live network produces (verified against the
    # running node). Every peer computes this, so an upgraded node MUST reproduce it
    # byte-for-byte or it is rejected on handshake.
    1: bytes.fromhex(
        "41f0acb8b3ac98ddee524a7bb1752f6af25dc596c71003fd3df4a69d899730b1"
    ),
}


def compute_network_params_hash(chain_id: Optional[int] = None) -> bytes:
    """
    Compute a fingerprint that captures network parameters and consensus constants.

    This is used for P2P compatibility checks to avoid syncing incompatible chains.

    For known live networks the value is PINNED (see PINNED_NETWORK_PARAMS_HASH_BY_CHAIN)
    so it stays stable across releases and source edits. Without the pin, adding a
    dormant forward fork (or any edit to this module) would change the fingerprint and
    fragment upgraded nodes from legacy peers on network_params_mismatch /
    consensus_mismatch. Un-pinned (dev / ephemeral) chains fall back to hashing the
    source identity below.
    """
    if chain_id is not None:
        pinned = PINNED_NETWORK_PARAMS_HASH_BY_CHAIN.get(int(chain_id))
        if pinned is not None:
            return bytes(pinned)
    files = [
        Path(__file__).resolve(),
        BASE_DIR / "types" / "params.py",
        REPO_ROOT / "consensus" / "types.py",
    ]
    payload = bytearray()
    if chain_id is not None:
        payload.extend(f"chain_id:{int(chain_id)}".encode())
        expected = get_expected_genesis_hash(int(chain_id))
        if expected:
            payload.extend(b"genesis:")
            payload.extend(expected)
    for path in files:
        if path.exists():
            payload.extend(b"|")
            payload.extend(path.read_bytes())
    return sha3_256(bytes(payload))


def is_mainnet_name(network_name: Optional[str]) -> bool:
    if not network_name:
        return False
    return network_name.strip().lower() in {"mainnet", "main"}


def enforce_pinned_genesis(
    *,
    chain_id: int,
    genesis_block_hash: bytes,
    genesis_path: Optional[str] = None,
    network_name: Optional[str] = None,
) -> None:
    from core.errors import GenesisError

    params = get_network_params(chain_id=chain_id, network_name=network_name)
    if params is None:
        return
    expected = get_pinned_genesis_hash(chain_id=chain_id, network_name=params.name)
    if expected is None:
        return

    expected_path = get_network_genesis_path(chain_id=chain_id, network_name=params.name)
    resolved_path = Path(genesis_path).resolve() if genesis_path else None
    expected_path_resolved = expected_path.resolve() if expected_path else None

    if expected_path_resolved and resolved_path and resolved_path != expected_path_resolved:
        strict_path_check = os.getenv("ANIMICA_STRICT_GENESIS_PATH", "").strip().lower()
        if strict_path_check in {"1", "true", "yes", "on"}:
            raise GenesisError(
                "genesis path does not match canonical network genesis",
                expected_path=str(expected_path_resolved),
                genesis_path=str(resolved_path),
                chain_id=chain_id,
                network=params.name,
                hint=(
                    "Set ANIMICA_GENESIS_PATH/GENESIS_PATH to the canonical file or update"
                    " the network configuration to point at the correct genesis file."
                ),
            )
        logger.warning(
            "Selected genesis path differs from canonical network path; continuing because pinned hash validation still applies",
            extra={
                "chain_id": chain_id,
                "network": params.name,
                "expected_path": str(expected_path_resolved),
                "genesis_path": str(resolved_path),
            },
        )

    expected_hex = "0x" + expected.hex()
    found_hex = "0x" + genesis_block_hash.hex()
    path_hint = str(resolved_path or expected_path_resolved or genesis_path or "<unknown>")

    if genesis_block_hash != expected:
        raise GenesisError(
            "genesis does not match pinned network genesis",
            expected=expected_hex,
            found=found_hex,
            genesis_path=path_hint,
            chain_id=chain_id,
            network=params.name,
            hint=(
                "The configured genesis file does not match the pinned hash. "
                "If this is the correct genesis file, update PINNED_GENESIS_BY_NETWORK; "
                "otherwise point to the correct network genesis file."
            ),
        )

    logger.info(
        "[genesis] Selected genesis: %s hash=%s pinned=%s",
        path_hint,
        found_hex,
        expected_hex,
    )
