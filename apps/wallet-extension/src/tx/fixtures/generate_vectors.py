#!/usr/bin/env python3
"""
Generate golden test vectors for wallet extension transaction signing.

This script creates canonical test vectors that the wallet extension
must match byte-for-byte to ensure compatibility with the node.
"""

import json
import sys
from pathlib import Path

# Add paths for imports
repo_root = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(repo_root / "python"))
sys.path.insert(0, str(repo_root))

from animica.tx.signing import (
    tx_signing_preimage,
    ChainContext,
)
from coretx.canonical import (
    DOMAIN_TX_SIGN,
    PREHASH_SHA3_512,
    encode_tx_body,
    compute_sign_hash,
    compute_txid,
    encode_tx_envelope,
)
from coretx.types import TxBody, TxAuth, TxEnvelope, TxKind
from coretx.crypto import SCHEME_DILITHIUM3, SCHEME_SPHINCS_SHAKE_128S

def create_test_body():
    """Create a simple test transaction body"""
    return TxBody(
        version=1,
        chain_id=1337,
        nonce=0,
        from_addr=b"\x01" * 32,  # Mock address
        to_addr=b"\x02" * 32,    # Mock address
        value=1000,
        fee=1000000000,
        gas_limit=21000,
        data=b"",
        memo="",
        timestamp=1700000000,
        kind=TxKind.transfer,
    )

def create_test_context():
    """Create test chain context"""
    return ChainContext(
        chain_id=1337,
        genesis_hash=b"\xaa" * 32,
        network="testnet",
        fork_id=None,
        domain=DOMAIN_TX_SIGN,
        prehash="sha3-512",
    )

def bytes_to_hex(b: bytes) -> str:
    """Convert bytes to 0x-prefixed hex string"""
    return "0x" + b.hex()

def generate_dilithium3_vector():
    """Generate test vector for Dilithium3 transaction"""
    body = create_test_body()
    context = create_test_context()
    
    # Create mock keys (NOT real dilithium3 keys, just correct sizes)
    public_key = b"\x03" * 1952   # Dilithium3 pubkey size
    signature = b"\x04" * 3293    # Dilithium3 signature size
    
    # Compute signing preimage
    preimage = tx_signing_preimage(
        body,
        chain_id=context.chain_id,
        genesis=context.genesis_hash,
        network=context.network,
        domain=DOMAIN_TX_SIGN,
        message_type="tx",
    )
    
    # Compute sign hash
    sign_hash = compute_sign_hash(body, prehash_id=PREHASH_SHA3_512)
    
    # Build auth
    auth = TxAuth(
        scheme_id=SCHEME_DILITHIUM3,
        pubkey_bytes=public_key,
        signature_bytes=signature,
        prehash_id=PREHASH_SHA3_512,
    )
    
    # Build envelope
    envelope = TxEnvelope(
        body=body,
        auth=auth,
        txid=compute_txid(TxEnvelope(body=body, auth=auth, txid=b"\x00" * 32)),
    )
    
    # Encode envelope for RPC
    raw_tx = encode_tx_envelope(envelope)
    
    return {
        "name": "dilithium3_simple_transfer",
        "description": "Simple transfer with Dilithium3 signature",
        "scheme_id": SCHEME_DILITHIUM3,
        "scheme_name": "dilithium3",
        "body": {
            "version": body.version,
            "chain_id": body.chain_id,
            "nonce": body.nonce,
            "from_addr": bytes_to_hex(body.from_addr),
            "to_addr": bytes_to_hex(body.to_addr),
            "value": body.value,
            "fee": body.fee,
            "gas_limit": body.gas_limit,
            "data": bytes_to_hex(body.data),
            "memo": body.memo,
            "timestamp": body.timestamp,
            "kind": int(body.kind),
        },
        "context": {
            "chain_id": context.chain_id,
            "genesis_hash": bytes_to_hex(context.genesis_hash),
            "network": context.network,
            "fork_id": context.fork_id,
            "domain": context.domain,
            "prehash": context.prehash,
        },
        "auth": {
            "scheme_id": auth.scheme_id,
            "pubkey_bytes": bytes_to_hex(auth.pubkey_bytes),
            "signature_bytes": bytes_to_hex(auth.signature_bytes),
            "prehash_id": auth.prehash_id,
        },
        "canonical": {
            "body_cbor": bytes_to_hex(encode_tx_body(body)),
            "preimage": bytes_to_hex(preimage),
            "sign_hash": bytes_to_hex(sign_hash),
            "txid": bytes_to_hex(envelope.txid.bytes32),
            "raw_tx": bytes_to_hex(raw_tx),
        },
    }

