"""
core.aicf_pool - AICF Pool State Management
============================================

Protocol-level AICF (AI Compute Fund) pool accounting.

The AICF pool is NOT a wallet address - it's a system-level accounting object
that tracks:
- Pool balance (earned via AICF mining)
- Cap (maximum that can be earned)
- Issued total (cumulative mined)
- Spent total (cumulative spent on compute/payouts)

Design principles:
- Deterministic: all nodes compute same state from same inputs
- Auditable: all changes are logged/tracked
- Consensus-safe: state changes only via validated transactions/blocks
- No premine: pool starts at 0, fills via AICF mining
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

log = logging.getLogger("core.aicf_pool")

# Constants
COIN = 1_000_000_000  # 1 ANM = 1B nANM (base units)


@dataclass
class AicfPoolState:
    """
    AICF pool state object.
    
    This is NOT a wallet - it's protocol-level accounting.
    Balance can only change via protocol rules (mining, spending, fees).
    """
    balance: int = 0  # Current pool balance in base units (nANM)
    cap: int = 0  # Maximum pool balance (cap) in base units
    issued_total: int = 0  # Cumulative issued (mined) into pool
    spent_total: int = 0  # Cumulative spent from pool
    
    # Miner tracking (for rate limiting)
    miner_credits: Dict[bytes, int] = field(default_factory=dict)  # miner_addr -> credits earned
    epoch_proofs: Dict[int, Dict[bytes, int]] = field(default_factory=dict)  # epoch -> miner -> count
    
    def __post_init__(self):
        """Validate invariants"""
        if self.balance < 0:
            raise ValueError(f"AICF pool balance cannot be negative: {self.balance}")
        if self.cap < 0:
            raise ValueError(f"AICF pool cap cannot be negative: {self.cap}")
        if self.issued_total < 0:
            raise ValueError(f"AICF issued_total cannot be negative: {self.issued_total}")
        if self.spent_total < 0:
            raise ValueError(f"AICF spent_total cannot be negative: {self.spent_total}")
        # Balance should equal issued - spent
        expected_balance = self.issued_total - self.spent_total
        if self.balance != expected_balance:
            raise ValueError(
                f"AICF pool balance inconsistent: balance={self.balance}, "
                f"issued={self.issued_total}, spent={self.spent_total}, "
                f"expected={expected_balance}"
            )
    
    def can_issue(self, amount: int) -> Tuple[bool, str]:
        """
        Check if we can issue (mint) amount into pool.
        
        Returns (can_issue, reason)
        """
        if amount <= 0:
            return (False, "amount must be positive")
        
        new_issued = self.issued_total + amount
        if new_issued > self.cap:
            return (False, f"would exceed cap: {new_issued} > {self.cap}")
        
        return (True, "ok")
    
    def issue(self, amount: int, miner_addr: Optional[bytes] = None) -> None:
        """
        Issue (mint) amount into pool.
        
        This increases both balance and issued_total.
        Optionally tracks which miner earned the credit.
        """
        can, reason = self.can_issue(amount)
        if not can:
            raise ValueError(f"Cannot issue {amount} to AICF pool: {reason}")
        
        self.balance += amount
        self.issued_total += amount
        
        if miner_addr:
            self.miner_credits[miner_addr] = self.miner_credits.get(miner_addr, 0) + amount
        
        log.info(f"AICF pool issued {amount} nANM. New balance: {self.balance}, issued: {self.issued_total}")
    
    def can_spend(self, amount: int) -> Tuple[bool, str]:
        """
        Check if we can spend amount from pool.
        
        Returns (can_spend, reason)
        """
        if amount <= 0:
            return (False, "amount must be positive")
        
        if amount > self.balance:
            return (False, f"insufficient balance: {amount} > {self.balance}")
        
        return (True, "ok")
    
    def spend(self, amount: int, reason: str = "") -> None:
        """
        Spend amount from pool.
        
        This decreases balance and increases spent_total.
        Reason is for logging/auditing.
        """
        can, msg = self.can_spend(amount)
        if not can:
            raise ValueError(f"Cannot spend {amount} from AICF pool: {msg}")
        
        self.balance -= amount
        self.spent_total += amount
        
        log.info(f"AICF pool spent {amount} nANM ({reason}). New balance: {self.balance}, spent: {self.spent_total}")
    
    def add_fee(self, amount: int) -> None:
        """
        Add fee to pool (does NOT count toward issued_total or cap).
        
        Fees from contracts are a separate inflow that replenishes the pool.
        """
        if amount <= 0:
            return
        
        self.balance += amount
        log.info(f"AICF pool received fee: {amount} nANM. New balance: {self.balance}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Export to dict for RPC/storage"""
        return {
            "balance": self.balance,
            "cap": self.cap,
            "issued_total": self.issued_total,
            "spent_total": self.spent_total,
            "balance_anm": self.balance / COIN,
            "cap_anm": self.cap / COIN,
            "issued_anm": self.issued_total / COIN,
            "spent_anm": self.spent_total / COIN,
            "percent_filled": (self.issued_total / self.cap * 100) if self.cap > 0 else 0,
        }
    
    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> AicfPoolState:
        """Import from dict with validation"""
        # Validate and convert miner_credits
        miner_credits = {}
        for k, v in d.get("miner_credits", {}).items():
            try:
                miner_credits[bytes.fromhex(k)] = v
            except (ValueError, AttributeError) as e:
                log.warning(f"Invalid miner_credits key '{k}': {e}")
                continue
        
        # Validate and convert epoch_proofs
        epoch_proofs = {}
        for epoch, miners in d.get("epoch_proofs", {}).items():
            try:
                epoch_int = int(epoch)
                epoch_proofs[epoch_int] = {}
                for k, v in miners.items():
                    try:
                        epoch_proofs[epoch_int][bytes.fromhex(k)] = v
                    except (ValueError, AttributeError) as e:
                        log.warning(f"Invalid epoch_proofs miner key '{k}' in epoch {epoch}: {e}")
                        continue
            except (ValueError, TypeError) as e:
                log.warning(f"Invalid epoch key '{epoch}': {e}")
                continue
        
        return AicfPoolState(
            balance=int(d.get("balance", 0)),
            cap=int(d.get("cap", 0)),
            issued_total=int(d.get("issued_total", 0)),
            spent_total=int(d.get("spent_total", 0)),
            miner_credits=miner_credits,
            epoch_proofs=epoch_proofs,
        )


