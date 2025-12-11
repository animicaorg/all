from __future__ import annotations

"""
Optional liboqs ctypes backend for Animica PQ primitives.

What this module does
---------------------
- Dynamically loads the liboqs C library via ctypes (if present).
- Exposes a *uniform*, tiny wrapper for:
    • Signatures: Dilithium3, SPHINCS+-SHAKE-128s
    • KEM: Kyber768 (a.k.a. ML-KEM-768)
- Reports sizes (pk/sk/ct/ss/sig) directly from liboqs structs.
- Falls back gracefully: if liboqs isn't available, `is_available()` returns False
  and constructing `OQSBackend()` raises a clear RuntimeError.

Why this exists when `python-oqs` also exists?
----------------------------------------------
We prefer `python-oqs` when available (see the high-level wrappers in the sibling
modules). This backend is a *secondary* path that can be used to avoid Python
package/runtime issues or to exercise a lower-level ABI from a single, static
liboqs shared object. Nothing in this repo *requires* it at runtime.

Safety notes
------------
- This module is *only* a loader + FFI surface; it does not implement crypto.
- If you do not have liboqs installed (e.g., via your package manager or from
  source), `is_available()` will be False.
"""

import ctypes
import glob
import logging
import os
import sys
from ctypes import (POINTER, byref, c_char_p, c_int, c_size_t, c_uint8,
                    c_void_p, create_string_buffer)
from ctypes.util import find_library
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Recommended liboqs version
RECOMMENDED_LIBOQS_VERSION = "0.15.0"

# Common library SONAME versions to try (newest to oldest)
# These may need updating as new liboqs versions are released
LIBOQS_SONAME_VERSIONS = ["liboqs.so.5", "liboqs.so.4", "liboqs.so.3"]

# SPHINCS+ parameter set preference (matches sphincs_shake_128s thin wrapper)
_SPHINCS_VARIANT_ENV = "ANIMICA_SPHINCS_VARIANT"

# --------------------------------------------------------------------------------------------------
# Attempt to load liboqs
# --------------------------------------------------------------------------------------------------


def _get_python_oqs_bundled_lib_paths() -> List[str]:
    """
    Get paths where python-oqs might have bundled liboqs.
    
    When python-oqs (liboqs-python) is installed via wheel, it may bundle
    the liboqs shared library in its package directory. This function
    finds those locations.
    
    Returns:
        List of potential paths to liboqs shared library from python-oqs
    """
    paths: List[str] = []
    
    try:
        # Try to import oqs module to find its location
        import importlib.util
        spec = importlib.util.find_spec("oqs")
        if spec and spec.origin:
            # oqs module found, check for bundled libs nearby
            oqs_dir = os.path.dirname(spec.origin)
            logger.debug(f"Found oqs module at: {oqs_dir}")
            
            # Common locations where wheels bundle native libs
            lib_patterns = [
                os.path.join(oqs_dir, "liboqs.so*"),
                os.path.join(oqs_dir, "liboqs.dylib"),
                os.path.join(oqs_dir, ".libs", "liboqs.so*"),
                os.path.join(oqs_dir, ".dylibs", "liboqs.dylib"),
                os.path.join(oqs_dir, "lib", "liboqs.so*"),
                os.path.join(oqs_dir, "lib", "liboqs.dylib"),
            ]
            
            for pattern in lib_patterns:
                matches = glob.glob(pattern)
                paths.extend(matches)
                
            # Also check versioned SONAMEs
            for soname in LIBOQS_SONAME_VERSIONS:
                candidate = os.path.join(oqs_dir, soname)
                if os.path.exists(candidate):
                    paths.append(candidate)
                # Check .libs subdirectory
                candidate = os.path.join(oqs_dir, ".libs", soname)
                if os.path.exists(candidate):
                    paths.append(candidate)
    except Exception as e:
        logger.debug(f"Could not locate python-oqs bundled libs: {e}")
    
    return paths


