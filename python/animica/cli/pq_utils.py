"""
Utility functions for checking post-quantum cryptography dependencies.

This module provides runtime checks for PQ signing libraries (liboqs-python/liboqs)
and displays helpful error messages when dependencies are missing.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Recommended liboqs version for installation
RECOMMENDED_LIBOQS_VERSION = "0.15.0"


def check_pq_signing_available() -> Tuple[bool, Optional[str]]:
    """
    Check if PQ signing is available (production mode).
    
    Uses centralized capability detection from pq.py.capability module.
    This function performs detection once and caches the result.
    
    Returns:
        (available, error_message)
        - available: True if liboqs-python is available, False otherwise
        - error_message: Helpful error message if not available, None if available
    """
    # Use centralized capability detection
    try:
        from pq.py.capability import get_capability
        
        cap = get_capability()
        
        if cap.available:
            # PQ is available - no error
            return True, None
        else:
            # PQ not available - return False with no specific error
            # (the capability module already logged details)
            return False, None
            
    except ImportError:
        # Fallback if capability module not available
        # This is for backward compatibility during transition
        logger.debug("pq.py.capability module not available, using legacy detection")
        
        # Check if unsafe fake mode is enabled (should not be used in production)
        if os.environ.get("ANIMICA_UNSAFE_PQ_FAKE", "") == "1":
            logger.warning("Using ANIMICA_UNSAFE_PQ_FAKE=1 mode (NOT SECURE - development only)")
            return True, None  # Available but using unsafe mode
        
        # Try to import liboqs-python (provides 'oqs' module)
        try:
            import oqs  # type: ignore
            
            # Log the version if available
            version = getattr(oqs, "__version__", "unknown")
            logger.info(f"✓ liboqs-python detected: version {version}")
            
            # Check if any mechanisms are enabled
            enabled = set(getattr(oqs, "get_enabled_sig_mechanisms", lambda: [])())
            
            # Log all available signature mechanisms for debugging
            logger.debug(f"Available signature mechanisms: {sorted(enabled)}")
            
            if enabled:
                logger.info("✓ PQ signature support confirmed")
                return True, None
            else:
                logger.error("✗ liboqs-python is installed but no signature mechanisms enabled")
                return False, (
                    "liboqs-python is installed but no signature mechanisms are enabled.\n"
                    "This may indicate an incomplete liboqs installation."
                )
        except ImportError as e:
            logger.debug(f"liboqs-python (oqs module) not available: {e}")
            
            # Also check if oqs_backend can load liboqs directly via ctypes
            try:
                from pq.py.algs import oqs_backend
                if oqs_backend.is_available():
                    version = oqs_backend.get_version_info()
                    logger.info(f"✓ liboqs loaded via ctypes backend: version {version or 'unknown'}")
                    logger.info("Note: Using ctypes backend. For best compatibility, install python-oqs.")
                    return True, None
                else:
                    logger.debug("oqs_backend reported liboqs not available")
            except Exception as backend_err:
                logger.debug(f"Could not check oqs_backend: {backend_err}")
            
            return False, None


def get_pq_diagnostics() -> str:
    """
    Get detailed diagnostic information about PQ library availability.
    
    Uses centralized capability detection when available.
    
    Returns:
        Formatted diagnostic information
    """
    # Try to use centralized capability diagnostics
    try:
        from pq.py.capability import get_diagnostics
        return get_diagnostics()
    except ImportError:
        # Fallback to legacy diagnostics
        pass
    
    # Legacy diagnostics (for backward compatibility)
    diagnostics = ["PQ Library Diagnostics", "=" * 50]
    
    # Check python-oqs (oqs module)
    try:
        import oqs  # type: ignore
        version = getattr(oqs, "__version__", "unknown")
        diagnostics.append(f"✓ python-oqs (oqs module): installed (version {version})")
        
        # Check enabled mechanisms
        enabled = set(getattr(oqs, "get_enabled_sig_mechanisms", lambda: [])())
        sphincs_available = any("SPHINCS" in m for m in enabled)
        if sphincs_available:
            sphincs_mechs = [m for m in enabled if "SPHINCS" in m]
            diagnostics.append(f"  ✓ SPHINCS+ mechanisms: {', '.join(sphincs_mechs)}")
        else:
            diagnostics.append("  ✗ SPHINCS+ mechanisms: not available")
            
        # Try to locate oqs module
        try:
            import importlib.util
            spec = importlib.util.find_spec("oqs")
            if spec and spec.origin:
                diagnostics.append(f"  Module location: {spec.origin}")
        except Exception:
            pass
    except ImportError:
        diagnostics.append("✗ python-oqs (oqs module): not installed")
    
    # Check oqs_backend (ctypes direct loading)
    try:
        from pq.py.algs import oqs_backend
        if oqs_backend.is_available():
            version = oqs_backend.get_version_info()
            diagnostics.append(f"✓ liboqs (ctypes backend): loaded (version {version or 'unknown'})")
        else:
            diagnostics.append("✗ liboqs (ctypes backend): not loaded")
    except Exception as e:
        diagnostics.append(f"✗ liboqs (ctypes backend): error - {e}")
    
    # Environment variables
    diagnostics.append("\nEnvironment Variables:")
    env_vars = {
        "ANIMICA_PQ_MECHANISM": os.environ.get("ANIMICA_PQ_MECHANISM"),
        "LD_LIBRARY_PATH": os.environ.get("LD_LIBRARY_PATH"),
        "DYLD_LIBRARY_PATH": os.environ.get("DYLD_LIBRARY_PATH"),
        "LIBRARY_PATH": os.environ.get("LIBRARY_PATH"),
        "LIBOQS_PATH": os.environ.get("LIBOQS_PATH"),
        "ANIMICA_UNSAFE_PQ_FAKE": os.environ.get("ANIMICA_UNSAFE_PQ_FAKE"),
    }
    
    for var, value in env_vars.items():
        if value:
            diagnostics.append(f"  {var}: {value}")
        else:
            diagnostics.append(f"  {var}: (not set)")
    
    return "\n".join(diagnostics)


def get_pq_missing_error_message() -> str:
    """
    Get a helpful error message for missing PQ dependencies.
    
    Returns:
        Formatted error message with installation instructions
    """
    # Check if liboqs library path environment variables are set
    ld_lib_path = os.environ.get("LD_LIBRARY_PATH", "")
    dyld_lib_path = os.environ.get("DYLD_LIBRARY_PATH", "")
    lib_path = os.environ.get("LIBRARY_PATH", "")
    liboqs_path = os.environ.get("LIBOQS_PATH", "")
    
    env_info = ""
    if any([ld_lib_path, dyld_lib_path, lib_path, liboqs_path]):
        env_info = "\nCurrent library path environment variables:\n"
        if ld_lib_path:
            env_info += f"  LD_LIBRARY_PATH: {ld_lib_path}\n"
        if dyld_lib_path:
            env_info += f"  DYLD_LIBRARY_PATH: {dyld_lib_path}\n"
        if lib_path:
            env_info += f"  LIBRARY_PATH: {lib_path}\n"
        if liboqs_path:
            env_info += f"  LIBOQS_PATH: {liboqs_path}\n"
    
    # Include diagnostics
    try:
        diag = get_pq_diagnostics()
        diagnostic_section = f"\n{diag}\n"
    except Exception as e:
        # Log but don't fail if diagnostics can't be generated
        logger.debug(f"Could not generate diagnostics: {e}")
        diagnostic_section = ""
    
    return f"""