@dataclass
class AicfProof:
    """
    AICF proof data structure.
    
    This represents a proof of useful work for AICF mining.
    For now, this is a placeholder - real AI compute verification would plug in here.
    """
    miner_addr: bytes  # 32-byte address of miner
    work_units: int  # Deterministic work units completed
    proof_data: bytes  # Proof payload (e.g., hash commitment, merkle proof, etc.)
    timestamp: int  # Unix timestamp
    nonce: int  # Nonce for uniqueness
    
    def __post_init__(self):
        """Validate fields"""
        if len(self.miner_addr) != 32:
            raise ValueError(f"miner_addr must be 32 bytes, got {len(self.miner_addr)}")
        if self.work_units < 0:
            raise ValueError(f"work_units must be >= 0, got {self.work_units}")
        if self.timestamp < 0:
            raise ValueError(f"timestamp must be >= 0, got {self.timestamp}")
        if self.nonce < 0:
            raise ValueError(f"nonce must be >= 0, got {self.nonce}")
    
    def to_dict(self) -> Dict[str, Any]:
        """Export to dict"""
        return {
            "miner_addr": self.miner_addr.hex(),
            "work_units": self.work_units,
            "proof_data": self.proof_data.hex(),
            "timestamp": self.timestamp,
            "nonce": self.nonce,
        }
    
    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> AicfProof:
        """Import from dict"""
        return AicfProof(
            miner_addr=bytes.fromhex(d["miner_addr"]),
            work_units=int(d["work_units"]),
            proof_data=bytes.fromhex(d["proof_data"]),
            timestamp=int(d["timestamp"]),
            nonce=int(d["nonce"]),
        )


def verify_aicf_proof(
    proof: AicfProof,
    params: Mapping[str, Any],
    height: int,
    pool_state: AicfPoolState,
) -> Tuple[bool, str, int]:
    """
    Verify an AICF proof.
    
    This is a placeholder verification that checks basic validity.
    Real AI compute verification would plug in here.
    
    Returns (is_valid, reason, reward_amount)
    """
    # Check work difficulty
    min_difficulty = params.get("aicf_pool", {}).get("min_work_difficulty", 10)
    if proof.work_units < min_difficulty:
        return (False, f"insufficient work: {proof.work_units} < {min_difficulty}", 0)
    
    # Check rate limits
    max_per_block = params.get("aicf_pool", {}).get("max_proofs_per_block", 10)
    epoch_blocks = params.get("aicf_pool", {}).get("epoch_blocks", 1440)
    max_per_epoch = params.get("aicf_pool", {}).get("max_proofs_per_miner_per_epoch", 1000)
    
    current_epoch = height // epoch_blocks
    epoch_count = pool_state.epoch_proofs.get(current_epoch, {}).get(proof.miner_addr, 0)
    
    if epoch_count >= max_per_epoch:
        return (False, f"miner exceeded epoch limit: {epoch_count} >= {max_per_epoch}", 0)
    
    # Calculate reward
    reward_per_proof_anm = params.get("aicf_pool", {}).get("reward_per_proof_anm", 10.0)
    reward_amount = int(reward_per_proof_anm * COIN)
    
    # Check if we can issue this reward
    can, reason = pool_state.can_issue(reward_amount)
    if not can:
        return (False, f"cannot issue reward: {reason}", 0)
    
    return (True, "valid", reward_amount)


def apply_aicf_proof(
    proof: AicfProof,
    params: Mapping[str, Any],
    height: int,
    pool_state: AicfPoolState,
) -> Tuple[bool, str, int]:
    """
    Apply a verified AICF proof to pool state.
    
    Returns (success, reason, reward_amount)
    """
    is_valid, reason, reward_amount = verify_aicf_proof(proof, params, height, pool_state)
    
    if not is_valid:
        return (False, reason, 0)
    
    # Issue reward to pool
    pool_state.issue(reward_amount, proof.miner_addr)
    
    # Track epoch count
    epoch_blocks = params.get("aicf_pool", {}).get("epoch_blocks", 1440)
    current_epoch = height // epoch_blocks
    
    if current_epoch not in pool_state.epoch_proofs:
        pool_state.epoch_proofs[current_epoch] = {}
    
    miner_epoch_count = pool_state.epoch_proofs[current_epoch].get(proof.miner_addr, 0)
    pool_state.epoch_proofs[current_epoch][proof.miner_addr] = miner_epoch_count + 1
    
    log.info(
        f"Applied AICF proof from {proof.miner_addr.hex()[:16]}... "
        f"work={proof.work_units}, reward={reward_amount} nANM, "
        f"epoch={current_epoch}, count={miner_epoch_count + 1}"
    )
    
    return (True, "ok", reward_amount)