def _load_liboqs() -> Optional[ctypes.CDLL]:
    """
    Attempt to load liboqs shared library.
    
    Searches in order:
    1. LIBOQS_PATH environment variable (explicit path to .so/.dylib/.dll)
    2. Python-oqs wheel bundled library paths
    3. System library search path via find_library()
    4. Common library names in standard locations
    5. Custom paths from LD_LIBRARY_PATH/DYLD_LIBRARY_PATH
    
    Returns:
        ctypes.CDLL instance if loaded, None if not found
    """
    logger.debug("Starting liboqs library search...")
    
    # Step 1: Allow manual override (useful in CI or non-standard paths)
    override = os.environ.get("LIBOQS_PATH")
    if override:
        if os.path.exists(override):
            logger.info(f"Loading liboqs from LIBOQS_PATH: {override}")
            try:
                lib = ctypes.CDLL(override)
                logger.info(f"✓ Successfully loaded liboqs from LIBOQS_PATH: {override}")
                return lib
            except OSError as e:
                logger.warning(f"Failed to load liboqs from LIBOQS_PATH {override}: {e}")
        else:
            logger.warning(f"LIBOQS_PATH={override} does not exist")

    # Step 2: Check python-oqs bundled libraries first
    bundled_paths = _get_python_oqs_bundled_lib_paths()
    if bundled_paths:
        logger.debug(f"Found {len(bundled_paths)} python-oqs bundled lib candidate(s)")
        for path in bundled_paths:
            logger.debug(f"Trying python-oqs bundled lib: {path}")
            try:
                lib = ctypes.CDLL(path)
                logger.info(f"✓ Successfully loaded liboqs from python-oqs wheel: {path}")
                return lib
            except OSError as e:
                logger.debug(f"Failed to load {path}: {e}")
    else:
        logger.debug("No python-oqs bundled libraries found")

    # Step 3: Try system library search via find_library
    candidates: List[str] = []
    probe = find_library("oqs")
    if probe:
        logger.debug(f"find_library('oqs') returned: {probe}")
        candidates.append(probe)
    else:
        logger.debug("find_library('oqs') returned None")
    
    # Step 4: Common SONAMEs on Linux/macOS/Windows
    candidates += ["liboqs.so", "liboqs.dylib", "oqs.dll"]
    
    # Also try versioned library names (Linux only)
    candidates += LIBOQS_SONAME_VERSIONS

    # Log environment variables for diagnostic purposes
    if sys.platform == "darwin":
        dyld_path = os.environ.get("DYLD_LIBRARY_PATH")
        if dyld_path:
            logger.debug(f"DYLD_LIBRARY_PATH is set: {dyld_path}")
        else:
            logger.debug("DYLD_LIBRARY_PATH is not set")
    else:
        ld_path = os.environ.get("LD_LIBRARY_PATH")
        if ld_path:
            logger.debug(f"LD_LIBRARY_PATH is set: {ld_path}")
        else:
            logger.debug("LD_LIBRARY_PATH is not set")

    logger.debug(f"Trying {len(candidates)} system library candidates")
    for name in candidates:
        try:
            lib = ctypes.CDLL(name)
            logger.info(f"✓ Successfully loaded liboqs from system: {name}")
            return lib
        except OSError as e:
            logger.debug(f"Failed to load liboqs candidate '{name}': {e}")
            continue
    
    # Step 5: Provide detailed failure message at DEBUG level
    # (Higher-level modules like capability.py provide user-facing messages)
    logger.debug(
        "liboqs shared library not found after searching:\n"
        f"  - LIBOQS_PATH environment variable: {override or '(not set)'}\n"
        f"  - python-oqs wheel bundled paths: {len(bundled_paths)} checked\n"
        f"  - System library search: {len(candidates)} candidates\n"
        f"  - Environment: LD_LIBRARY_PATH/DYLD_LIBRARY_PATH {'set' if os.environ.get('LD_LIBRARY_PATH') or os.environ.get('DYLD_LIBRARY_PATH') else 'not set'}\n"
        f"\n"
        f"To fix:\n"
        f"  1. Install liboqs-dev (apt/brew) or build from source\n"
        f"  2. Install python-oqs: pip install liboqs-python\n"
        f"  3. Set library path if needed:\n"
        f"     - Linux: export LD_LIBRARY_PATH=/path/to/liboqs/lib:$LD_LIBRARY_PATH\n"
        f"     - macOS: export DYLD_LIBRARY_PATH=/path/to/liboqs/lib:$DYLD_LIBRARY_PATH\n"
        f"  4. Or set LIBOQS_PATH=/path/to/liboqs.so directly\n"
        f"\n"
        f"See: https://github.com/open-quantum-safe/liboqs/releases/tag/{RECOMMENDED_LIBOQS_VERSION}"
    )
    return None


