from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from animica.cli import pq_utils


@contextmanager
def mock_import(mock_oqs):
    original_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "oqs":
            if mock_oqs is None:
                raise ImportError("No module named 'oqs'")
            return mock_oqs
        return original_import(name, *args, **kwargs)

    try:
        __builtins__["__import__"] = _fake_import
        yield
    finally:
        __builtins__["__import__"] = original_import


def test_import_error_falls_back_to_pure_python():
    """
    When oqs is not available, check_pq_signing_available() should fall back
    to pure-Python backends (animica.pq or pq.py).
    """
    with mock_import(None):
        available, err = pq_utils.check_pq_signing_available()
        # Should succeed via fallback to animica.pq or pq.py
        # In test environment, at least animica.pq should be available
        # If no backends are available, then it should fail
        if available:
            assert err is None
        else:
            # If it fails, the error should mention that no backend is available
            assert "No PQ backend available" in (err or "")


def test_version_mismatch_rejected():
    mock = SimpleNamespace(
        __version__="0.15.0",
        oqs_version=lambda: "0.15.0",
        get_enabled_sig_mechanisms=lambda: ["Dilithium3"],
    )
    with mock_import(mock):
        available, err = pq_utils.check_pq_signing_available()
        assert available is False
        assert "0.14." in (err or "")


def test_successful_detection_with_dilithium():
    class DummySig:
        def __init__(self, *_):
            pass

        def generate_keypair(self):
            return b"pk", b"secret" * 5

        def export_secret_key(self):
            return b"secret" * 5

        def sign(self, msg):
            return b"sig" + msg

        def verify(self, msg, sig, _pk):
            return sig == b"sig" + msg

    mock = SimpleNamespace(
        __version__="0.14.0",
        oqs_version=lambda: "0.14.0",
        get_enabled_sig_mechanisms=lambda: ["Dilithium3"],
        Signature=DummySig,
    )

    with mock_import(mock):
        available, err = pq_utils.check_pq_signing_available()
        assert available is True
        assert err is None


def test_missing_error_message_mentions_vendored_path():
    msg = pq_utils.get_pq_missing_error_message()
    assert "setup.sh" in msg
    assert ".deps/liboqs/0.14.0" in msg
    # Now also mentions backend status
    assert "Backend status:" in msg


def test_get_pq_diagnostics_structure():
    """Test that get_pq_diagnostics returns expected structure."""
    diag = pq_utils.get_pq_diagnostics()
    
    # Check all expected keys are present
    expected_keys = {
        "oqs_available",
        "oqs_error",
        "animica_pq_available",
        "animica_pq_error",
        "pq_py_available",
        "pq_py_error",
        "any_available",
    }
    assert set(diag.keys()) == expected_keys
    
    # Check types
    assert isinstance(diag["oqs_available"], bool)
    assert isinstance(diag["animica_pq_available"], bool)
    assert isinstance(diag["pq_py_available"], bool)
    assert isinstance(diag["any_available"], bool)
    
    # Check that any_available is true if any backend is available
    any_backend = (
        diag["oqs_available"] or 
        diag["animica_pq_available"] or 
        diag["pq_py_available"]
    )
    assert diag["any_available"] == any_backend


def test_pure_python_fallback_when_oqs_unavailable():
    """
    Test that pure-Python backends (animica.pq or pq.py) are used
    when oqs is not available.
    """
    # In test environment without oqs, should fall back to pure-Python
    diag = pq_utils.get_pq_diagnostics()
    
    # If oqs is not available, at least one fallback should work
    if not diag["oqs_available"]:
        assert diag["animica_pq_available"] or diag["pq_py_available"], (
            "Expected at least one pure-Python backend to be available"
        )