Error: Post-quantum signing dependencies not available.

Transaction signing requires liboqs-python (python-oqs) with liboqs support.

To install:

1. Install liboqs (recommended version: v{RECOMMENDED_LIBOQS_VERSION} or later):
   • Ubuntu/Debian: sudo apt-get install liboqs-dev
   • macOS: brew install liboqs
   • From source: https://github.com/open-quantum-safe/liboqs/releases/tag/{RECOMMENDED_LIBOQS_VERSION}

2. Install liboqs-python:
   python -m pip install liboqs-python

3. If you built liboqs from source, ensure library paths are set:
   • Linux: export LD_LIBRARY_PATH=/path/to/liboqs/lib:$LD_LIBRARY_PATH
   • macOS: export DYLD_LIBRARY_PATH=/path/to/liboqs/lib:$DYLD_LIBRARY_PATH
   • Or source the setup script: source .liboqs/env.sh (if using setup.sh)

4. Verify installation:
   python -c "import oqs; mechs = oqs.get_enabled_sig_mechanisms(); print('Available SPHINCS+ variants:', [m for m in mechs if 'SPHINCS' in m])"
{diagnostic_section}{env_info}
Note: For development/testing only, you can set ANIMICA_UNSAFE_PQ_FAKE=1,
but this is NOT secure and should NEVER be used in production.
"""


def ensure_pq_signing_or_exit() -> None:
    """
    Check if PQ signing is available and exit with helpful message if not.
    
    This is a convenience function for CLI commands that require PQ signing.
    """
    available, specific_error = check_pq_signing_available()
    if not available:
        print(get_pq_missing_error_message(), file=sys.stderr)
        if specific_error:
            print(f"\nAdditional info: {specific_error}", file=sys.stderr)
        sys.exit(1)