_LIB = _load_liboqs()
_HAVE = _LIB is not None

OQS_SUCCESS = 0


def is_available() -> bool:
    """Return True if liboqs was successfully loaded."""
    return _HAVE


def get_version_info() -> Optional[str]:
    """
    Get liboqs version information if available.
    
    Returns:
        Version string if liboqs is loaded, None otherwise
    """
    if not _HAVE or _LIB is None:
        return None
    
    try:
        # Try to get version string from liboqs
        # OQS_version_str is available in liboqs >= 0.8.0
        if hasattr(_LIB, "OQS_version"):
            _LIB.OQS_version.restype = c_char_p
            version_bytes = _LIB.OQS_version()
            if version_bytes:
                return version_bytes.decode("utf-8")
    except Exception as e:
        logger.debug(f"Could not retrieve liboqs version: {e}")
    
    return "unknown"


def has_sig(mechanism: str) -> bool:
    """
    Check if a specific signature mechanism is available.
    
    Args:
        mechanism: Mechanism name (e.g., "ML-DSA-65", "dilithium3", "sphincs_shake_128s")
    
    Returns:
        True if mechanism is available, False otherwise
    """
    if not _HAVE or _LIB is None:
        return False
    
    # Try to instantiate the mechanism
    try:
        backend = OQSBackend()
        normalized = backend._normalize_sig_alg(mechanism)
        sig = _LIB.OQS_SIG_new(normalized)
        if sig:
            _LIB.OQS_SIG_free(sig)
            return True
        return False
    except Exception:
        return False


def has_kem(mechanism: str) -> bool:
    """
    Check if a specific KEM mechanism is available.
    
    Args:
        mechanism: Mechanism name (e.g., "ML-KEM-768", "kyber768")
    
    Returns:
        True if mechanism is available, False otherwise
    """
    if not _HAVE or _LIB is None:
        return False
    
    try:
        backend = OQSBackend()
        normalized = backend._normalize_kem_alg(mechanism)
        kem = _LIB.OQS_KEM_new(normalized)
        if kem:
            _LIB.OQS_KEM_free(kem)
            return True
        return False
    except Exception:
        return False


# --------------------------------------------------------------------------------------------------
# Minimal struct views (prefix-only) to read size fields from opaque liboqs objects.
# We purposefully model only the fields we read. The remaining function pointers and
# members in the real C structs follow those fields and need not be declared here.
# --------------------------------------------------------------------------------------------------


class _OQS_SIG(ctypes.Structure):
    _fields_ = [
        ("method_name", c_char_p),
        ("alg_version", c_char_p),
        ("claimed_nist_level", c_size_t),  # uint32 in practice; size_t is conservative
        ("is_euf_cma", c_int),
        ("length_public_key", c_size_t),
        ("length_secret_key", c_size_t),
        ("length_signature", c_size_t),
        # (function pointers follow in the real struct; we don't need them)
    ]


