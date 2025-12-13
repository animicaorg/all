"""
Regression tests for Dilithium3 transaction signing with both canonical and legacy keys.

Tests verify that:
1. Canonical 4000-byte keys work correctly 
2. Legacy 4032-byte keys work correctly after normalization
3. Local verification matches signing for both formats
4. Pure-Python backend handles both formats correctly
"""

import json
import os
import sys
from pathlib import Path

import cbor2
import pytest

# Ensure animica modules are importable
repo_root = Path(__file__).resolve().parents[4]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "python"))
sys.path.insert(0, str(repo_root / "pq"))

# Set environment for pure-Python testing
os.environ["ANIMICA_UNSAFE_PQ_FAKE"] = "1"
os.environ["ANIMICA_ALLOW_PQ_PURE_FALLBACK"] = "1"


def _make_test_tx_body(from_addr: str = "anim1test", to_addr: str = "anim1dest") -> dict:
    """Create a standard test transaction body."""
    return {
        "chainId": 2,
        "from": from_addr,
        "to": to_addr,
        "nonce": 0,
        "value": 1000000000,
        "gasLimit": 21000,
        "maxFee": 1,
        "data": b"",
    }


class TestDilithium3TxSigningWithNormalization:
    """Test transaction signing with canonical and legacy Dilithium3 keys."""
    
    def test_canonical_4000_byte_key_signs_and_verifies(self):
        """Canonical 4000-byte key should sign and verify correctly."""
        from animica._vendor.dilithium_py.dilithium3 import Dilithium3
        from pq.py.sign import sign_detached, verify_detached, build_sign_bytes
        
        # Generate canonical key
        seed = b"a" * 32
        sk, pk = Dilithium3.keygen(seed)
        
        assert len(sk) == 4000, f"Expected 4000-byte key, got {len(sk)}"
        assert len(pk) == 1952
        
        # Simulate transaction body
        tx_body = _make_test_tx_body()
        body_bytes = cbor2.dumps(tx_body, canonical=True)
        
        # Sign
        sig = sign_detached(
            body_bytes,
            alg="dilithium3",
            sk=sk,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
        
        # Verify
        valid = verify_detached(
            body_bytes,
            sig,
            pk,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
        
        assert valid, "Verification failed for canonical 4000-byte key"
    
    def test_legacy_4032_byte_key_normalizes_and_verifies(self):
        """Legacy 4032-byte key should normalize to 4000 and verify correctly."""
        from animica._vendor.dilithium_py.dilithium3 import Dilithium3
        from pq.py.sign import sign_detached, verify_detached
        
        # Generate canonical key and extend to simulate liboqs format
        seed = b"b" * 32
        sk_canonical, pk = Dilithium3.keygen(seed)
        sk_legacy = sk_canonical + (b"x" * 32)  # Add 32 bytes to simulate liboqs format
        
        assert len(sk_legacy) == 4032, f"Expected 4032-byte legacy key, got {len(sk_legacy)}"
        
        # Simulate transaction body
        tx_body = _make_test_tx_body()
        body_bytes = cbor2.dumps(tx_body, canonical=True)
        
        # Sign with legacy key (should auto-normalize)
        sig = sign_detached(
            body_bytes,
            alg="dilithium3",
            sk=sk_legacy,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
        
        # Verify
        valid = verify_detached(
            body_bytes,
            sig,
            pk,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
        
        assert valid, "Verification failed for legacy 4032-byte key"
    
    def test_legacy_and_canonical_produce_same_signature(self):
        """Legacy 4032-byte and canonical 4000-byte keys should produce identical signatures."""
        from animica._vendor.dilithium_py.dilithium3 import Dilithium3
        from pq.py.sign import sign_detached
        
        # Generate keys
        seed = b"c" * 32
        sk_canonical, pk = Dilithium3.keygen(seed)
        sk_legacy = sk_canonical + b"x" * 32
        
        # Simulate transaction body
        tx_body = _make_test_tx_body()
        body_bytes = cbor2.dumps(tx_body, canonical=True)
        
        # Sign with canonical key
        sig1 = sign_detached(
            body_bytes,
            alg="dilithium3",
            sk=sk_canonical,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
        
        # Sign with legacy key
        sig2 = sign_detached(
            body_bytes,
            alg="dilithium3",
            sk=sk_legacy,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
        
        # Signatures should be identical since keys are equivalent
        # sig1 and sig2 are pq.py.sign.Signature objects with .sig attribute containing raw bytes
        assert sig1.sig == sig2.sig, "Canonical and legacy keys produced different signatures"
    
    def test_tx_cli_flow_with_canonical_key(self, tmp_path: Path):
        """Test the full CLI tx send flow with a canonical 4000-byte key."""
        from animica._vendor.dilithium_py.dilithium3 import Dilithium3
        from pq.py.sign import sign_detached, verify_detached
        
        # Generate canonical key
        seed = b"d" * 32
        sk, pk = Dilithium3.keygen(seed)
        alg_id = 0x1001  # dilithium3
        
        # Create wallet entry
        wallet_file = tmp_path / "wallets.json"
        wallet_entry = {
            "label": "test",
            "address": "anim1test",
            "alg_id": alg_id,
            "alg_name": "dilithium3",
            "public_key_hex": pk.hex(),
            "secret_key_hex": sk.hex(),
            "created_at": "2024-01-01T00:00:00Z",
        }
        wallet_file.write_text(json.dumps({"version": 1, "wallets": [wallet_entry]}))
        
        # Load key back from hex (simulating CLI flow)
        sk_loaded = bytes.fromhex(wallet_entry["secret_key_hex"])
        pk_loaded = bytes.fromhex(wallet_entry["public_key_hex"])
        
        assert len(sk_loaded) == 4000
        assert len(pk_loaded) == 1952
        
        # Build transaction body
        tx_body = _make_test_tx_body()
        body_bytes = cbor2.dumps(tx_body, canonical=True)
        
        # Sign (simulating tx.py flow)
        sig = sign_detached(
            body_bytes,
            alg=alg_id,
            sk=sk_loaded,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
        
        # Local verify (this is where the issue occurs in the bug report)
        valid = verify_detached(
            body_bytes,
            sig,
            pk_loaded,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
        
        assert valid, "Local verification failed for canonical key (this is the reported bug)"
    
    def test_tx_cli_flow_with_legacy_key(self, tmp_path: Path):
        """Test the full CLI tx send flow with a legacy 4032-byte key."""
        from animica._vendor.dilithium_py.dilithium3 import Dilithium3
        from pq.py.sign import sign_detached, verify_detached
        
        # Generate canonical key and extend to legacy format
        seed = b"e" * 32
        sk_canonical, pk = Dilithium3.keygen(seed)
        sk_legacy = sk_canonical + (b"y" * 32)  # Add 32 bytes to simulate liboqs format
        alg_id = 0x1001  # dilithium3
        
        # Create wallet entry with legacy key
        wallet_file = tmp_path / "wallets.json"
        wallet_entry = {
            "label": "legacy",
            "address": "anim1legacy",
            "alg_id": alg_id,
            "alg_name": "dilithium3",
            "public_key_hex": pk.hex(),
            "secret_key_hex": sk_legacy.hex(),
            "created_at": "2024-01-01T00:00:00Z",
        }
        wallet_file.write_text(json.dumps({"version": 1, "wallets": [wallet_entry]}))
        
        # Load key back from hex (simulating CLI flow)
        sk_loaded = bytes.fromhex(wallet_entry["secret_key_hex"])
        pk_loaded = bytes.fromhex(wallet_entry["public_key_hex"])
        
        assert len(sk_loaded) == 4032
        assert len(pk_loaded) == 1952
        
        # Build transaction body
        tx_body = _make_test_tx_body(from_addr="anim1legacy")
        body_bytes = cbor2.dumps(tx_body, canonical=True)
        
        # Sign (simulating tx.py flow) - should auto-normalize
        sig = sign_detached(
            body_bytes,
            alg=alg_id,
            sk=sk_loaded,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
        
        # Local verify
        valid = verify_detached(
            body_bytes,
            sig,
            pk_loaded,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )

        assert valid, "Local verification failed for legacy 4032-byte key"

    def test_signing_can_use_provided_public_key_for_commitment(self):
        """Explicitly passing pk keeps signatures compatible with external keygens."""
        from animica._vendor.dilithium_py.dilithium3 import Dilithium3
        from pq.py.sign import sign_detached, verify_detached

        seed = b"f" * 32
        sk, pk = Dilithium3.keygen(seed)

        # Simulate a public key that doesn't match the reference derivation (e.g., liboqs)
        pk_external = bytes([pk[0] ^ 0x01]) + pk[1:]

        tx_body = _make_test_tx_body(from_addr="anim1alt")
        body_bytes = cbor2.dumps(tx_body, canonical=True)

        # Without providing the external pk, verification with that pk should fail
        sig_default = sign_detached(
            body_bytes,
            alg="dilithium3",
            sk=sk,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
        assert not verify_detached(
            body_bytes,
            sig_default,
            pk_external,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )

        # Supplying the external pk to signing aligns the commitment with verification
        sig_with_pk = sign_detached(
            body_bytes,
            alg="dilithium3",
            sk=sk,
            pk=pk_external,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
        assert verify_detached(
            body_bytes,
            sig_with_pk,
            pk_external,
            domain="tx",
            chain_id=2,
            prehash="sha3-512",
        )
