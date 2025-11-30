"""Unit tests for the VM stdlib pq_verify shim.

These tests validate the mock HMAC-based verification behaves as expected.
"""

from __future__ import annotations

from tools.quantum import mock_pq
from vm_py.stdlib import pq_verify


def test_pq_verify_accepts_valid_signature():
    sk = mock_pq.gen_key()
    msg = b"hello-quantum"
    sig = mock_pq.sign(msg, sk)
    assert pq_verify.verify(sk, msg, sig)


def test_pq_verify_rejects_bad_signature():
    sk = mock_pq.gen_key()
    msg = b"hello-quantum"
    bad = b"deadbeef"
    assert not pq_verify.verify(sk, msg, bad.hex())


if __name__ == "__main__":
    import pytest

    pytest.main([__file__])