class _OQS_KEM(ctypes.Structure):
    _fields_ = [
        ("method_name", c_char_p),
        ("alg_version", c_char_p),
        ("claimed_nist_level", c_size_t),
        ("ind_cca", c_int),
        ("length_public_key", c_size_t),
        ("length_secret_key", c_size_t),
        ("length_ciphertext", c_size_t),
        ("length_shared_secret", c_size_t),
        # (function pointers follow; we don't need them)
    ]


if _HAVE:
    # Signature API
    _LIB.OQS_SIG_new.argtypes = [c_char_p]
    _LIB.OQS_SIG_new.restype = POINTER(_OQS_SIG)
    _LIB.OQS_SIG_free.argtypes = [POINTER(_OQS_SIG)]
    _LIB.OQS_SIG_free.restype = None

    _LIB.OQS_SIG_keypair.argtypes = [
        POINTER(_OQS_SIG),
        POINTER(c_uint8),
        POINTER(c_uint8),
    ]
    _LIB.OQS_SIG_keypair.restype = c_int

    _LIB.OQS_SIG_sign.argtypes = [
        POINTER(_OQS_SIG),
        POINTER(c_uint8),
        POINTER(c_size_t),
        POINTER(c_uint8),
        c_size_t,
        POINTER(c_uint8),
    ]
    _LIB.OQS_SIG_sign.restype = c_int

    _LIB.OQS_SIG_verify.argtypes = [
        POINTER(_OQS_SIG),
        POINTER(c_uint8),
        c_size_t,
        POINTER(c_uint8),
        c_size_t,
        POINTER(c_uint8),
    ]
    _LIB.OQS_SIG_verify.restype = c_int

    # KEM API
    _LIB.OQS_KEM_new.argtypes = [c_char_p]
    _LIB.OQS_KEM_new.restype = POINTER(_OQS_KEM)
    _LIB.OQS_KEM_free.argtypes = [POINTER(_OQS_KEM)]
    _LIB.OQS_KEM_free.restype = None

    _LIB.OQS_KEM_keypair.argtypes = [
        POINTER(_OQS_KEM),
        POINTER(c_uint8),
        POINTER(c_uint8),
    ]
    _LIB.OQS_KEM_keypair.restype = c_int

    _LIB.OQS_KEM_encaps.argtypes = [
        POINTER(_OQS_KEM),
        POINTER(c_uint8),
        POINTER(c_uint8),
        POINTER(c_uint8),
    ]
    _LIB.OQS_KEM_encaps.restype = c_int

    _LIB.OQS_KEM_decaps.argtypes = [
        POINTER(_OQS_KEM),
        POINTER(c_uint8),
        POINTER(c_uint8),
        POINTER(c_uint8),
    ]
    _LIB.OQS_KEM_decaps.restype = c_int


# Canonical algorithm names as liboqs expects them.
# liboqs 0.15.x uses NIST standard names (ML-DSA, ML-KEM)
# We maintain backward compatibility with older names

# Signature algorithms
# ML-DSA (liboqs 0.15.0+, NIST standard names)
ALG_ML_DSA_44 = b"ML-DSA-44"
ALG_ML_DSA_65 = b"ML-DSA-65"
ALG_ML_DSA_87 = b"ML-DSA-87"

# Dilithium (legacy, liboqs < 0.15.0)
ALG_DILITHIUM2 = b"Dilithium2"
ALG_DILITHIUM3 = b"Dilithium3"
ALG_DILITHIUM5 = b"Dilithium5"

# SPHINCS+ (both simple and robust variants)
ALG_SPHINCS_SHAKE_128S = b"SPHINCS+-SHAKE-128s-simple"
ALG_SPHINCS_SHAKE_128S_ROBUST = b"SPHINCS+-SHAKE-128s-robust"

# KEM algorithms
ALG_KYBER768 = b"Kyber768"  # Legacy, liboqs < 0.15.0
ALG_ML_KEM_768 = b"ML-KEM-768"  # NIST standard, liboqs 0.15.0+


