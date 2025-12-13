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


def test_import_error_reports_reason():
    with mock_import(None):
        available, err = pq_utils.check_pq_signing_available()
        assert available is False
        assert "Failed to import oqs" in (err or "")


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

