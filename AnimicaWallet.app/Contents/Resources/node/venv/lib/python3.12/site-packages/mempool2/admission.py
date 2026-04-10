"""
mempool2.admission - Never-Throw Admission Engine
=================================================

Transaction admission that NEVER throws exceptions.
All errors are caught and converted to TxReject with diagnostics.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

from coretx import RejectReason, TxEnvelope, TxId, TxReject
from coretx.errors import reject
from coretx.canonical import compute_txid, decode_tx_envelope
from coretx.signing import verify_tx

from . import policy
from .storage import MempoolStorage
from .types import MempoolEntry, TxSource

__all__ = ["admit_tx", "AdmissionEngine"]

log = logging.getLogger(__name__)


class AdmissionEngine:
    """
    Admission engine with configurable policy parameters.
    
    Coordinates all validation checks and storage operations.
    Never throws exceptions - always returns (success, Optional[TxReject]).
    """
    
    def __init__(
        self,
        storage: MempoolStorage,
        chain_id: int = 1,
        max_tx_bytes: int = 128 * 1024,  # 128KB
        min_fee_rate: int = 1,  # 1 wei/gas
    ):
        """
        Initialize admission engine.
        
        Args:
            storage: Mempool storage backend
            chain_id: Expected chain ID
            max_tx_bytes: Maximum transaction size
            min_fee_rate: Minimum fee rate (wei/gas)
        """
        self.storage = storage
        self.chain_id = chain_id
        self.max_tx_bytes = max_tx_bytes
        self.min_fee_rate = min_fee_rate
    
    def admit_tx(
        self,
        envelope: TxEnvelope,
        source: str = "rpc",
        peer_id: Optional[str] = None,
        balance_getter: Optional[callable] = None,
    ) -> Tuple[bool, Optional[TxReject]]:
        """
        Admit transaction to mempool.
        
        NEVER THROWS EXCEPTIONS. All errors caught and converted to TxReject.
        
        Steps:
        1. Validate format
        2. Verify PQ signature
        3. Check chain_id
        4. Check size
        5. Check fee rate
        6. Check nonce (if balance_getter provided)
        7. Check funds (if balance_getter provided)
        8. Store in storage
        
        Args:
            envelope: Transaction envelope to admit
            source: Source of transaction ("rpc", "p2p", "local", "resubmit")
            peer_id: Optional peer ID if source is p2p
            balance_getter: Optional function(address) -> (balance, nonce) for state checks
            
        Returns:
            (True, None) if admitted successfully
            (False, TxReject) if rejected with reason
        """
        try:
            # Step 1: Format validation
            rejection = policy.check_format(envelope)
            if rejection:
                log.debug(f"Format check failed: {rejection}")
                return (False, rejection)
            
            # Step 2: Verify signature
            rejection = verify_tx(envelope)
            if rejection:
                log.debug(f"Signature verification failed: {rejection}")
                return (False, rejection)
            
            # Step 3: Chain ID check
            rejection = policy.check_chain_id(envelope, self.chain_id)
            if rejection:
                log.debug(f"Chain ID check failed: {rejection}")
                return (False, rejection)
            
            # Step 4: Size check
            rejection = policy.check_size(envelope, self.max_tx_bytes)
            if rejection:
                log.debug(f"Size check failed: {rejection}")
                return (False, rejection)
            
            # Step 5: Fee check
            rejection = policy.check_fee(envelope, self.min_fee_rate)
            if rejection:
                log.debug(f"Fee check failed: {rejection}")
                return (False, rejection)
            
            # Step 6 & 7: State-dependent checks (nonce, funds)
            if balance_getter:
                sender = envelope.body.from_addr
                
                try:
                    balance, confirmed_nonce = balance_getter(sender)
                except Exception as e:
                    # State query failed - treat as internal error
                    rejection = reject(
                        RejectReason.internal_error,
                        message="Failed to query sender state",
                        hint="Node may be syncing or experiencing issues",
                        context={
                            "sender": sender.hex(),
                            "error": str(e),
                        },
                        error_class=type(e).__name__,
                    )
                    log.error(f"Balance getter failed: {rejection}")
                    return (False, rejection)
                
                # Check nonce
                pending_nonces = self.storage.get_sender_nonces(sender)
                rejection = policy.check_nonce(envelope, confirmed_nonce, pending_nonces)
                if rejection:
                    log.debug(f"Nonce check failed: {rejection}")
                    return (False, rejection)
                
                # Check funds
                pending_debits = self.storage.get_sender_pending_debits(sender)
                rejection = policy.check_funds(envelope, balance, pending_debits)
                if rejection:
                    log.debug(f"Funds check failed: {rejection}")
                    return (False, rejection)
            
            # Check for duplicate
            if self.storage.has_tx(envelope.txid):
                rejection = reject(
                    RejectReason.tx_already_known,
                    message=f"Transaction {envelope.txid.hex()} already in mempool",
                    hint="No action needed - transaction is already queued",
                    context={"txid": envelope.txid.hex()},
                )
                log.debug(f"Duplicate tx: {rejection}")
                return (False, rejection)
            
            # Calculate fee rate
            fee_rate = envelope.body.fee // envelope.body.gas_limit
            
            # Step 8: Store in storage
            entry = MempoolEntry(
                envelope=envelope,
                arrival_time=time.time(),
                fee_rate=fee_rate,
                source=TxSource(source),
                peer_id=peer_id,
            )
            
            added = self.storage.add_tx(entry)
            if not added:
                # Race condition - already added
                rejection = reject(
                    RejectReason.tx_already_known,
                    message=f"Transaction {envelope.txid.hex()} already in mempool",
                    hint="No action needed - transaction is already queued",
                    context={"txid": envelope.txid.hex()},
                )
                return (False, rejection)
            
            log.info(f"Admitted tx {envelope.txid.hex()} from {source}")
            return (True, None)
        
        except Exception as e:
            # Catch-all for any unexpected errors
            # This should never happen, but ensures admission never throws
            rejection = reject(
                RejectReason.internal_error,
                message=f"Unexpected error during admission: {str(e)}",
                hint="This is a bug - please report to developers",
                context={
                    "error": str(e),
                    "source": source,
                },
                error_class=type(e).__name__,
            )
            log.error(f"Unexpected admission error: {rejection}", exc_info=True)
            return (False, rejection)


def admit_tx(
    envelope: TxEnvelope,
    storage: MempoolStorage,
    source: str = "rpc",
    peer_id: Optional[str] = None,
    balance_getter: Optional[callable] = None,
    chain_id: int = 1,
    max_tx_bytes: int = 128 * 1024,
    min_fee_rate: int = 1,
) -> Tuple[bool, Optional[TxReject]]:
    """
    Convenience function for admitting a transaction.
    
    Creates a temporary AdmissionEngine with the given parameters.
    
    Args:
        envelope: Transaction envelope to admit
        storage: Mempool storage backend
        source: Source of transaction
        peer_id: Optional peer ID
        balance_getter: Optional function(address) -> (balance, nonce)
        chain_id: Expected chain ID
        max_tx_bytes: Maximum transaction size
        min_fee_rate: Minimum fee rate
        
    Returns:
        (success, Optional[TxReject])
    """
    engine = AdmissionEngine(
        storage=storage,
        chain_id=chain_id,
        max_tx_bytes=max_tx_bytes,
        min_fee_rate=min_fee_rate,
    )
    return engine.admit_tx(envelope, source, peer_id, balance_getter)
