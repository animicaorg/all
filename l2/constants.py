"""L2 protocol constants, limits, and enums.

Everything here is consensus-relevant: two nodes that disagree on any value below
will disagree on state roots. Changes require a protocol-version bump and, where
they affect L1 anchoring, a fork-height gate.
"""

from __future__ import annotations

import enum
from typing import Final

# ── identity / replay domains ────────────────────────────────────────────────

# L2 chain id. Distinct from L1 chainId (1) so an L1 tx can never be replayed as
# an L2 tx and vice-versa. Mainnet L2 = 1001; devnet = 421337.
L2_CHAIN_ID_MAINNET: Final[int] = 1001
L2_CHAIN_ID_DEVNET: Final[int] = 421337

# Domain-separation tag mixed into every L2 signing preimage. Bumping the trailing
# integer invalidates all prior signatures — do this only on a breaking change to
# the signed body layout.
SIGN_DOMAIN: Final[bytes] = b"animica.l2.tx.v1"

# ANM base unit. 1 ANM = 10**9 nanos, identical to L1 (see AnimicaConfig on L1).
# All L2 balances/amounts/fees are integers in nanos. Floating point is banned.
NANOS_PER_ANM: Final[int] = 1_000_000_000

# Canonical L2 treasury account — fees always accrue here so ANM stays inside L2
# (fees move between accounts, they are never destroyed) and the conservation
# invariant holds exactly. Deterministic + protocol-fixed so the sequencer and
# any independent verifier agree without carrying it in a batch's public inputs.
import hashlib as _hashlib  # noqa: E402

L2_TREASURY_ADDRESS: Final[bytes] = _hashlib.sha3_256(b"animica.l2.treasury.v1").digest()

# ── L1 anchoring fork ────────────────────────────────────────────────────────

# Height on Animica L1 at which 10.0.0 L2 anchoring transactions (state-root
# commitments + DA blobs published as L1 txs to the bridge address) become
# consensus-meaningful. Below this height an anchoring tx is an opaque memo;
# at/above it, full nodes that advertise 10.0.0 support validate it. Chosen to
# sit above the pending 75,000 fork bundle so it never collides with them.
FORK_L2_ANCHOR_HEIGHT: Final[int] = 80_000
FORK_L2_ANCHOR_NAME: Final[str] = "FORK_L2_ANCHOR"

# The canonical L1 bridge account that locks deposited ANM and receives anchoring
# transactions. Resolved from network params at runtime; this is the devnet
# default (a burn-style deterministic address, overridable via config).
BRIDGE_ADDRESS_ENV: Final[str] = "ANIMICA_L2_BRIDGE_ADDRESS"

# ── sizes / limits (anti-DoS; consensus for the encoder) ─────────────────────

ADDR_LEN: Final[int] = 32  # L1/L2 accounts are the 32-byte sha3 address digest
HASH_LEN: Final[int] = 32
SIG_LEN: Final[int] = 3309  # ML-DSA-65 signature
PUBKEY_LEN: Final[int] = 1952  # ML-DSA-65 public key

MAX_TX_BYTES: Final[int] = 16 * 1024  # a single encoded L2 tx
MAX_MEMO_BYTES: Final[int] = 256
MAX_BATCH_PAYMENT_RECIPIENTS: Final[int] = 4096
MAX_TXS_PER_BATCH: Final[int] = 1_000_000
MAX_BATCH_BYTES: Final[int] = 64 * 1024 * 1024

# Amounts are unsigned and bounded so overflow is impossible in fixed-width
# reasoning even though Python ints are arbitrary precision. Ceiling is far above
# total ANM supply.
MAX_AMOUNT: Final[int] = 2**96 - 1

# ── signature schemes ────────────────────────────────────────────────────────


class SigScheme(enum.IntEnum):
    """Signature scheme id carried in the tx envelope. Only ML-DSA-65 (the L1
    canonical 0x1003 scheme) is accepted for value-moving txs in 10.0.0."""

    ML_DSA_65 = 1


