"""
Test RPC tx.sendRawTransaction with PQ signatures.

This test ensures that the node correctly verifies PQ signatures
sent via tx.sendRawTransaction RPC method.
"""

import pytest


pytestmark = pytest.mark.anyio

# Constants for PQ algorithms
ALG_SPHINCS_SHAKE_128S = "sphincs_shake_128s"
ALG_SPHINCS_ID = 4098


def _create_signed_tx():
    """Create a properly signed transaction envelope for testing."""
    try:
        from omni_sdk.wallet.signer import PQSigner
        from omni_sdk.tx.build import transfer
        from omni_sdk.tx.encode import sign_bytes, pack_signed
    except ImportError:
        pytest.skip("SDK not available")
    
    # Create a deterministic signer
    seed = bytes(range(32))
    signer = PQSigner.from_seed("dilithium3", seed=seed)
    
    # Build transaction
    chain_id = 1
    tx = transfer(
        from_addr=signer.address or "anim1test",
        to_addr="anim1dest",
        amount=1000,
        nonce=5,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=chain_id,
    )
    
    # Sign
    msg = sign_bytes(tx)
    sig_bytes = signer.sign_tx(msg, chain_id)
    
    # Pack into signed envelope
    raw_tx = pack_signed(
        tx,
        signature=sig_bytes,
        alg_id=signer.alg_id,
        public_key=signer.public_key,
    )
    
    return raw_tx, signer


async def test_sendRawTransaction_accepts_valid_pq_signature(monkeypatch):
    """Test that tx.sendRawTransaction accepts validly signed transactions."""
    # Create signed tx
    raw_tx, signer = _create_signed_tx()
    
    # Mock the pending pool and chain_id
    from rpc.methods import tx as tx_methods
    
    # Mock dependencies
    class MockDeps:
        def get_chain_params(self):
            class ChainParams:
                chain_id = 1
            return ChainParams()
    
    monkeypatch.setattr(tx_methods, "deps", MockDeps())
    
    # Mock pending pool
    pending_store = {}
    
    def mock_pending_put(tx_hash_hex, raw):
        pending_store[tx_hash_hex] = raw
    
    monkeypatch.setattr(tx_methods, "_pending_put", mock_pending_put)
    
    # Mock lookup
    def mock_lookup(tx_hash_hex):
        return None, None, None, None
    
    monkeypatch.setattr(tx_methods, "_lookup_persisted_tx", mock_lookup)
    
    # Call sendRawTransaction
    from rpc.methods.tx import tx_send_raw_transaction
    
    raw_hex = "0x" + raw_tx.hex()
    tx_hash = tx_send_raw_transaction(raw_hex)
    
    # Should return a valid tx hash
    assert isinstance(tx_hash, str)
    assert tx_hash.startswith("0x")
    assert len(tx_hash) == 66  # 0x + 64 hex chars


async def test_sendRawTransaction_rejects_tampered_signature(monkeypatch):
    """Test that tx.sendRawTransaction rejects tampered signatures."""
    # Create signed tx
    raw_tx, signer = _create_signed_tx()
    
    # Tamper with the signature in the envelope (flip byte in the middle)
    from omni_sdk.utils.cbor import loads as cbor_loads, dumps as cbor_dumps
    
    envelope = cbor_loads(raw_tx)
    
    # Flip a byte in the middle of the signature
    sig_bytes = envelope["sig"]["sig"]
    tampered_sig = bytearray(sig_bytes)
    tamper_index = len(tampered_sig) // 2
    tampered_sig[tamper_index] ^= 0xFF
    envelope["sig"]["sig"] = bytes(tampered_sig)
    
    tampered_raw = cbor_dumps(envelope)
    
    # Mock dependencies
    from rpc.methods import tx as tx_methods
    
    class MockDeps:
        def get_chain_params(self):
            class ChainParams:
                chain_id = 1
            return ChainParams()
    
    monkeypatch.setattr(tx_methods, "deps", MockDeps())
    
    # Call sendRawTransaction - should raise BadSignature
    from rpc.methods.tx import tx_send_raw_transaction
    from rpc.errors import BadSignature
    
    raw_hex = "0x" + tampered_raw.hex()
    
    with pytest.raises(BadSignature, match="Invalid post-quantum signature"):
        tx_send_raw_transaction(raw_hex)


