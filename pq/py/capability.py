"""
PQ capability detection and configuration.

This module provides centralized capability detection for post-quantum cryptography
with caching and graceful fallback. It supports:

- Runtime detection of available PQ mechanisms
- Configurable default mechanism via ANIMICA_PQ_MECHANISM environment variable
- Cached detection results to avoid repeated checks
- Graceful fallback when PQ libraries are not available
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Cache for capability detection
_CAPABILITY_CACHE: Optional["PQCapability"] = None
_CAPABILITY_CHECKED = False


@dataclass(frozen=True)
class PQCapability:
    """Post-quantum capability information."""
    
    available: bool
    """Whether PQ signing/KEM is available at all."""
    
    sig_mechanisms: Set[str]
    """Available signature mechanisms (e.g., ML-DSA-65, SPHINCS+-SHAKE-128s-simple)."""
    
    kem_mechanisms: Set[str]
    """Available KEM mechanisms (e.g., ML-KEM-768, Kyber768)."""
    
    default_sig_mechanism: Optional[str]
    """Default signature mechanism to use."""
    
    provider: str
    """Provider name: 'oqs', 'oqs_backend', 'fake', or 'none'."""
    
    version: Optional[str]
    """Library version if available."""


def _detect_oqs_module() -> Optional[PQCapability]:
    """
    Try to detect capability via python-oqs (oqs module).
    
    Returns:
        PQCapability if successful, None otherwise
    """
    try:
        import oqs  # type: ignore
        
        version = getattr(oqs, "__version__", "unknown")
        
        # Get enabled mechanisms
        sig_mechs = set(getattr(oqs, "get_enabled_sig_mechanisms", lambda: [])())
        kem_mechs = set(getattr(oqs, "get_enabled_kem_mechanisms", lambda: [])())
        
        if not sig_mechs and not kem_mechs:
            logger.debug("python-oqs imported but no mechanisms available")
            return None
        
        # Determine default signature mechanism
        default_sig = _select_default_sig_mechanism(sig_mechs)
        
        logger.info(f"✓ python-oqs detected: version {version}")
        logger.debug(f"Available sig mechanisms: {sorted(sig_mechs)}")
        logger.debug(f"Available KEM mechanisms: {sorted(kem_mechs)}")
        logger.info(f"✓ Default signature mechanism: {default_sig}")
        
        return PQCapability(
            available=True,
            sig_mechanisms=sig_mechs,
            kem_mechanisms=kem_mechs,
            default_sig_mechanism=default_sig,
            provider="oqs",
            version=version,
        )
    except ImportError as e:
        logger.debug(f"python-oqs (oqs module) not available: {e}")
        return None
    except Exception as e:
        logger.debug(f"Error detecting python-oqs: {e}")
        return None


def _detect_oqs_backend() -> Optional[PQCapability]:
    """
    Try to detect capability via ctypes oqs_backend.
    
    Returns:
        PQCapability if successful, None otherwise
    """
    try:
        from .algs import oqs_backend
        
        if not oqs_backend.is_available():
            logger.debug("oqs_backend reported liboqs not available")
            return None
        
        version = oqs_backend.get_version_info()
        
        # Try to instantiate backend to get mechanisms
        try:
            backend = oqs_backend.OQSBackend()
            sig_mechs = set(backend.sig_available_names())
            kem_mechs = set(backend.kem_available_names())
        except Exception as e:
            logger.debug(f"Could not instantiate OQSBackend: {e}")
            # Assume basic mechanisms are available
            sig_mechs = {"ML-DSA-65", "SPHINCS+-SHAKE-128s-simple"}
            kem_mechs = {"ML-KEM-768"}
        
        default_sig = _select_default_sig_mechanism(sig_mechs)
        
        logger.info(f"✓ liboqs loaded via ctypes backend: version {version or 'unknown'}")
        logger.debug(f"Available sig mechanisms: {sorted(sig_mechs)}")
        logger.info(f"✓ Default signature mechanism: {default_sig}")
        
        return PQCapability(
            available=True,
            sig_mechanisms=sig_mechs,
            kem_mechanisms=kem_mechs,
            default_sig_mechanism=default_sig,
            provider="oqs_backend",
            version=version,
        )
    except Exception as e:
        logger.debug(f"Could not check oqs_backend: {e}")
        return None


def _detect_fake_fallback() -> Optional[PQCapability]:
    """
    Check if unsafe fake mode is enabled.
    
    Returns:
        PQCapability if enabled, None otherwise
    """
    if os.environ.get("ANIMICA_UNSAFE_PQ_FAKE", "") == "1":
        logger.warning("Using ANIMICA_UNSAFE_PQ_FAKE=1 mode (NOT SECURE - development only)")
        
        # Fake mode supports basic mechanisms
        sig_mechs = {"dilithium3", "sphincs_shake_128s"}
        kem_mechs = {"kyber768"}
        
        return PQCapability(
            available=True,
            sig_mechanisms=sig_mechs,
            kem_mechanisms=kem_mechs,
            default_sig_mechanism="dilithium3",
            provider="fake",
            version="fake",
        )
    return None


def _select_default_sig_mechanism(available_mechs: Set[str]) -> Optional[str]:
    """
    Select the default signature mechanism from available mechanisms.
    
    Priority:
    1. ANIMICA_PQ_MECHANISM environment variable (if set and available)
    2. ML-DSA-65 (NIST standard, liboqs 0.15.x+)
    3. ML-DSA-87 (higher security level)
    4. ML-DSA-44 (lower security level)
    5. Dilithium3 (legacy, liboqs < 0.15.x)
    6. SPHINCS+-SHAKE-128s-simple (hash-based, stateless)
    7. SPHINCS+-SHAKE-128s (older variant)
    8. First available mechanism
    
    Args:
        available_mechs: Set of available mechanism names
    
    Returns:
        Default mechanism name, or None if no mechanisms available
    """
    if not available_mechs:
        return None
    
    # Check for environment variable override
    env_mechanism = os.environ.get("ANIMICA_PQ_MECHANISM")
    if env_mechanism:
        # Normalize mechanism name (case-insensitive, flexible matching)
        env_norm = env_mechanism.strip().upper()
        for mech in available_mechs:
            mech_norm = mech.upper()
            if mech_norm == env_norm or mech_norm.replace("-", "").replace("_", "") == env_norm.replace("-", "").replace("_", ""):
                logger.info(f"Using mechanism from ANIMICA_PQ_MECHANISM: {mech}")
                return mech
        logger.warning(f"ANIMICA_PQ_MECHANISM={env_mechanism} not found in available mechanisms: {sorted(available_mechs)}")
    
    # Priority list for auto-detection
    priorities = [
        "ML-DSA-65",
        "ML-DSA-87",
        "ML-DSA-44",
        "Dilithium3",
        "SPHINCS+-SHAKE-128s-simple",
        "SPHINCS+-shake-128s-simple",
        "SPHINCS+-SHAKE-128s",
        "SPHINCS+-shake-128s",
    ]
    
    for candidate in priorities:
        if candidate in available_mechs:
            return candidate
    
    # Fallback to first available
    return sorted(available_mechs)[0]


def detect_capability() -> PQCapability:
    """
    Detect PQ capability with caching.
    
    This function performs capability detection in the following order:
    1. Check cache
    2. Try python-oqs (oqs module)
    3. Try ctypes oqs_backend
    4. Try unsafe fake mode (if enabled)
    5. Return unavailable capability
    
    The result is cached for subsequent calls.
    
    Returns:
        PQCapability with detection results
    """
    global _CAPABILITY_CACHE, _CAPABILITY_CHECKED
    
    if _CAPABILITY_CHECKED and _CAPABILITY_CACHE is not None:
        return _CAPABILITY_CACHE
    
    _CAPABILITY_CHECKED = True
    
    # Try detection methods in order
    for detector in [_detect_oqs_module, _detect_oqs_backend, _detect_fake_fallback]:
        result = detector()
        if result is not None:
            _CAPABILITY_CACHE = result
            return result
    
    # No PQ available
    logger.info("Post-quantum cryptography not available (no liboqs-python or liboqs library found)")
    logger.info("Using ed25519 fallback for non-PQ operations")
    
    _CAPABILITY_CACHE = PQCapability(
        available=False,
        sig_mechanisms=set(),
        kem_mechanisms=set(),
        default_sig_mechanism=None,
        provider="none",
        version=None,
    )
    
    return _CAPABILITY_CACHE


def get_capability() -> PQCapability:
    """
    Get cached PQ capability (calls detect_capability if not cached).
    
    Returns:
        PQCapability with detection results
    """
    return detect_capability()


def is_pq_available() -> bool:
    """
    Check if PQ signing/KEM is available.
    
    Returns:
        True if PQ is available, False otherwise
    """
    return get_capability().available


def get_default_sig_mechanism() -> Optional[str]:
    """
    Get the default signature mechanism.
    
    Returns:
        Default mechanism name, or None if PQ not available
    """
    return get_capability().default_sig_mechanism


def get_available_sig_mechanisms() -> Set[str]:
    """
    Get available signature mechanisms.
    
    Returns:
        Set of available mechanism names
    """
    return get_capability().sig_mechanisms


def reset_cache() -> None:
    """
    Reset capability cache (useful for testing).
    """
    global _CAPABILITY_CACHE, _CAPABILITY_CHECKED
    _CAPABILITY_CACHE = None
    _CAPABILITY_CHECKED = False


def get_diagnostics() -> str:
    """
    Get detailed diagnostic information about PQ capability.
    
    Returns:
        Formatted diagnostic information
    """
    cap = get_capability()
    
    lines = [
        "PQ Capability Diagnostics",
        "=" * 60,
        f"Available: {cap.available}",
        f"Provider: {cap.provider}",
        f"Version: {cap.version or 'N/A'}",
        "",
    ]
    
    if cap.available:
        lines.extend([
            f"Default Signature Mechanism: {cap.default_sig_mechanism}",
            "",
            "Available Signature Mechanisms:",
        ])
        for mech in sorted(cap.sig_mechanisms):
            lines.append(f"  • {mech}")
        
        if cap.kem_mechanisms:
            lines.extend([
                "",
                "Available KEM Mechanisms:",
            ])
            for mech in sorted(cap.kem_mechanisms):
                lines.append(f"  • {mech}")
    else:
        lines.extend([
            "PQ cryptography is not available.",
            "",
            "To enable PQ:",
            "  1. Install liboqs-dev (apt/brew) or build from source",
            "  2. Install python-oqs: pip install liboqs-python",
            "  3. Set library paths if needed (LD_LIBRARY_PATH/DYLD_LIBRARY_PATH)",
            "  4. Or set LIBOQS_PATH=/path/to/liboqs.so",
            "",
            "For development only:",
            "  export ANIMICA_UNSAFE_PQ_FAKE=1  (NOT SECURE)",
        ])
    
    # Environment variables
    lines.extend([
        "",
        "Environment Variables:",
    ])
    
    env_vars = {
        "ANIMICA_PQ_MECHANISM": os.environ.get("ANIMICA_PQ_MECHANISM"),
        "ANIMICA_UNSAFE_PQ_FAKE": os.environ.get("ANIMICA_UNSAFE_PQ_FAKE"),
        "LIBOQS_PATH": os.environ.get("LIBOQS_PATH"),
        "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH"),
        "DYLD_LIBRARY_PATH": os.environ.get("DYLD_LIBRARY_PATH"),
    }
    
    for var, value in env_vars.items():
        if value:
            # Truncate long paths for readability
            display_value = value if len(value) < 80 else value[:77] + "..."
            lines.append(f"  {var}: {display_value}")
        else:
            lines.append(f"  {var}: (not set)")
    
    return "\n".join(lines)


if __name__ == "__main__":
    # Simple test when run directly
    print(get_diagnostics())
