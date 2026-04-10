"""
Test configuration for coretx tests
"""

import pytest


@pytest.fixture
def test_body():
    """Fixture for a standard test transaction body"""
    from coretx.types import TxBody, TxKind
    
    return TxBody(
        version=1,
        chain_id=1,
        nonce=0,
        from_addr=b"\x01" * 32,
        to_addr=b"\x02" * 32,
        value=1000,
        fee=21,
        gas_limit=21000,
        data=b"",
        memo="test",
        timestamp=1234567890,
        kind=TxKind.TRANSFER,
    )


@pytest.fixture
def test_auth():
    """Fixture for a standard test auth"""
    from coretx.types import TxAuth
    
    return TxAuth(
        scheme_id=1,
        pubkey_bytes=b"pubkey" * 10,
        signature_bytes=b"signature" * 20,
        prehash_id=2,
    )
