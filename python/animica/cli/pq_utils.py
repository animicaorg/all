
from __future__ import annotations

from typing import Optional, Tuple


def _try_import_oqs():
    try:
        import oqs  # type: ignore
        return oqs, None
    except Exception as e:
        return None, str(e)


def _enabled_mechs(oqs_mod) -> list[str]:
    for fn in ("get_enabled_sig_mechanisms", "get_enabled_mechanisms"):
        if hasattr(oqs_mod, fn):
            try:
                return list(getattr(oqs_mod, fn)())
            except Exception:
                continue
    return []


def check_pq_signing_available() -> Tuple[bool, Optional[str]]:
    """
    Returns (available, error_msg).
    Treat ML-DSA-65 as Dilithium3 equivalent (liboqs 0.15+ rename).
    """
    oqs_mod, err = _try_import_oqs()
    if oqs_mod is None:
        return False, f"Failed to import oqs (liboqs-python): {err}"

    mechs = _enabled_mechs(oqs_mod)
    if not mechs:
        # Even if listing fails, we can still try to instantiate.
        mechs = []

    want = ["ML-DSA-65", "Dilithium3"]
    picked = None
    for w in want:
        if w in mechs:
            picked = w
            break
    picked = picked or "ML-DSA-65"

    try:
        s = oqs_mod.Signature(picked)
        pk = s.generate_keypair()
        sk = s.export_secret_key()
        msg = b"animica-pq-check"
        sig = s.sign(msg)
        ok = s.verify(msg, sig, pk)

        if not ok:
            return False, f"oqs self-test failed for {picked}"
        # Sanity sizes (avoid fake keys)
        if len(sk) <= len(pk) or bytes(sk) == bytes(pk):
            return False, f"oqs produced suspicious key sizes for {picked} (pk={len(pk)} sk={len(sk)})"
        return True, None
    except Exception as e:
        return False, f"Failed to init/self-test oqs Signature({picked}): {e}"


def get_pq_missing_error_message() -> str:
    return (
        "Post-quantum signing is not available.\n"
        "Make sure liboqs + liboqs-python are installed and the 'oqs' module imports cleanly.\n"
        "On Ubuntu, re-run: ./setup.sh\n"
    )
