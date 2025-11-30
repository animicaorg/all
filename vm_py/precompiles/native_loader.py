"""CTypes loader for native pq_precompile shared library.

Attempts to load a shared lib named `pq_precompile` (platform suffixes are handled by ctypes).
Exposes `verify(pubkey, message, signature, scheme)` to Python callers by calling the C ABI.

This is a development helper; production runtime should link the precompile directly into the host.
"""
from __future__ import annotations

import ctypes
import os
import sys
from typing import Optional

_lib = None

_lib_names = [
    # Platform-specific names to try
    "pq_precompile",
    "libpq_precompile",
]

def _find_and_load() -> Optional[ctypes.CDLL]:
    for name in _lib_names:
        try:
            if sys.platform == "win32":
                candidate = name + ".dll"
            elif sys.platform == "darwin":
                candidate = name + ".dylib"
            else:
                candidate = name + ".so"
            lib = ctypes.CDLL(candidate)
            return lib
        except OSError:
            continue
    # Try to load by full path in native/pq_precompile/target/release/
    here = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "native", "pq_precompile", "target", "release"))
    for fname in os.listdir(here) if os.path.isdir(here) else []:
        pass
    # try common path
    try:
        lib = ctypes.CDLL(os.path.join(here, "libpq_precompile.so"))
        return lib
    except Exception:
        return None


_lib = _find_and_load()


def verify(pubkey: bytes, message: bytes, signature: bytes, scheme: str = "Dilithium3") -> bool:
    """Call the native pq_verify C ABI. Returns True/False; raises RuntimeError if loader not present or error."""
    if _lib is None:
        raise RuntimeError("native pq_precompile library not found")

    func = getattr(_lib, "pq_verify")
    func.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                     ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                     ctypes.POINTER(ctypes.c_ubyte), ctypes.c_size_t,
                     ctypes.c_char_p]
    func.restype = ctypes.c_int

    pub_b = (ctypes.c_ubyte * len(pubkey)).from_buffer_copy(pubkey)
    msg_b = (ctypes.c_ubyte * len(message)).from_buffer_copy(message)
    sig_b = (ctypes.c_ubyte * len(signature)).from_buffer_copy(signature)
    scheme_b = scheme.encode('utf-8')

    res = func(pub_b, len(pubkey), msg_b, len(message), sig_b, len(signature), scheme_b)
    if res == 1:
        return True
    if res == 0:
        return False
    raise RuntimeError("pq_precompile call returned error code")

*** End Patch