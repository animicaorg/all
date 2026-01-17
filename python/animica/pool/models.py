"""
Database models for the mining pool.

All monetary values are stored as integers in base units (no floats).
All timestamps are UTC.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4


class BlockState(enum.Enum):
    """Block lifecycle states."""

    FOUND = "found"  # Share met network difficulty
    SUBMITTED = "submitted"  # Submitted to node RPC
    ACCEPTED = "accepted"  # Confirmed in main chain
    ORPHANED = "orphaned"  # Reorged out of main chain
    CONFIRMED = "confirmed"  # Reached maturity confirmations
    PAID = "paid"  # Payouts executed


class PayoutState(enum.Enum):
    """Payout lifecycle states."""

    CREATED = "created"  # Payout batch created
    SENT = "sent"  # Transaction submitted
    CONFIRMED = "confirmed"  # Transaction confirmed
    FAILED = "failed"  # Transaction failed


@dataclass
class Miner:
    """Miner identity and settings."""

    id: UUID
    payout_address: str  # bech32 address
    created_at: datetime
    last_seen_at: datetime
    settings_json: Optional[str] = None  # VarDiff prefs, etc.

    @staticmethod
    def create(payout_address: str) -> Miner:
        """Create a new miner record."""
        now = datetime.utcnow()
        return Miner(
            id=uuid4(),
            payout_address=payout_address,
            created_at=now,
            last_seen_at=now,
            settings_json=None,
        )


@dataclass
class Worker:
    """Worker connection (one miner can have multiple workers)."""

    id: int
    miner_id: UUID
    name: str  # Worker name from username
    connected_at: datetime
    last_seen_at: datetime
    ip: Optional[str] = None
    user_agent: Optional[str] = None


@dataclass
class Share:
    """Accepted or rejected share submission."""

    id: int
    miner_id: UUID
    worker_id: Optional[int]
    height: int  # Template block height
    job_id: str
    difficulty: float  # Share difficulty
    work: int  # Integer work weight (no floats)
    accepted: bool
    reason: Optional[str]  # Rejection reason if not accepted
    created_at: datetime

    @property
    def is_valid(self) -> bool:
        """Check if share was accepted."""
        return self.accepted


@dataclass
class Block:
    """Found block record."""

    id: int
    height: int
    hash: str
    prev_hash: str
    found_at: datetime
    finder_miner_id: UUID  # Who found this block
    state: BlockState
    network_difficulty: float
    target: str
    coinbase_value: int  # In base units
    confirmations: int
    orphaned: bool
    payout_txid: Optional[str]
    pplns_window_start_share_id: Optional[int]
    pplns_window_end_share_id: Optional[int]
    metadata_json: Optional[str]


@dataclass
class Balance:
    """Miner balance tracking."""

    payout_address: str  # Primary key
    immature: int  # Not yet confirmed
    mature: int  # Ready for payout
    paid_total: int  # Lifetime paid
    updated_at: datetime

    def total_unpaid(self) -> int:
        """Total balance not yet paid."""
        return self.immature + self.mature


@dataclass
class Payout:
    """Payout batch record."""

    id: int
    created_at: datetime
    mode: str  # "pplns"
    state: PayoutState
    txid: Optional[str]
    total_amount: int  # Base units
    fee_amount: int  # Base units
    metadata_json: Optional[str]


@dataclass
class PayoutItem:
    """Individual payout within a batch."""

    id: int
    payout_id: int
    payout_address: str
    amount: int  # Base units
    block_id: Optional[int]  # Which block this is from
    details_json: Optional[str]  # Proof data: shares, work, weight


@dataclass
class MinerStats:
    """Aggregated statistics for a miner."""

    miner_id: UUID
    payout_address: str
    total_shares: int
    accepted_shares: int
    rejected_shares: int
    stale_shares: int
    invalid_shares: int
    total_work: int  # Sum of work weights
    hashrate_ema: float  # Exponential moving average
    last_share_at: Optional[datetime]
    blocks_found: int
    total_earned: int  # Base units
    total_paid: int  # Base units
    balance_unpaid: int  # Base units


@dataclass
class PoolStats:
    """Pool-wide statistics."""

    total_miners: int
    active_miners: int  # Active in last hour
    total_workers: int
    active_workers: int
    pool_hashrate: float  # EMA-based
    shares_per_minute: float
    total_shares: int
    accepted_shares: int
    rejected_shares: int
    blocks_found: int
    blocks_confirmed: int
    blocks_orphaned: int
    last_block_at: Optional[datetime]
    luck_percent: float  # vs expected based on difficulty
    total_paid: int  # Base units
    unpaid_balances: int  # Base units


@dataclass
class WorkerStats:
    """Per-worker statistics."""

    worker_id: int
    worker_name: str
    miner_id: UUID
    connected_at: datetime
    last_seen_at: datetime
    shares_accepted: int
    shares_rejected: int
    hashrate_ema: float
    is_active: bool
