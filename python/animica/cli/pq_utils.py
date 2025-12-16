"""Helpers for validating Animica's vendored liboqs 0.14.x stack."""

from __future__ import annotations

import os
from typing import Optional, Tuple

REQUIRED_PREFIX = "0.14."
VENDORED_RELATIVE = ".deps/liboqs/0.14.0"

# Dilithium3 algorithm ID (imported from pq.py.keygen if available)
try:
    from pq.py.keygen import DILITHIUM3_ID
except Exception:
    DILITHIUM3_ID = 0x1001  # Fallback constant


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


def _test_pq_py_keygen(alg_id: int) -> Tuple[bool, Optional[str]]:
    """
    Helper to test pq.py keygen with a specific algorithm ID.
    
    Returns (success, error_msg).
    """
    try:
        from pq.py.keygen import keygen_sig
        from pq.py.sign import sign_detached, verify_detached
        
        kp = keygen_sig(alg_id)
        
        # Basic validation
        if kp.public_key == kp.secret_key:
            return False, "pq.py produced sk==pk (invalid)"
        if len(kp.secret_key) <= len(kp.public_key):
            return False, f"pq.py produced suspicious key sizes (pk={len(kp.public_key)} sk={len(kp.secret_key)})"
        
        # Test signing (pq.py uses a more complex signing API)
        msg = b"animica-pq-check"
        sig_obj = sign_detached(msg, alg_id, kp.secret_key, domain="test")
        if not verify_detached(msg, sig_obj, kp.public_key, domain="test"):
            return False, "pq.py sign/verify self-test failed"
        
        return True, None
    except Exception as e:
        return False, str(e)


def check_pq_signing_available() -> Tuple[bool, Optional[str]]:
    """
    Returns (available, error_msg).

    We prefer the vendored liboqs build (0.14.x) and reject mismatched versions to
    avoid loading system-wide 0.15.x libraries. Falls back to pure-Python PQ
    implementations (animica.pq or pq.py) if liboqs is unavailable.
    """

    # Try oqs (liboqs-python) first
    oqs_mod, oqs_err = _try_import_oqs()
    if oqs_mod is not None:
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

    # Fall back to pure-Python animica.pq implementation
    try:
        from animica.pq import sig_keygen, sig_sign, sig_verify
        pk, sk = sig_keygen()
        msg = b"animica-pq-check"
        sig = sig_sign(sk, msg)
        if not sig_verify(pk, msg, sig):
            return False, "Pure-Python animica.pq self-test failed"
        if len(sk) <= len(pk) or bytes(sk) == bytes(pk):
            return False, f"animica.pq produced suspicious key sizes (pk={len(pk)} sk={len(sk)})"
        return True, None
    except Exception as animica_err:
        pass

    # Fall back to pq.py module
    # Try with the default algorithm that wallet.py will use
    try:
        from pq.py.registry import default_signature_alg
        
        alg_info = default_signature_alg()
        ok, err = _test_pq_py_keygen(alg_info.alg_id)
        if ok:
            return True, None
    except NotImplementedError:
        pass
    except Exception:
        pass

    # Default algorithm not supported, try Dilithium3 as fallback
    # since that's what animica.pq provides
    ok, err = _test_pq_py_keygen(DILITHIUM3_ID)
    if ok:
        return True, None

    # Neither backend available
    return False, f"No PQ backend available. oqs error: {oqs_err}"


def get_pq_diagnostics() -> dict:
    """
    Get detailed diagnostics about available PQ backends.
    
    Returns:
        dict with keys:
            - oqs_available: bool
            - oqs_error: Optional[str]
            - animica_pq_available: bool
            - animica_pq_error: Optional[str]
            - pq_py_available: bool
            - pq_py_error: Optional[str]
            - any_available: bool
    """
    result = {
        "oqs_available": False,
        "oqs_error": None,
        "animica_pq_available": False,
        "animica_pq_error": None,
        "pq_py_available": False,
        "pq_py_error": None,
        "any_available": False,
    }
    
    # Check oqs
    oqs_mod, oqs_err = _try_import_oqs()
    if oqs_mod is not None:
        ok, ver_msg = _check_versions(oqs_mod)
        if ok:
            result["oqs_available"] = True
        else:
            result["oqs_error"] = ver_msg
    else:
        result["oqs_error"] = str(oqs_err)
    
    # Check animica.pq
    try:
        from animica.pq import sig_keygen, sig_sign, sig_verify
        pk, sk = sig_keygen()
        msg = b"test"
        sig = sig_sign(sk, msg)
        if sig_verify(pk, msg, sig):
            result["animica_pq_available"] = True
    except Exception as e:
        result["animica_pq_error"] = str(e)
    
    # Check pq.py
    try:
        from pq.py.keygen import keygen_sig
        from pq.py.registry import default_signature_alg
        from pq.py.sign import sign_detached, verify_detached
        
        alg_info = default_signature_alg()
        kp = keygen_sig(alg_info.alg_id)
        msg = b"test"
        sig_obj = sign_detached(msg, alg_info.alg_id, kp.secret_key, domain="test")
        if verify_detached(msg, sig_obj, kp.public_key, domain="test"):
            result["pq_py_available"] = True
    except Exception as e:
        result["pq_py_error"] = str(e)
    
    result["any_available"] = (
        result["oqs_available"] or 
        result["animica_pq_available"] or 
        result["pq_py_available"]
    )
    
    return result


def get_pq_missing_error_message() -> str:
    """
    Get error message when PQ signing is not available.
    
    Now includes information about all backend attempts.
    """
    repo_hint = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
    vendored_hint = os.path.join(repo_hint, VENDORED_RELATIVE)
    
    diag = get_pq_diagnostics()
    
    msg = "Post-quantum signing is not available.\n"
    msg += f"Animica pins liboqs {REQUIRED_PREFIX} (vendored at: {vendored_hint}).\n"
    msg += "Re-run ./setup.sh to rebuild liboqs and install the ~/.local/bin/animica shim.\n"
    msg += "If you use a custom install, set LIBOQS_PATH to the vendored liboqs.so and prepend LD_LIBRARY_PATH accordingly.\n"
    msg += "\nBackend status:\n"
    
    if diag["oqs_available"]:
        msg += "  - oqs (liboqs-python): ✓ available\n"
    else:
        msg += f"  - oqs (liboqs-python): ✗ unavailable ({diag['oqs_error']})\n"
    
    if diag["animica_pq_available"]:
        msg += "  - animica.pq (pure-Python): ✓ available\n"
    else:
        msg += f"  - animica.pq (pure-Python): ✗ unavailable ({diag['animica_pq_error']})\n"
    
    if diag["pq_py_available"]:
        msg += "  - pq.py: ✓ available\n"
    else:
        msg += f"  - pq.py: ✗ unavailable ({diag['pq_py_error']})\n"
    
    return msg

