from __future__ import annotations

import pytest

try:
    from vm_py.precompiles import native_loader

    _HAS_NATIVE = True
except Exception:
    native_loader = None
    _HAS_NATIVE = False

try:
    import oqs

    _HAS_OQS = True
except Exception:
    oqs = None
    _HAS_OQS = False


@pytest.mark.skipif(
    not (_HAS_NATIVE or _HAS_OQS), reason="No native precompile or python-oqs available"
)
def test_native_verify_dilithium():
    scheme = "Dilithium3"

    if _HAS_OQS:
        signer = oqs.sig.Sig(scheme)
        pk, sk = signer.keypair()
        msg = b"test-message"
        sig = signer.sign(msg, sk)
        # Try native path if available
        if _HAS_NATIVE:
            ok = native_loader.verify(pk, msg, sig, scheme)
            assert ok is True
        else:
            # Fallback: python-oqs verify
            assert signer.verify(msg, sig, pk) is True
    else:
        pytest.skip("python-oqs not available")
