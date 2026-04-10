"""
mempool2.evict - Deterministic Eviction Logic
=============================================

Eviction policies for managing mempool capacity.
All eviction is deterministic (no randomness).
"""

from __future__ import annotations

import logging
from typing import List

from coretx import TxId

from .storage import MempoolStorage

__all__ = [
    "check_capacity",
    "evict_lowest_fee",
    "per_sender_limit",
]

log = logging.getLogger(__name__)


def check_capacity(
    storage: MempoolStorage,
    max_txs: int,
    max_bytes: int,
) -> List[TxId]:
    """
    Check if mempool exceeds capacity limits.
    
    Returns transactions to evict to get back under limits.
    Evicts lowest fee transactions first (deterministic order).
    
    Args:
        storage: Mempool storage
        max_txs: Maximum number of transactions
        max_bytes: Maximum total bytes
        
    Returns:
        List of TxIds to evict
    """
    stats = storage.get_stats()
    
    to_evict = []
    
    # Check if over capacity
    if stats.tx_count <= max_txs and stats.total_bytes <= max_bytes:
        return to_evict
    
    # Calculate how many txs to remove
    txs_over = max(0, stats.tx_count - max_txs)
    
    # If over byte limit, need to evict enough to get under
    # Approximate: evict lowest fee txs until under limit
    current_bytes = stats.total_bytes
    
    # Iterate lowest fee first (deterministic order: fee asc, arrival asc)
    for entry in storage.iter_by_fee(descending=False):
        if stats.tx_count - len(to_evict) <= max_txs and current_bytes <= max_bytes:
            break
        
        to_evict.append(entry.txid)
        current_bytes -= entry.size_bytes()
    
    if to_evict:
        log.info(f"Capacity check: evicting {len(to_evict)} txs (over limits)")
    
    return to_evict


def evict_lowest_fee(storage: MempoolStorage, count: int) -> List[TxId]:
    """
    Evict the N lowest fee transactions.
    
    Deterministic order: fee_rate ascending, then arrival_time ascending.
    
    Args:
        storage: Mempool storage
        count: Number of transactions to evict
        
    Returns:
        List of TxIds evicted
    """
    if count <= 0:
        return []
    
    to_evict = []
    
    # Iterate lowest fee first
    for entry in storage.iter_by_fee(descending=False):
        if len(to_evict) >= count:
            break
        to_evict.append(entry.txid)
    
    if to_evict:
        log.info(f"Evicting {len(to_evict)} lowest fee transactions")
    
    return to_evict


def per_sender_limit(
    storage: MempoolStorage,
    sender: bytes,
    max_per_sender: int,
) -> List[TxId]:
    """
    Check if sender has too many pending transactions.
    
    Returns transactions to evict if sender exceeds limit.
    Evicts highest nonce transactions first (most speculative).
    
    Args:
        storage: Mempool storage
        sender: Sender address (32 bytes)
        max_per_sender: Maximum transactions per sender
        
    Returns:
        List of TxIds to evict (empty if under limit)
    """
    sender_txs = storage.get_sender_txs(sender)
    
    if len(sender_txs) <= max_per_sender:
        return []
    
    # Evict highest nonce first (most speculative)
    # sender_txs is already sorted by nonce ascending
    over_limit = len(sender_txs) - max_per_sender
    to_evict = [entry.txid for entry in sender_txs[-over_limit:]]
    
    if to_evict:
        log.info(
            f"Sender {sender.hex()[:16]}... has {len(sender_txs)} txs "
            f"(limit {max_per_sender}), evicting {len(to_evict)}"
        )
    
    return to_evict


def evict_expired(
    storage: MempoolStorage,
    current_time: float,
    max_age_seconds: float,
) -> List[TxId]:
    """
    Evict transactions that have been in mempool too long.
    
    Args:
        storage: Mempool storage
        current_time: Current timestamp
        max_age_seconds: Maximum age in seconds
        
    Returns:
        List of TxIds to evict
    """
    to_evict = []
    
    for entry in storage.list_txs():
        age = current_time - entry.arrival_time
        if age > max_age_seconds:
            to_evict.append(entry.txid)
    
    if to_evict:
        log.info(f"Evicting {len(to_evict)} expired transactions (age > {max_age_seconds}s)")
    
    return to_evict
