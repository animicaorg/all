"""Known Answer Tests (KATs) for PQ algorithms."""

import json
import pathlib
import pytest


def load_kat(filename):
    """Load KAT file from assets directory."""
    assets_dir = pathlib.Path(__file__).parent / "assets"
    kat_file = assets_dir / filename
    
    if not kat_file.exists():
        pytest.skip(f"KAT file not found: {filename}")
    
    with open(kat_file) as f:
        return json.load(f)


def test_kem_kat_determinism():
    """Test ML-KEM-768 produces consistent output for same seed."""
    from animica.pq import kem_keygen, kem_encaps, kem_decaps
    
    kat = load_kat("kem_kat_simple.json")
    
    for vector in kat["test_vectors"]:
        seed = bytes.fromhex(vector["seed"])
        
        # Generate keypair twice with same seed
        ek1, dk1 = kem_keygen(seed)
        ek2, dk2 = kem_keygen(seed)
        
        # Should be identical
        assert ek1 == ek2, f"KAT {vector['count']}: Encapsulation keys differ"
        assert dk1 == dk2, f"KAT {vector['count']}: Decapsulation keys differ"
        
        # Encapsulate with fixed seed
        encaps_seed = b"\x42" * 32
        ss1, ct1 = kem_encaps(ek1, encaps_seed)
        ss2, ct2 = kem_encaps(ek2, encaps_seed)
        
        # Should be identical
        assert ss1 == ss2, f"KAT {vector['count']}: Shared secrets differ"
        assert ct1 == ct2, f"KAT {vector['count']}: Ciphertexts differ"
        
        # Decapsulation should work
        ss_dec = kem_decaps(dk1, ct1)
        assert ss_dec == ss1, f"KAT {vector['count']}: Decapsulation failed"


def test_sig_kat_determinism():
    """Test ML-DSA-65 produces consistent output for same seed."""
    from animica.pq import sig_keygen, sig_sign, sig_verify
    
    kat = load_kat("sig_kat_simple.json")
    
    for vector in kat["test_vectors"]:
        seed = bytes.fromhex(vector["seed"])
        message = bytes.fromhex(vector["message"])
        
        # Generate keypair twice with same seed
        pk1, sk1 = sig_keygen(seed)
        pk2, sk2 = sig_keygen(seed)
        
        # Should be identical
        assert pk1 == pk2, f"KAT {vector['count']}: Public keys differ"
        assert sk1 == sk2, f"KAT {vector['count']}: Secret keys differ"
        
        # Sign twice
        sig1 = sig_sign(sk1, message)
        sig2 = sig_sign(sk2, message)
        
        # Should be identical (deterministic/hedged signing)
        assert sig1 == sig2, f"KAT {vector['count']}: Signatures differ"
        
        # Verification should work
        assert sig_verify(pk1, message, sig1), f"KAT {vector['count']}: Verification failed"


def test_kem_kat_sizes():
    """Test that KAT vectors produce correct sizes."""
    from animica.pq import kem_keygen, kem_encaps
    
    kat = load_kat("kem_kat_simple.json")
    
    for vector in kat["test_vectors"]:
        seed = bytes.fromhex(vector["seed"])
        
        ek, dk = kem_keygen(seed)
        assert len(ek) == 1184, f"KAT {vector['count']}: Wrong EK size"
        assert len(dk) == 2400, f"KAT {vector['count']}: Wrong DK size"
        
        encaps_seed = b"\x00" * 32
        ss, ct = kem_encaps(ek, encaps_seed)
        assert len(ss) == 32, f"KAT {vector['count']}: Wrong SS size"
        assert len(ct) == 1088, f"KAT {vector['count']}: Wrong CT size"


def test_sig_kat_sizes():
    """Test that KAT vectors produce correct sizes."""
    from animica.pq import sig_keygen, sig_sign
    
    kat = load_kat("sig_kat_simple.json")
    
    for vector in kat["test_vectors"]:
        seed = bytes.fromhex(vector["seed"])
        message = bytes.fromhex(vector["message"])
        
        pk, sk = sig_keygen(seed)
        assert len(pk) == 1952, f"KAT {vector['count']}: Wrong PK size"
        assert len(sk) == 4000, f"KAT {vector['count']}: Wrong SK size"
        
        sig = sig_sign(sk, message)
        assert len(sig) == 3293, f"KAT {vector['count']}: Wrong signature size"


def test_kem_cross_vector_uniqueness():
    """Test that different seeds produce different keys."""
    from animica.pq import kem_keygen
    
    kat = load_kat("kem_kat_simple.json")
    
    if len(kat["test_vectors"]) < 2:
        pytest.skip("Need at least 2 vectors for uniqueness test")
    
    keys = []
    for vector in kat["test_vectors"]:
        seed = bytes.fromhex(vector["seed"])
        ek, dk = kem_keygen(seed)
        keys.append((ek, dk))
    
    # All keys should be unique
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert keys[i][0] != keys[j][0], "Encapsulation keys should differ"
            assert keys[i][1] != keys[j][1], "Decapsulation keys should differ"


def test_sig_cross_vector_uniqueness():
    """Test that different seeds produce different keys."""
    from animica.pq import sig_keygen
    
    kat = load_kat("sig_kat_simple.json")
    
    if len(kat["test_vectors"]) < 2:
        pytest.skip("Need at least 2 vectors for uniqueness test")
    
    keys = []
    for vector in kat["test_vectors"]:
        seed = bytes.fromhex(vector["seed"])
        pk, sk = sig_keygen(seed)
        keys.append((pk, sk))
    
    # All keys should be unique
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert keys[i][0] != keys[j][0], "Public keys should differ"
            assert keys[i][1] != keys[j][1], "Secret keys should differ"