@dataclass(frozen=True)
class SigSizes:
    pk: int
    sk: int
    sig: int


@dataclass(frozen=True)
class KemSizes:
    pk: int
    sk: int
    ct: int
    ss: int


class OQSBackend:
    """
    Thin RAII wrapper over liboqs for the few algorithms we care about.
    """

    def __init__(self):
        if not _HAVE:
            # Provide detailed error message with troubleshooting steps
            error_msg = (
                "liboqs shared library not found.\n\n"
                "To fix this issue:\n\n"
                f"1. Install liboqs (recommended version: v{RECOMMENDED_LIBOQS_VERSION} or later):\n"
                "   • Ubuntu/Debian: sudo apt-get install liboqs-dev\n"
                "   • macOS: brew install liboqs\n"
                f"   • From source: https://github.com/open-quantum-safe/liboqs/releases/tag/{RECOMMENDED_LIBOQS_VERSION}\n\n"
                "2. If built from source, ensure library paths are set:\n"
            )
            
            if sys.platform == "darwin":
                dyld_path = os.environ.get("DYLD_LIBRARY_PATH", "")
                error_msg += f"   • export DYLD_LIBRARY_PATH=/path/to/liboqs/lib:$DYLD_LIBRARY_PATH\n"
                if dyld_path:
                    error_msg += f"   Current DYLD_LIBRARY_PATH: {dyld_path}\n"
            else:
                ld_path = os.environ.get("LD_LIBRARY_PATH", "")
                error_msg += f"   • export LD_LIBRARY_PATH=/path/to/liboqs/lib:$LD_LIBRARY_PATH\n"
                if ld_path:
                    error_msg += f"   Current LD_LIBRARY_PATH: {ld_path}\n"
            
            error_msg += (
                "\n3. Alternatively, install python-oqs:\n"
                "   python -m pip install liboqs-python\n\n"
                "4. Or set LIBOQS_PATH to the full path of the shared library:\n"
                "   export LIBOQS_PATH=/path/to/liboqs.so  # (or .dylib on macOS)\n"
            )
            
            raise RuntimeError(error_msg)
        
        # Log successful initialization with version info
        version = get_version_info()
        if version:
            logger.info(f"OQSBackend initialized with liboqs version: {version}")
        else:
            logger.info("OQSBackend initialized (liboqs version unknown)")

    # ------------- internals -------------
    def _sig_new(self, name: bytes) -> Tuple[POINTER(_OQS_SIG), SigSizes]:
        sig = _LIB.OQS_SIG_new(name)
        if not sig:
            raise RuntimeError(f"OQS_SIG_new failed (algorithm not enabled?): {name!r}")
        sizes = SigSizes(
            pk=int(sig.contents.length_public_key),
            sk=int(sig.contents.length_secret_key),
            sig=int(sig.contents.length_signature),
        )
        return sig, sizes

    def _kem_new(self, name: bytes) -> Tuple[POINTER(_OQS_KEM), KemSizes]:
        kem = _LIB.OQS_KEM_new(name)
        if not kem:
            raise RuntimeError(f"OQS_KEM_new failed (algorithm not enabled?): {name!r}")
        sizes = KemSizes(
            pk=int(kem.contents.length_public_key),
            sk=int(kem.contents.length_secret_key),
            ct=int(kem.contents.length_ciphertext),
            ss=int(kem.contents.length_shared_secret),
        )
        return kem, sizes

    # ------------- helpers: probing -------------
    @staticmethod
    def _probe_sig_mechanism(mechanism: bytes) -> bool:
        """
        Probe if a signature mechanism is available.
        
        Args:
            mechanism: Mechanism name (bytes)
        
        Returns:
            True if mechanism can be instantiated, False otherwise
        """
        try:
            sig = _LIB.OQS_SIG_new(mechanism)
            if sig:
                _LIB.OQS_SIG_free(sig)
                return True
            return False
        except Exception:
            return False
    
    @staticmethod
    def _probe_kem_mechanism(mechanism: bytes) -> bool:
        """
        Probe if a KEM mechanism is available.
        
        Args:
            mechanism: Mechanism name (bytes)
        
        Returns:
            True if mechanism can be instantiated, False otherwise
        """
        try:
            kem = _LIB.OQS_KEM_new(mechanism)
            if kem:
                _LIB.OQS_KEM_free(kem)
                return True
            return False
        except Exception:
            return False

    # ------------- public: signatures -------------
    def sig_available_names(self) -> List[str]:
        """
        Probe and return available signature mechanism names.
        
        Tries both modern (ML-DSA) and legacy (Dilithium) names.
        """
        names: List[bytes] = []
        
        # Probe ML-DSA variants (liboqs 0.15.0+)
        for candidate in [ALG_ML_DSA_65, ALG_ML_DSA_87, ALG_ML_DSA_44]:
            if self._probe_sig_mechanism(candidate):
                names.append(candidate)
        
        # Probe legacy Dilithium variants (liboqs < 0.15.0)
        for candidate in [ALG_DILITHIUM3, ALG_DILITHIUM2, ALG_DILITHIUM5]:
            if self._probe_sig_mechanism(candidate):
                names.append(candidate)
        
        # Probe SPHINCS+ variants
        for candidate in [ALG_SPHINCS_SHAKE_128S, ALG_SPHINCS_SHAKE_128S_ROBUST]:
            if self._probe_sig_mechanism(candidate):
                names.append(candidate)
        
        return [n.decode("ascii") for n in names]

    def sig_keypair(self, alg: str) -> Tuple[bytes, bytes]:
        name = self._normalize_sig_alg(alg)
        sig, sizes = self._sig_new(name)
        try:
            pk_buf = (c_uint8 * sizes.pk)()
            sk_buf = (c_uint8 * sizes.sk)()
            rc = _LIB.OQS_SIG_keypair(sig, pk_buf, sk_buf)
            if rc != OQS_SUCCESS:
                raise RuntimeError(f"OQS_SIG_keypair failed (rc={rc})")
            pk = bytes(pk_buf)
            sk = bytes(sk_buf)
            return (sk, pk)
        finally:
            _LIB.OQS_SIG_free(sig)

    def sig_sign(self, alg: str, msg: bytes, sk: bytes) -> bytes:
        name = self._normalize_sig_alg(alg)
        sig, sizes = self._sig_new(name)
        try:
            sig_out = (c_uint8 * sizes.sig)()
            sig_len = c_size_t(0)
            msg_buf = (c_uint8 * len(msg)).from_buffer_copy(msg)
            sk_buf = (c_uint8 * len(sk)).from_buffer_copy(sk)
            rc = _LIB.OQS_SIG_sign(
                sig, sig_out, byref(sig_len), msg_buf, c_size_t(len(msg)), sk_buf
            )
            if rc != OQS_SUCCESS:
                raise RuntimeError(f"OQS_SIG_sign failed (rc={rc})")
            return bytes(sig_out)[: int(sig_len.value)]
        finally:
            _LIB.OQS_SIG_free(sig)

    def sig_verify(self, alg: str, msg: bytes, signature: bytes, pk: bytes) -> bool:
        name = self._normalize_sig_alg(alg)
        sig, _sizes = self._sig_new(name)
        try:
            msg_buf = (c_uint8 * len(msg)).from_buffer_copy(msg)
            sig_buf = (c_uint8 * len(signature)).from_buffer_copy(signature)
            pk_buf = (c_uint8 * len(pk)).from_buffer_copy(pk)
            rc = _LIB.OQS_SIG_verify(
                sig,
                msg_buf,
                c_size_t(len(msg)),
                sig_buf,
                c_size_t(len(signature)),
                pk_buf,
            )
            return rc == OQS_SUCCESS
        finally:
            _LIB.OQS_SIG_free(sig)

    # ------------- public: KEM -------------
    def kem_available_names(self) -> List[str]:
        """
        Probe and return available KEM mechanism names.
        
        Tries both modern (ML-KEM) and legacy (Kyber) names.
        """
        names: List[bytes] = []
        for candidate in (ALG_ML_KEM_768, ALG_KYBER768):
            if self._probe_kem_mechanism(candidate):
                names.append(candidate)
        return [n.decode("ascii") for n in names]

    def kem_keypair(self, alg: str) -> Tuple[bytes, bytes]:
        name = self._normalize_kem_alg(alg)
        kem, sizes = self._kem_new(name)
        try:
            pk_buf = (c_uint8 * sizes.pk)()
            sk_buf = (c_uint8 * sizes.sk)()
            rc = _LIB.OQS_KEM_keypair(kem, pk_buf, sk_buf)
            if rc != OQS_SUCCESS:
                raise RuntimeError(f"OQS_KEM_keypair failed (rc={rc})")
            return (bytes(sk_buf), bytes(pk_buf))
        finally:
            _LIB.OQS_KEM_free(kem)

    def kem_encapsulate(self, alg: str, pk: bytes) -> Tuple[bytes, bytes]:
        name = self._normalize_kem_alg(alg)
        kem, sizes = self._kem_new(name)
        try:
            ct_buf = (c_uint8 * sizes.ct)()
            ss_buf = (c_uint8 * sizes.ss)()
            pk_buf = (c_uint8 * len(pk)).from_buffer_copy(pk)
            rc = _LIB.OQS_KEM_encaps(kem, ct_buf, ss_buf, pk_buf)
            if rc != OQS_SUCCESS:
                raise RuntimeError(f"OQS_KEM_encaps failed (rc={rc})")
            return (bytes(ct_buf), bytes(ss_buf))
        finally:
            _LIB.OQS_KEM_free(kem)

    def kem_decapsulate(self, alg: str, sk: bytes, ct: bytes) -> bytes:
        name = self._normalize_kem_alg(alg)
        kem, sizes = self._kem_new(name)
        try:
            ss_buf = (c_uint8 * sizes.ss)()
            sk_buf = (c_uint8 * len(sk)).from_buffer_copy(sk)
            ct_buf = (c_uint8 * len(ct)).from_buffer_copy(ct)
            rc = _LIB.OQS_KEM_decaps(kem, ss_buf, ct_buf, sk_buf)
            if rc != OQS_SUCCESS:
                raise RuntimeError(f"OQS_KEM_decaps failed (rc={rc})")
            return bytes(ss_buf)
        finally:
            _LIB.OQS_KEM_free(kem)

    # ------------- helpers -------------
    def _try_sig_mechanism(self, primary: bytes, fallback: bytes) -> bytes:
        """
        Try primary mechanism, fall back to secondary if unavailable.
        
        Args:
            primary: Primary mechanism to try
            fallback: Fallback mechanism if primary unavailable
        
        Returns:
            Primary if available, otherwise fallback
        """
        if self._probe_sig_mechanism(primary):
            return primary
        return fallback

    def _select_sphincs_mechanism(self) -> bytes:
        """
        Choose a deterministic SPHINCS+ mechanism based on availability.

        Preference order mirrors pq.py.algs.sphincs_shake_128s:
          • Default: prefer the "robust" parameter set for compatibility with
            older liboqs builds used by existing nodes.
          • ANIMICA_SPHINCS_VARIANT=simple flips the preference to "simple".
        """

        preferred = os.environ.get(_SPHINCS_VARIANT_ENV, "robust").strip().lower()
        variant_order = ["robust", "simple"]
        if preferred == "simple":
            variant_order = ["simple", "robust"]

        candidates = {
            "simple": [ALG_SPHINCS_SHAKE_128S, ALG_SPHINCS_SHAKE_128S_ROBUST],
            "robust": [ALG_SPHINCS_SHAKE_128S_ROBUST, ALG_SPHINCS_SHAKE_128S],
        }

        for variant in variant_order:
            for mech in candidates[variant]:
                if self._probe_sig_mechanism(mech):
                    return mech

        # If nothing probed successfully, fall back to the first option in the
        # preferred order to ensure deterministic selection.
        return candidates[variant_order[0]][0]
    
    def _normalize_sig_alg(self, alg: str) -> bytes:
        """
        Normalize algorithm name to liboqs mechanism name.
        
        Handles both modern (ML-DSA) and legacy (Dilithium) names,
        with automatic fallback between versions.
        """
        a = alg.lower().replace("_", "-")
        
        # ML-DSA variants (liboqs 0.15.0+) with Dilithium fallback
        if "ml-dsa-65" in a or "mldsa65" in a:
            return self._try_sig_mechanism(ALG_ML_DSA_65, ALG_DILITHIUM3)
        
        if "ml-dsa-87" in a or "mldsa87" in a:
            return self._try_sig_mechanism(ALG_ML_DSA_87, ALG_DILITHIUM5)
        
        if "ml-dsa-44" in a or "mldsa44" in a:
            return self._try_sig_mechanism(ALG_ML_DSA_44, ALG_DILITHIUM2)
        
        # Legacy Dilithium names - try to map to ML-DSA first for 0.15.0+
        if "dilithium3" in a:
            return self._try_sig_mechanism(ALG_ML_DSA_65, ALG_DILITHIUM3)
        
        if "dilithium2" in a:
            return self._try_sig_mechanism(ALG_ML_DSA_44, ALG_DILITHIUM2)
        
        if "dilithium5" in a:
            return self._try_sig_mechanism(ALG_ML_DSA_87, ALG_DILITHIUM5)

        # SPHINCS+ variants - prefer simple profile
        if "sphincs" in a:
            return self._select_sphincs_mechanism()

        raise ValueError(f"Unknown/unsupported signature alg: {alg}")

    @staticmethod
    def _normalize_kem_alg(alg: str) -> bytes:
        a = alg.lower().replace("_", "-")
        if "ml-kem-768" in a or "mlkem768" in a:
            return ALG_ML_KEM_768
        if "kyber768" in a or "kyber-768" in a:
            return ALG_KYBER768
        raise ValueError(f"Unknown/unsupported KEM alg: {alg}")