def generate_sphincs_vector():
    """Generate test vector for SPHINCS+ transaction"""
    body = create_test_body()
    body.nonce = 1  # Different nonce for variety
    context = create_test_context()
    
    # Create mock keys (NOT real SPHINCS+ keys, just correct sizes)
    public_key = b"\x05" * 32      # SPHINCS+ pubkey size
    signature = b"\x06" * 7856     # SPHINCS+ signature size
    
    # Compute signing preimage
    preimage = tx_signing_preimage(
        body,
        chain_id=context.chain_id,
        genesis=context.genesis_hash,
        network=context.network,
        domain=DOMAIN_TX_SIGN,
        message_type="tx",
    )
    
    # Compute sign hash
    sign_hash = compute_sign_hash(body, prehash_id=PREHASH_SHA3_512)
    
    # Build auth
    auth = TxAuth(
        scheme_id=SCHEME_SPHINCS_SHAKE_128S,
        pubkey_bytes=public_key,
        signature_bytes=signature,
        prehash_id=PREHASH_SHA3_512,
    )
    
    # Build envelope
    envelope = TxEnvelope(
        body=body,
        auth=auth,
        txid=compute_txid(TxEnvelope(body=body, auth=auth, txid=b"\x00" * 32)),
    )
    
    # Encode envelope for RPC
    raw_tx = encode_tx_envelope(envelope)
    
    return {
        "name": "sphincs_simple_transfer",
        "description": "Simple transfer with SPHINCS+ signature",
        "scheme_id": SCHEME_SPHINCS_SHAKE_128S,
        "scheme_name": "sphincs_shake_128s",
        "body": {
            "version": body.version,
            "chain_id": body.chain_id,
            "nonce": body.nonce,
            "from_addr": bytes_to_hex(body.from_addr),
            "to_addr": bytes_to_hex(body.to_addr),
            "value": body.value,
            "fee": body.fee,
            "gas_limit": body.gas_limit,
            "data": bytes_to_hex(body.data),
            "memo": body.memo,
            "timestamp": body.timestamp,
            "kind": int(body.kind),
        },
        "context": {
            "chain_id": context.chain_id,
            "genesis_hash": bytes_to_hex(context.genesis_hash),
            "network": context.network,
            "fork_id": context.fork_id,
            "domain": context.domain,
            "prehash": context.prehash,
        },
        "auth": {
            "scheme_id": auth.scheme_id,
            "pubkey_bytes": bytes_to_hex(auth.pubkey_bytes),
            "signature_bytes": bytes_to_hex(auth.signature_bytes),
            "prehash_id": auth.prehash_id,
        },
        "canonical": {
            "body_cbor": bytes_to_hex(encode_tx_body(body)),
            "preimage": bytes_to_hex(preimage),
            "sign_hash": bytes_to_hex(sign_hash),
            "txid": bytes_to_hex(envelope.txid.bytes32),
            "raw_tx": bytes_to_hex(raw_tx),
        },
    }

def main():
    """Generate and save test vectors"""
    vectors = {
        "version": "1.0",
        "description": "Golden test vectors for wallet extension transaction signing",
        "vectors": [
            generate_dilithium3_vector(),
            generate_sphincs_vector(),
        ],
    }
    
    output_path = Path(__file__).parent / "golden_vectors.json"
    with open(output_path, "w") as f:
        json.dump(vectors, f, indent=2)
    
    print(f"Generated test vectors: {output_path}")
    print(f"  - Dilithium3 vector")
    print(f"  - SPHINCS+ vector")

if __name__ == "__main__":
    main()