async def test_sendRawTransaction_rejects_wrong_chain_id(monkeypatch):
    """Test that tx.sendRawTransaction rejects transactions with wrong chain_id."""
    # Create signed tx for chain_id=1
    try:
        from omni_sdk.wallet.signer import PQSigner
        from omni_sdk.tx.build import transfer
        from omni_sdk.tx.encode import sign_bytes, pack_signed
    except ImportError:
        pytest.skip("SDK not available")
    
    seed = bytes(range(32))
    signer = PQSigner.from_seed("dilithium3", seed=seed)
    
    # Build transaction for chain_id=999 (wrong)
    chain_id = 999
    tx = transfer(
        from_addr=signer.address or "anim1test",
        to_addr="anim1dest",
        amount=1000,
        nonce=5,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=chain_id,
    )
    
    # Sign with chain_id=999
    msg = sign_bytes(tx)
    sig_bytes = signer.sign_tx(msg, chain_id)
    
    raw_tx = pack_signed(
        tx,
        signature=sig_bytes,
        alg_id=signer.alg_id,
        public_key=signer.public_key,
    )
    
    # Mock dependencies - node expects chain_id=1
    from rpc.methods import tx as tx_methods
    
    class MockDeps:
        def get_chain_params(self):
            class ChainParams:
                chain_id = 1  # Node expects chain_id=1
            return ChainParams()
    
    monkeypatch.setattr(tx_methods, "deps", MockDeps())
    
    # Call sendRawTransaction - should raise ChainIdMismatch
    from rpc.methods.tx import tx_send_raw_transaction
    from rpc.errors import ChainIdMismatch
    
    raw_hex = "0x" + raw_tx.hex()
    
    with pytest.raises(ChainIdMismatch):
        tx_send_raw_transaction(raw_hex)


async def test_sendRawTransaction_requires_sig_field(monkeypatch):
    """Test that tx.sendRawTransaction requires sig field in envelope."""
    from omni_sdk.tx.build import transfer
    from omni_sdk.utils.cbor import dumps as cbor_dumps
    
    # Build transaction
    tx = transfer(
        from_addr="anim1test",
        to_addr="anim1dest",
        amount=1000,
        nonce=5,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=1,
    )
    
    # Create envelope WITHOUT signature
    from omni_sdk.tx.encode import canonical_body_dict
    
    envelope = {
        "body": canonical_body_dict(tx),
        # Missing "sig" field
    }
    
    raw_tx = cbor_dumps(envelope)
    
    # Mock dependencies
    from rpc.methods import tx as tx_methods
    
    class MockDeps:
        def get_chain_params(self):
            class ChainParams:
                chain_id = 1
            return ChainParams()
    
    monkeypatch.setattr(tx_methods, "deps", MockDeps())
    
    # Call sendRawTransaction - should raise InvalidParams
    from rpc.methods.tx import tx_send_raw_transaction
    from rpc.errors import InvalidParams
    
    raw_hex = "0x" + raw_tx.hex()
    
    with pytest.raises(InvalidParams, match="Missing 'sig'"):
        tx_send_raw_transaction(raw_hex)


def _create_signed_tx_sphincs():
    """Create a properly signed transaction envelope using SPHINCS+ for testing."""
    try:
        from omni_sdk.wallet.signer import PQSigner
        from omni_sdk.tx.build import transfer
        from omni_sdk.tx.encode import sign_bytes, pack_signed
    except ImportError:
        pytest.skip("SDK not available")
    
    # Create a deterministic signer with SPHINCS+ (sphincs_shake_128s)
    seed = bytes(range(32))
    signer = PQSigner.from_seed(ALG_SPHINCS_SHAKE_128S, seed=seed)
    
    # Build transaction
    chain_id = 1
    tx = transfer(
        from_addr=signer.address or "anim1test",
        to_addr="anim1dest",
        amount=1000,
        nonce=5,
        gas_limit=21000,
        max_fee=1000000000,
        chain_id=chain_id,
    )
    
    # Sign
    msg = sign_bytes(tx)
    sig_bytes = signer.sign_tx(msg, chain_id)
    
    # Pack into signed envelope
    raw_tx = pack_signed(
        tx,
        signature=sig_bytes,
        alg_id=signer.alg_id,
        public_key=signer.public_key,
    )
    
    return raw_tx, signer


async def test_sendRawTransaction_accepts_valid_sphincs_signature(monkeypatch):
    """Test that tx.sendRawTransaction accepts validly signed SPHINCS+ transactions."""
    # Create signed tx with SPHINCS+
    raw_tx, signer = _create_signed_tx_sphincs()
    
    # Verify signer is using SPHINCS+
    assert signer.alg_name == ALG_SPHINCS_SHAKE_128S
    assert signer.alg_id == ALG_SPHINCS_ID  # Expected SPHINCS+ algorithm ID
    
    # Mock the pending pool and chain_id
    from rpc.methods import tx as tx_methods
    
    # Mock dependencies
    class MockDeps:
        def get_chain_params(self):
            class ChainParams:
                chain_id = 1
            return ChainParams()
    
    monkeypatch.setattr(tx_methods, "deps", MockDeps())
    
    # Mock pending pool
    pending_store = {}
    
    def mock_pending_put(tx_hash_hex, raw):
        pending_store[tx_hash_hex] = raw
    
    monkeypatch.setattr(tx_methods, "_pending_put", mock_pending_put)
    
    # Mock lookup
    def mock_lookup(tx_hash_hex):
        return None, None, None, None
    
    monkeypatch.setattr(tx_methods, "_lookup_persisted_tx", mock_lookup)
    
    # Call sendRawTransaction
    from rpc.methods.tx import tx_send_raw_transaction
    
    raw_hex = "0x" + raw_tx.hex()
    tx_hash = tx_send_raw_transaction(raw_hex)
    
    # Should return a valid tx hash
    assert isinstance(tx_hash, str)
    assert tx_hash.startswith("0x")
    assert len(tx_hash) == 66  # 0x + 64 hex chars
