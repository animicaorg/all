"""
ENA Protocol Integration Extension
====================================

Extends ENA service with AICF protocol deposit recording.

Add to config.py:
    # AICF Protocol
    AICF_PROTOCOL_ENABLED = os.getenv("ENA_AICF_PROTOCOL_ENABLED", "true").lower() in ("true", "1", "yes")
    AICF_PROTOCOL_DB = os.getenv("ENA_AICF_PROTOCOL_DB", "./ena_data/aicf_protocol.db")
    AICF_EPOCH_LENGTH = int(os.getenv("ENA_AICF_EPOCH_LENGTH", "1000"))

Add to main.py startup:
    # Initialize AICF protocol recorder (if enabled)
    protocol_recorder = None
    if Config.AICF_PROTOCOL_ENABLED:
        try:
            from aicf.protocol.integration import create_protocol_recorder
            protocol_recorder = create_protocol_recorder(
                db_path=Config.AICF_PROTOCOL_DB,
                epoch_length_blocks=Config.AICF_EPOCH_LENGTH,
            )
            logger.info(f"AICF protocol recorder initialized: {Config.AICF_PROTOCOL_DB}")
        except ImportError as e:
            logger.warning(f"AICF protocol module not available: {e}")
            protocol_recorder = None

Add to pricing endpoint:
    @app.get("/v1/pricing")
    async def get_pricing() -> PricingInfo:
        ...
        # Add protocol status if enabled
        protocol_status = None
        if Config.AICF_PROTOCOL_ENABLED and protocol_recorder:
            try:
                protocol_status = protocol_recorder.get_protocol_status()
            except Exception as e:
                logger.warning(f"Failed to get protocol status: {e}")
        
        return PricingInfo(
            ...
            protocol=protocol_status,  # Add this field
        )

Add to infer after marking transactions as used (around line 505):
                if tx_hash_aicf:
                    database.mark_transaction_used(
                        tx_hash=tx_hash_aicf,
                        payer=request_data.payment.payer,
                        amount=aicf_paid,
                        request_id=request_id,
                    )
                    
                    # Record AICF deposit in protocol (NEW)
                    if Config.AICF_PROTOCOL_ENABLED and protocol_recorder and aicf_paid > 0:
                        try:
                            # Get block height from RPC
                            try:
                                tx_data = rpc_client.get_transaction(tx_hash_aicf)
                                block_height = tx_data.get("blockNumber")
                                if block_height and isinstance(block_height, str):
                                    block_height = int(block_height, 16 if block_height.startswith("0x") else 10)
                            except Exception as e:
                                logger.warning(f"Failed to get block height: {e}")
                                block_height = None
                            
                            # Record deposit
                            inflow_id = protocol_recorder.record_ena_deposit(
                                amount=aicf_paid,
                                tx_hash=tx_hash_aicf,
                                block_height=block_height,
                                payer=request_data.payment.payer,
                                request_id=request_id,
                            )
                            logger.info(f"Recorded AICF protocol deposit: {inflow_id}")
                        except Exception as e:
                            logger.error(f"Failed to record AICF deposit in protocol: {e}")

This modular approach allows the ENA service to optionally integrate with the
AICF protocol without requiring code changes. The integration is controlled via
environment variables and gracefully degrades if the protocol module is unavailable.
"""