# --------------------------------------------------------------------------------------------------
# Manual smoke test
# --------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    print("[oqs_backend] available:", is_available())
    if not is_available():
        raise SystemExit(0)

    oqs = OQSBackend()

    # Signatures
    for alg in ("Dilithium3", "SPHINCS+-SHAKE-128s"):
        print(f" -- SIG alg: {alg}")
        sk, pk = oqs.sig_keypair(alg)
        msg = b"hello animica"
        sig = oqs.sig_sign(alg, msg, sk)
        ok = oqs.sig_verify(alg, msg, sig, pk)
        print("    sizes: pk", len(pk), "sk", len(sk), "sig", len(sig), "verify:", ok)

    # KEM
    for alg in ("ML-KEM-768", "Kyber768"):
        try:
            print(f" -- KEM alg: {alg}")
            sk, pk = oqs.kem_keypair(alg)
            ct, ss_b = oqs.kem_encapsulate(alg, pk)
            ss_a = oqs.kem_decapsulate(alg, sk, ct)
            print(
                "    sizes: pk",
                len(pk),
                "sk",
                len(sk),
                "ct",
                len(ct),
                "ss",
                len(ss_a),
                "match:",
                ss_a == ss_b,
            )
            break  # first that works is fine
        except Exception as e:
            print("    (skip) reason:", e)
            continue