# ── transaction types ────────────────────────────────────────────────────────


class TxType(enum.IntEnum):
    """L2 transaction kinds. The integer is part of the canonical encoding and
    signing preimage — never renumber an existing entry."""

    TRANSFER = 0  # plain account->account value move
    PAY = 1  # transfer carrying an Animica-Pay memo (invoice/order ref)
    DEPOSIT_CLAIM = 2  # credit L2 from a finalized L1 deposit (bridge-authorized)
    WITHDRAW = 3  # burn on L2, unlock claimable on L1
    FORCED_WITHDRAW = 4  # withdraw initiated via L1 escape hatch
    ESCROW_OPEN = 5
    ESCROW_RELEASE = 6
    ESCROW_REFUND = 7
    AGENT_PAYMENT = 8  # machine-to-machine payment w/ agent + task binding
    INFERENCE_PAYMENT = 9  # payment bound to an AICF inference request receipt
    BATCH_PAYMENT = 10  # one signature authorizes many (recipient, amount) pairs


# Types a user signs and submits to the sequencer directly.
USER_SIGNED_TYPES: Final[frozenset] = frozenset(
    {
        TxType.TRANSFER,
        TxType.PAY,
        TxType.WITHDRAW,
        TxType.ESCROW_OPEN,
        TxType.ESCROW_RELEASE,
        TxType.ESCROW_REFUND,
        TxType.AGENT_PAYMENT,
        TxType.INFERENCE_PAYMENT,
        TxType.BATCH_PAYMENT,
    }
)

# Types minted by the protocol from an L1 event rather than a free-standing user
# submission. Their authenticity comes from the bridge/deposit indexer, not a
# fresh user signature over an L2 nonce.
PROTOCOL_MINTED_TYPES: Final[frozenset] = frozenset(
    {TxType.DEPOSIT_CLAIM, TxType.FORCED_WITHDRAW}
)


# ── lifecycle states ─────────────────────────────────────────────────────────


class TxStatus(str, enum.Enum):
    """Explicit lifecycle. Sequencer acceptance (SOFT_CONFIRMED) is never
    represented as L1 finality (L1_FINALIZED)."""

    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    SOFT_CONFIRMED = "SOFT_CONFIRMED"
    BATCHED = "BATCHED"
    PROVEN = "PROVEN"
    L1_SUBMITTED = "L1_SUBMITTED"
    L1_FINALIZED = "L1_FINALIZED"
    FAILED = "FAILED"
    REVERTED = "REVERTED"


class SettlementMode(str, enum.Enum):
    VALIDITY = "VALIDITY"  # state transition re-verifiable by anyone
    OPTIMISTIC = "OPTIMISTIC"  # bonded sequencer + challenge window
    DEV = "DEV"  # local development; NOT trust-minimized


# ── L1 event confirmation tiers (reorg safety) ───────────────────────────────


class L1Confirmation(str, enum.Enum):
    OBSERVED = "OBSERVED"  # seen in a block; may vanish on reorg
    CONFIRMED = "CONFIRMED"  # buried under CONFIRM_DEPTH blocks
    FINALIZED = "FINALIZED"  # past the L1 finality depth; safe to credit


# Deposits are only credited to L2 once FINALIZED, so an L1 reorg can never
# create unbacked L2 ANM.
L1_CONFIRM_DEPTH: Final[int] = 12
L1_FINALITY_DEPTH: Final[int] = 64

# ── execution / batching defaults (tunable via config; not consensus) ────────

DEFAULT_EXEC_WORKERS: Final[int] = 0  # 0 = auto (cpu_count - 1)
DEFAULT_SIG_WORKERS: Final[int] = 0  # 0 = auto
DEFAULT_BATCH_MAX_TXS: Final[int] = 50_000
DEFAULT_BATCH_MAX_MS: Final[int] = 250
DEFAULT_BATCH_MAX_BYTES: Final[int] = 8 * 1024 * 1024
