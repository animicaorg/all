"""
Test RPC tx.sendRawTransaction with PQ signatures.

This test ensures that the node correctly verifies PQ signatures
sent via tx.sendRawTransaction RPC method.
"""

import types

import pytest


pytestmark = pytest.mark.anyio

# Constants for PQ algorithms
ALG_SPHINCS_SHAKE_128S = "sphincs_shake_128s"
ALG_SPHINCS_ID = 4098


@pytest.fixture(autouse=True)
def _allow_fake_pq(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests can run without liboqs by enabling the fake backend."""

    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    monkeypatch.setenv("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")


def _create_signed_tx(chain_id: int = 1, alg: str = "dilithium3"):
    """Create a properly signed transaction envelope for testing."""
    try:
        from omni_sdk.wallet.signer import PQSigner
        from omni_sdk.tx.build import transfer
        from omni_sdk.tx.encode import sign_bytes, pack_signed
    except ImportError:
        pytest.skip("SDK not available")
    
    # Create a deterministic signer
    seed = bytes(range(32))
    signer = PQSigner.from_seed(alg, seed=seed)

    # Build transaction
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


@pytest.mark.parametrize("chain_id", (1, 2))
async def test_sendRawTransaction_accepts_valid_pq_signature(monkeypatch, chain_id):
    """Test that tx.sendRawTransaction accepts validly signed transactions."""
    # Create signed tx
    raw_tx, signer = _create_signed_tx(chain_id=chain_id)
    
    # Mock the pending pool and chain_id
    from rpc.methods import tx as tx_methods
    
    # Mock dependencies
    class MockDeps:
        def get_chain_params(self):
            return types.SimpleNamespace(chain_id=chain_id)
    
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


async def test_sendRawTransaction_falls_back_to_alt_sign_bytes(monkeypatch):
    """Verification should succeed even if the primary SignBytes helper drifts."""

    raw_tx, _signer = _create_signed_tx(chain_id=1)

    from rpc.methods import tx as tx_methods

    # Force the primary helper to return incorrect bytes; alternates should recover
    monkeypatch.setattr(tx_methods, "_build_signable_tx_bytes", lambda obj: b"bad-bytes")

    class MockDeps:
        def get_chain_params(self):
            class ChainParams:
                chain_id = 1

            return ChainParams()

    monkeypatch.setattr(tx_methods, "deps", MockDeps())

    pending_store = {}

    def mock_pending_put(tx_hash_hex, raw):
        pending_store[tx_hash_hex] = raw

    monkeypatch.setattr(tx_methods, "_pending_put", mock_pending_put)

    def mock_lookup(tx_hash_hex):
        return None, None, None, None

    monkeypatch.setattr(tx_methods, "_lookup_persisted_tx", mock_lookup)

    from rpc.methods.tx import tx_send_raw_transaction

    raw_hex = "0x" + raw_tx.hex()

    # Should still verify thanks to alternate SignBytes sources
    tx_hash = tx_send_raw_transaction(raw_hex)

    assert isinstance(tx_hash, str) and tx_hash.startswith("0x")
    assert pending_store  # ensure we attempted to persist


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


def test_verify_uses_envelope_body_for_sign_bytes(monkeypatch):
    """Ensure verification canonicalizes the same body the CLI signed."""

    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")

    try:
        from omni_sdk.wallet.signer import PQSigner
        from omni_sdk.tx.build import transfer
        from omni_sdk.tx.encode import pack_signed, sign_bytes, unpack_signed
        from rpc.methods import tx as rpc_tx
    except ImportError:
        pytest.skip("SDK not available")

    # Build and sign a transfer using the SDK (canonical body only)
    signer = PQSigner.from_seed("sphincs_shake_128s", seed=bytes(range(32)))
    tx = transfer(
        from_addr=signer.address or "anim1from",
        to_addr="anim1dest",
        amount=1,
        nonce=0,
        gas_limit=21000,
        max_fee=1_000_000_000,
        chain_id=2,
    )

    msg_cli = sign_bytes(tx)
    sig = signer.sign_tx(msg_cli, chain_id=2)
    raw = pack_signed(tx, signature=sig, alg_id=signer.alg_id, public_key=signer.public_key)

    # Decoded envelope preserves the exact body the CLI signed
    envelope = unpack_signed(raw)
    tx_candidates = rpc_tx._collect_sign_bytes(tx)
    env_candidates = rpc_tx._collect_sign_bytes(envelope)

    assert tx_candidates and tx_candidates[0][1] == msg_cli
    assert any(candidate == msg_cli for _, candidate in env_candidates)

    class MockDeps:
        def get_chain_params(self):
            class CP:
                chain_id = 2

            return CP()

    monkeypatch.setattr(rpc_tx, "deps", MockDeps())

    # Verification should succeed even when tx_like is a dataclass with extras
    rpc_tx._verify_pq_signature(tx, envelope, chain_id=2)


def test_pq_verify_works_without_oqs(monkeypatch):
    """pq.py.verify should stay available even when the oqs module is missing."""

    import importlib
    import sys

    try:
        from omni_sdk.utils.cbor import loads as cbor_loads
    except ImportError:
        pytest.skip("SDK not available")

    # Simulate environment without liboqs/oqs-python
    sys.modules.pop("oqs", None)

    import pq.py.keygen as pq_keygen
    import rpc.methods.tx as tx_methods

    importlib.reload(pq_keygen)
    importlib.reload(tx_methods)

    raw_tx, _signer = _create_signed_tx(chain_id=1)
    envelope = cbor_loads(raw_tx)

    # pq verify backend should be present and able to validate the tx envelope
    assert tx_methods._pq_verify is not None
    tx_methods._verify_pq_signature(envelope, envelope, chain_id=1)

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


def _create_signed_tx_sphincs(chain_id: int = 1):
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


@pytest.mark.parametrize("chain_id", (1, 2))
async def test_sendRawTransaction_accepts_valid_sphincs_signature(monkeypatch, chain_id):
    """Test that tx.sendRawTransaction accepts validly signed SPHINCS+ transactions."""
    # Create signed tx with SPHINCS+
    raw_tx, signer = _create_signed_tx_sphincs(chain_id=chain_id)
    
    # Verify signer is using SPHINCS+
    assert signer.alg_name == ALG_SPHINCS_SHAKE_128S
    assert signer.alg_id == ALG_SPHINCS_ID  # Expected SPHINCS+ algorithm ID
    
    # Mock the pending pool and chain_id
    from rpc.methods import tx as tx_methods
    
    # Mock dependencies
    class MockDeps:
        def get_chain_params(self):
            return types.SimpleNamespace(chain_id=chain_id)
    
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
