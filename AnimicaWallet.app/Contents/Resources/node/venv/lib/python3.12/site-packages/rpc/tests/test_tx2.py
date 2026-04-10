"""
Tests for rpc.methods.tx2 - New Transaction RPC Methods
========================================================

Comprehensive tests for mempool2-based RPC methods.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from coretx import TxAuth, TxBody, TxEnvelope, TxId, TxKind
from coretx.canonical import (
    PREHASH_SHA3_512,
    compute_txid,
    encode_tx_envelope,
)
from coretx.signing import sign_tx

# Import RPC methods
from rpc.methods.tx2 import (
    send_raw_transaction_v2,
    get_transaction_v2,
    get_transaction_status_v2,
    get_mempool_stats_v2,
)
from rpc import errors as rpc_errors
from rpc.mempool2_service import Mempool2Service

# Try to import PQ for signing
try:
    from pq.py import keygen, SPHINCS_SHAKE_128F_SIMPLE
    _HAVE_PQ = True
except ImportError:
    _HAVE_PQ = False


@pytest.fixture
def temp_mempool_db(tmp_path: Path) -> Path:
    """Create temporary mempool database"""
    db_path = tmp_path / "test_mempool2.db"
    return db_path


@pytest.fixture
def mempool_service(temp_mempool_db: Path) -> Mempool2Service:
    """Create mempool2 service for testing"""
    service = Mempool2Service(
        storage_path=temp_mempool_db,
        chain_id=1337,  # devnet
        max_tx_bytes=128 * 1024,
        min_fee_rate=1,
    )
    yield service
    service.close()


@pytest.fixture
def mock_keypair():
    """Generate mock keypair for testing"""
    if not _HAVE_PQ:
        pytest.skip("PQ cryptography not available")
    
    # Generate SPHINCS+ keypair
    pk, sk = keygen(SPHINCS_SHAKE_128F_SIMPLE)
    return pk, sk


@pytest.fixture
def sample_tx_body() -> TxBody:
    """Create a sample transaction body"""
    return TxBody(
        version=1,
        chain_id=1337,
        nonce=0,
        from_addr=b"\x01" * 32,
        to_addr=b"\x02" * 32,
        value=1000,
        fee=100,
        gas_limit=21000,
        data=b"",
        memo="test transaction",
        timestamp=1234567890,
        kind=TxKind.TRANSFER,
    )


@pytest.fixture
def signed_tx_envelope(sample_tx_body: TxBody, mock_keypair) -> TxEnvelope:
    """Create a signed transaction envelope"""
    pk, sk = mock_keypair
    
    # Sign transaction
    envelope = sign_tx(
        body=sample_tx_body,
        secret_key=sk,
        scheme_id=SPHINCS_SHAKE_128F_SIMPLE,
        prehash_id=PREHASH_SHA3_512,
    )
    
    return envelope


@pytest.fixture
def raw_tx_hex(signed_tx_envelope: TxEnvelope) -> str:
    """Encode transaction envelope as hex"""
    tx_bytes = encode_tx_envelope(signed_tx_envelope)
    return "0x" + tx_bytes.hex()


@pytest.fixture(autouse=True)
def setup_mempool_env(temp_mempool_db: Path, monkeypatch):
    """Configure environment for mempool2 service"""
    monkeypatch.setenv("ANIMICA_MEMPOOL2_DB_PATH", str(temp_mempool_db))
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1337")
    monkeypatch.setenv("ANIMICA_DEBUG_TX", "1")
    
    # Reset singleton
    import rpc.mempool2_service
    rpc.mempool2_service._mempool2_service = None


# ────────────────────────────────────────────────────────────────────────────
# tx2.sendRawTransaction tests
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAVE_PQ, reason="PQ cryptography required")
async def test_send_raw_transaction_success(raw_tx_hex: str):
    """Test successful transaction submission"""
    result = await send_raw_transaction_v2(raw_tx_hex)
    
    assert "txid" in result
    assert result["admitted"] is True
    assert result["txid"].startswith("0x")
    assert len(result["txid"]) == 66  # 0x + 64 hex chars


@pytest.mark.asyncio
async def test_send_raw_transaction_invalid_hex():
    """Test invalid hex encoding"""
    with pytest.raises(rpc_errors.InvalidParams) as exc_info:
        await send_raw_transaction_v2("not_valid_hex")
    
    assert "Invalid hex encoding" in str(exc_info.value.message)


@pytest.mark.asyncio
async def test_send_raw_transaction_invalid_cbor():
    """Test invalid CBOR encoding"""
    # Valid hex but invalid CBOR
    invalid_cbor = "0x" + "ff" * 100
    
    with pytest.raises(rpc_errors.InvalidParams) as exc_info:
        await send_raw_transaction_v2(invalid_cbor)
    
    assert "Invalid CBOR encoding" in str(exc_info.value.message)


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAVE_PQ, reason="PQ cryptography required")
async def test_send_raw_transaction_chain_id_mismatch(mock_keypair):
    """Test chain ID mismatch rejection"""
    pk, sk = mock_keypair
    
    # Create transaction with wrong chain_id
    body = TxBody(
        version=1,
        chain_id=9999,  # Wrong chain ID
        nonce=0,
        from_addr=b"\x01" * 32,
        to_addr=b"\x02" * 32,
        value=1000,
        fee=100,
        gas_limit=21000,
        data=b"",
        memo="wrong chain",
        timestamp=1234567890,
        kind=TxKind.TRANSFER,
    )
    
    envelope = sign_tx(body, sk, SPHINCS_SHAKE_128F_SIMPLE, PREHASH_SHA3_512)
    tx_bytes = encode_tx_envelope(envelope)
    raw_tx = "0x" + tx_bytes.hex()
    
    with pytest.raises(rpc_errors.RpcError) as exc_info:
        await send_raw_transaction_v2(raw_tx)
    
    # Should be chain ID mismatch error
    error = exc_info.value
    assert error.code == rpc_errors.AnimicaCode.CHAIN_ID_MISMATCH or \
           error.code == rpc_errors.AnimicaCode.INVALID_TX
    assert error.data is not None
    assert "reason" in error.data


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAVE_PQ, reason="PQ cryptography required")
async def test_send_raw_transaction_duplicate(raw_tx_hex: str):
    """Test duplicate transaction rejection"""
    # Submit once
    result1 = await send_raw_transaction_v2(raw_tx_hex)
    assert result1["admitted"] is True
    
    # Submit again - should be rejected as duplicate
    with pytest.raises(rpc_errors.RpcError) as exc_info:
        await send_raw_transaction_v2(raw_tx_hex)
    
    error = exc_info.value
    # Could be DUPLICATE_TX or INVALID_TX depending on admission logic
    assert error.code in [
        rpc_errors.AnimicaCode.DUPLICATE_TX,
        rpc_errors.AnimicaCode.INVALID_TX,
    ]


# ────────────────────────────────────────────────────────────────────────────
# tx2.getTransaction tests
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAVE_PQ, reason="PQ cryptography required")
async def test_get_transaction_from_mempool(raw_tx_hex: str):
    """Test retrieving transaction from mempool"""
    # Submit transaction
    submit_result = await send_raw_transaction_v2(raw_tx_hex)
    txid = submit_result["txid"]
    
    # Retrieve it
    result = await get_transaction_v2(txid)
    
    assert result is not None
    assert result["txid"] == txid
    assert result["status"] == "pending"
    assert "body" in result
    assert "auth" in result
    assert "arrival_time" in result
    assert "fee_rate" in result


@pytest.mark.asyncio
async def test_get_transaction_not_found():
    """Test retrieving non-existent transaction"""
    # Random txid
    txid = "0x" + "00" * 32
    
    result = await get_transaction_v2(txid)
    assert result is None


@pytest.mark.asyncio
async def test_get_transaction_invalid_hash():
    """Test invalid transaction hash"""
    with pytest.raises(rpc_errors.InvalidParams) as exc_info:
        await get_transaction_v2("not_valid_hash")
    
    assert "Invalid tx_hash" in str(exc_info.value.message)


@pytest.mark.asyncio
async def test_get_transaction_wrong_length():
    """Test transaction hash with wrong length"""
    # Valid hex but wrong length (16 bytes instead of 32)
    short_hash = "0x" + "ff" * 16
    
    with pytest.raises(rpc_errors.InvalidParams) as exc_info:
        await get_transaction_v2(short_hash)
    
    assert "Invalid tx_hash" in str(exc_info.value.message)


# ────────────────────────────────────────────────────────────────────────────
# tx2.getTransactionStatus tests
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAVE_PQ, reason="PQ cryptography required")
async def test_get_transaction_status_pending(raw_tx_hex: str):
    """Test status for pending transaction"""
    # Submit transaction
    submit_result = await send_raw_transaction_v2(raw_tx_hex)
    txid = submit_result["txid"]
    
    # Check status
    result = await get_transaction_status_v2(txid)
    
    assert result["txid"] == txid
    assert result["status"] == "pending"
    assert result["in_mempool"] is True


@pytest.mark.asyncio
async def test_get_transaction_status_unknown():
    """Test status for unknown transaction"""
    # Random txid
    txid = "0x" + "00" * 32
    
    result = await get_transaction_status_v2(txid)
    
    assert result["txid"] == txid
    assert result["status"] == "unknown"
    assert result["in_mempool"] is False


@pytest.mark.asyncio
async def test_get_transaction_status_invalid_hash():
    """Test invalid transaction hash"""
    with pytest.raises(rpc_errors.InvalidParams) as exc_info:
        await get_transaction_status_v2("invalid_hash")
    
    assert "Invalid tx_hash" in str(exc_info.value.message)


# ────────────────────────────────────────────────────────────────────────────
# tx2.getMempoolStats tests
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_mempool_stats_empty():
    """Test stats for empty mempool"""
    result = await get_mempool_stats_v2()
    
    assert "tx_count" in result
    assert result["tx_count"] == 0
    assert "total_bytes" in result
    assert result["total_bytes"] == 0
    assert "unique_senders" in result
    assert result["unique_senders"] == 0
    assert "fee_stats" in result


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAVE_PQ, reason="PQ cryptography required")
async def test_get_mempool_stats_with_transactions(raw_tx_hex: str):
    """Test stats after adding transactions"""
    # Submit transaction
    await send_raw_transaction_v2(raw_tx_hex)
    
    # Check stats
    result = await get_mempool_stats_v2()
    
    assert result["tx_count"] == 1
    assert result["total_bytes"] > 0
    assert result["unique_senders"] == 1
    
    fee_stats = result["fee_stats"]
    assert "min" in fee_stats
    assert "max" in fee_stats
    assert "median" in fee_stats
    assert "mean" in fee_stats


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAVE_PQ, reason="PQ cryptography required")
async def test_get_mempool_stats_multiple_transactions(mock_keypair):
    """Test stats with multiple transactions"""
    pk, sk = mock_keypair
    
    # Submit multiple transactions with different nonces
    for nonce in range(3):
        body = TxBody(
            version=1,
            chain_id=1337,
            nonce=nonce,
            from_addr=b"\x01" * 32,
            to_addr=b"\x02" * 32,
            value=1000,
            fee=100 * (nonce + 1),  # Different fees
            gas_limit=21000,
            data=b"",
            memo=f"test tx {nonce}",
            timestamp=1234567890 + nonce,
            kind=TxKind.TRANSFER,
        )
        
        envelope = sign_tx(body, sk, SPHINCS_SHAKE_128F_SIMPLE, PREHASH_SHA3_512)
        tx_bytes = encode_tx_envelope(envelope)
        raw_tx = "0x" + tx_bytes.hex()
        
        await send_raw_transaction_v2(raw_tx)
    
    # Check stats
    result = await get_mempool_stats_v2()
    
    assert result["tx_count"] == 3
    assert result["unique_senders"] == 1  # All from same sender
    
    fee_stats = result["fee_stats"]
    assert fee_stats["min"] > 0
    assert fee_stats["max"] >= fee_stats["min"]


# ────────────────────────────────────────────────────────────────────────────
# Integration tests
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(not _HAVE_PQ, reason="PQ cryptography required")
async def test_full_transaction_lifecycle(raw_tx_hex: str):
    """Test complete transaction lifecycle"""
    # 1. Submit transaction
    submit_result = await send_raw_transaction_v2(raw_tx_hex)
    txid = submit_result["txid"]
    assert submit_result["admitted"] is True
    
    # 2. Check status - should be pending
    status = await get_transaction_status_v2(txid)
    assert status["status"] == "pending"
    assert status["in_mempool"] is True
    
    # 3. Retrieve transaction details
    tx = await get_transaction_v2(txid)
    assert tx is not None
    assert tx["txid"] == txid
    assert tx["status"] == "pending"
    
    # 4. Check mempool stats
    stats = await get_mempool_stats_v2()
    assert stats["tx_count"] >= 1


@pytest.mark.asyncio
async def test_error_handling_preserves_structure():
    """Test that errors maintain stable JSON-RPC structure"""
    with pytest.raises(rpc_errors.RpcError) as exc_info:
        await send_raw_transaction_v2("invalid_hex_string")
    
    error = exc_info.value
    
    # Verify error structure
    assert hasattr(error, "code")
    assert hasattr(error, "message")
    assert hasattr(error, "data")
    assert isinstance(error.code, int)
    assert isinstance(error.message, str)
    
    # Verify it can be serialized
    error_dict = error.to_dict()
    assert "code" in error_dict
    assert "message" in error_dict


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
