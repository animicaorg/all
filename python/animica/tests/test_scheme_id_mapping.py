"""
Test scheme_id mapping consistency for PQ algorithms.

This ensures that scheme_id values are correctly mapped to algorithms
and that expected pubkey/signature sizes match the registry.
"""

from __future__ import annotations

import pytest


def test_scheme_id_4097_is_dilithium3() -> None:
    """Verify scheme_id 4097 (0x1001) maps to dilithium3."""
    from pq.py.registry import ALG_IDS, get_sig, DILITHIUM3_ID
    
    assert ALG_IDS["dilithium3"] == 0x1001
    assert DILITHIUM3_ID == 0x1001
    assert 0x1001 == 4097
    
    info = get_sig(4097)
    assert info is not None
    assert info.name == "dilithium3"
    assert info.alg_id == 4097
    assert info.pubkey_size == 1952
    assert info.seckey_size == 4000
    assert info.signature_size == 3293


def test_scheme_id_4098_is_sphincs_shake_128s() -> None:
    """Verify scheme_id 4098 (0x1002) maps to sphincs_shake_128s."""
    from pq.py.registry import ALG_IDS, get_sig, SPHINCS_SHAKE_128S_ID
    
    assert ALG_IDS["sphincs_shake_128s"] == 0x1002
    assert SPHINCS_SHAKE_128S_ID == 0x1002
    assert 0x1002 == 4098
    
    info = get_sig(4098)
    assert info is not None
    assert info.name == "sphincs_shake_128s"
    assert info.alg_id == 4098
    assert info.pubkey_size == 64
    assert info.seckey_size == 64
    assert info.signature_size == 7856


def test_scheme_id_reverse_lookup() -> None:
    """Test that ALG_NAME provides correct reverse lookup."""
    from pq.py.registry import ALG_NAME
    
    assert ALG_NAME[4097] == "dilithium3"
    assert ALG_NAME[4098] == "sphincs_shake_128s"


def test_expected_sizes_for_4098() -> None:
    """Verify SPHINCS+ (4098) has correct expected sizes."""
    from pq.py.registry import get_sig
    
    sphincs = get_sig("sphincs_shake_128s")
    assert sphincs is not None
    
    # These sizes must match what the PQ backend produces
    assert sphincs.pubkey_size == 64, "SPHINCS+ pubkey must be 64 bytes"
    assert sphincs.signature_size == 7856, "SPHINCS+ 128s signature must be 7856 bytes"
    
    # Verify via alg_id lookup
    by_id = get_sig(4098)
    assert by_id is not None
    assert by_id.pubkey_size == 64
    assert by_id.signature_size == 7856


def test_no_off_by_one_in_scheme_ids() -> None:
    """
    Ensure there's no off-by-one error in scheme_id interpretation.
    
    Common bug: Using 1-based indexing instead of actual IDs,
    or accidentally treating hex as decimal.
    """
    from pq.py.registry import ALG_IDS, get_sig
    
    # Verify exact hex values
    assert ALG_IDS["dilithium3"] == 0x1001  # Not 1 or 4096
    assert ALG_IDS["sphincs_shake_128s"] == 0x1002  # Not 2 or 4097
    
    # Verify lookup by decimal
    assert get_sig(4097) is not None  # dilithium3
    assert get_sig(4098) is not None  # sphincs
    
    # Verify these are NOT valid scheme IDs
    assert get_sig(1) is None  # Would be an off-by-one error
    assert get_sig(2) is None
    assert get_sig(4096) is None  # 0x1000 - would be another off-by-one


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
