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
    },
    # Testnet + devnet enforce from genesis (no legacy history to grandfather).
    ("testnet", 2): {
        FORK_PQ_HARDENING: 0,
        FORK_ROOT_COMMITMENT: 0,
    },
    ("devnet", 1337): {
        FORK_PQ_HARDENING: 0,
        FORK_ROOT_COMMITMENT: 0,
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
