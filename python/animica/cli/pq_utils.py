"""Helpers for validating Animica's vendored liboqs 0.14.x stack."""

from __future__ import annotations

import os
from typing import Optional, Tuple

REQUIRED_PREFIX = "0.14."
VENDORED_RELATIVE = ".deps/liboqs/0.14.0"


def _try_import_oqs():
    try:
        import oqs  # type: ignore

        return oqs, None
    except Exception as e:  # pragma: no cover - diagnostics only
        return None, str(e)


def _enabled_mechs(oqs_mod) -> list[str]:
    for fn in ("get_enabled_sig_mechanisms", "get_enabled_mechanisms"):
        if hasattr(oqs_mod, fn):
            try:
                return list(getattr(oqs_mod, fn)())
            except Exception:  # pragma: no cover - defensive
                continue
    return []


def _check_versions(oqs_mod) -> Tuple[bool, Optional[str]]:
    py_ver = getattr(oqs_mod, "__version__", "")
    c_ver = ""
    if hasattr(oqs_mod, "oqs_version"):
        try:
            c_ver = str(oqs_mod.oqs_version())
        except Exception:
            c_ver = ""

    if py_ver and not py_ver.startswith(REQUIRED_PREFIX):
        return False, f"liboqs-python {py_ver} detected; Animica pins {REQUIRED_PREFIX}"
    if c_ver and not c_ver.startswith(REQUIRED_PREFIX):
        return False, f"liboqs C library {c_ver} detected; Animica pins {REQUIRED_PREFIX}"
    return True, None


def check_pq_signing_available() -> Tuple[bool, Optional[str]]:
    """
    Returns (available, error_msg).

    We prefer the vendored liboqs build (0.14.x) and reject mismatched versions to
    avoid loading system-wide 0.15.x libraries.
    """

    oqs_mod, err = _try_import_oqs()
    if oqs_mod is None:
        return False, f"Failed to import oqs (liboqs-python): {err}"

    ok, ver_msg = _check_versions(oqs_mod)
    if not ok:
        return False, ver_msg

    mechs = _enabled_mechs(oqs_mod)
    want = ["Dilithium3", "ML-DSA-65"]  # ML-DSA kept for forward compat
    picked = next((w for w in want if w in mechs), want[0])

    try:
        signer = oqs_mod.Signature(picked)
        pk = signer.generate_keypair()
        sk = signer.export_secret_key()
        msg = b"animica-pq-check"
        sig = signer.sign(msg)
        ok = signer.verify(msg, sig, pk)
        if not ok:
            return False, f"oqs self-test failed for {picked}"
        if len(sk) <= len(pk) or bytes(sk) == bytes(pk):
            return False, f"oqs produced suspicious key sizes for {picked} (pk={len(pk)} sk={len(sk)})"
        return True, None
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"Failed to init/self-test oqs Signature({picked}): {exc}"


def get_pq_missing_error_message() -> str:
    repo_hint = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
    vendored_hint = os.path.join(repo_hint, VENDORED_RELATIVE)
    return (
        "Post-quantum signing is not available.\n"
        f"Animica pins liboqs {REQUIRED_PREFIX} (vendored at: {vendored_hint}).\n"
        "Re-run ./setup.sh to rebuild liboqs and install the ~/.local/bin/animica shim.\n"
        "If you use a custom install, set LIBOQS_PATH to the vendored liboqs.so and prepend LD_LIBRARY_PATH accordingly.\n"
    )

