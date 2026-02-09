"""
mempool2.template - Block Template Selection
============================================

Select transactions for block templates.
Enforces nonce ordering and resource limits.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Set

from coretx import TxEnvelope

from .storage import MempoolStorage

__all__ = ["select_txs"]

log = logging.getLogger(__name__)


def select_txs(
    storage: MempoolStorage,
    max_gas: int,
    max_bytes: int,
) -> List[TxEnvelope]:
    """
    Select transactions for a block template.
    
    Strategy:
    - Sort by fee_rate descending (highest fee first)
    - Enforce nonce ordering per sender (cannot include N+1 without N)
    - Stop when gas or byte limit reached
    
    Args:
        storage: Mempool storage
        max_gas: Maximum gas limit for block
        max_bytes: Maximum bytes for block
        
    Returns:
        List of transaction envelopes, ready for inclusion
    """
    selected: List[TxEnvelope] = []
    total_gas = 0
    total_bytes = 0
    
    # Track which nonces we've included per sender
    sender_nonces: Dict[bytes, Set[int]] = {}
    
    # Track the next expected nonce per sender (for ordering)
    sender_next_nonce: Dict[bytes, int] = {}
    
    # First pass: determine starting nonce for each sender
    # This is the minimum nonce for each sender in the mempool
    for entry in storage.list_txs():
        sender = entry.sender
        nonce = entry.nonce
        
        if sender not in sender_next_nonce:
            sender_next_nonce[sender] = nonce
        else:
            sender_next_nonce[sender] = min(sender_next_nonce[sender], nonce)
    
    # Second pass: select transactions in fee order, respecting nonce ordering
    for entry in storage.iter_by_fee(descending=True):
        envelope = entry.envelope
        sender = envelope.body.from_addr
        nonce = envelope.body.nonce
        gas_needed = envelope.body.gas_limit
        bytes_needed = entry.size_bytes()
        
        # Check resource limits
        if total_gas + gas_needed > max_gas:
            log.debug(f"Gas limit reached at {total_gas}/{max_gas}")
            continue
        
        if total_bytes + bytes_needed > max_bytes:
            log.debug(f"Byte limit reached at {total_bytes}/{max_bytes}")
            continue
        
        # Check nonce ordering
        expected_nonce = sender_next_nonce.get(sender)
        
        if expected_nonce is None:
            # Should not happen after first pass, but handle gracefully
            log.warning(f"Sender {sender.hex()[:16]}... not in sender_next_nonce")
            continue
        
        if nonce < expected_nonce:
            # Already included a higher nonce - skip this one
            log.debug(
                f"Skipping tx with nonce {nonce} (already included {expected_nonce})"
            )
            continue
        elif nonce > expected_nonce:
            # Gap in nonces - cannot include yet
            log.debug(
                f"Nonce gap for sender {sender.hex()[:16]}...: "
                f"expected {expected_nonce}, got {nonce}"
            )
            continue
        # else: nonce == expected_nonce - this is the next transaction
        
        # Track this nonce
        if sender not in sender_nonces:
            sender_nonces[sender] = set()
        sender_nonces[sender].add(nonce)
        
        # Add to selection
        selected.append(envelope)
        total_gas += gas_needed
        total_bytes += bytes_needed
        
        # Update next expected nonce
        sender_next_nonce[sender] = nonce + 1
    
    log.info(
        f"Selected {len(selected)} transactions "
        f"(gas: {total_gas}/{max_gas}, bytes: {total_bytes}/{max_bytes})"
    )
    
    return selected


def select_txs_simple(
    storage: MempoolStorage,
    max_count: int,
) -> List[TxEnvelope]:
    """
    Simple selection: just take top N transactions by fee rate.
    
    No nonce ordering or resource checks.
    Useful for testing or simple scenarios.
    
    Args:
        storage: Mempool storage
        max_count: Maximum number of transactions
        
    Returns:
        List of transaction envelopes
    """
    selected: List[TxEnvelope] = []
    
    for entry in storage.iter_by_fee(descending=True):
        if len(selected) >= max_count:
            break
        selected.append(entry.envelope)
    
    log.info(f"Selected {len(selected)} transactions (simple mode)")
    return selected
