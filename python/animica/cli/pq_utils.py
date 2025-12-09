"""
Utility functions for checking post-quantum cryptography dependencies.

This module provides runtime checks for PQ signing libraries (python-oqs/liboqs)
and displays helpful error messages when dependencies are missing.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple


def check_pq_signing_available() -> Tuple[bool, Optional[str]]:
    """
    Check if PQ signing is available (production mode).
    
    Returns:
        (available, error_message)
        - available: True if python-oqs is available, False otherwise
        - error_message: Helpful error message if not available, None if available
    """
    # Check if unsafe fake mode is enabled (should not be used in production)
    if os.environ.get("ANIMICA_UNSAFE_PQ_FAKE", "") == "1":
        return True, None  # Available but using unsafe mode
    
    # Try to import python-oqs
    try:
        import oqs  # type: ignore
        
        # Check if SPHINCS+ is enabled
        # Note: fallback lambda returns empty list if method doesn't exist,
        # resulting in an empty set and no SPHINCS+ variants found
        enabled = set(getattr(oqs, "get_enabled_sig_mechanisms", lambda: [])())
        sphincs_variants = [
            "SPHINCS+-SHAKE-128s",
            "SPHINCS+-SHAKE-128s-robust",
            "SPHINCS+-shake-128s",
            "SPHINCS+-shake-128s-robust",
        ]
        
        if any(variant in enabled for variant in sphincs_variants):
            return True, None
        else:
            return False, (
                "python-oqs is installed but SPHINCS+-SHAKE-128s is not enabled.\n"
                "This may indicate an incomplete liboqs installation."
            )
    except ImportError:
        return False, None


def get_pq_missing_error_message() -> str:
    """
    Get a helpful error message for missing PQ dependencies.
    
    Returns:
        Formatted error message with installation instructions
    """
    return """
Error: Post-quantum signing dependencies not available.

Transaction signing requires python-oqs with liboqs support.

To install:

1. Install liboqs:
   • Ubuntu/Debian: sudo apt-get install liboqs-dev
   • macOS: brew install liboqs
   • Or build from source: https://github.com/open-quantum-safe/liboqs

2. Install python-oqs:
   python -m pip install python-oqs

3. Verify installation:
   python -c "import oqs; mechs = oqs.get_enabled_sig_mechanisms(); print('Available SPHINCS+ variants:', [m for m in mechs if 'SPHINCS' in m])"

Note: For development/testing only, you can set ANIMICA_UNSAFE_PQ_FAKE=1,
but this is NOT secure and should NEVER be used in production.
"""


def ensure_pq_signing_or_exit() -> None:
    """
    Check if PQ signing is available and exit with helpful message if not.
    
    This is a convenience function for CLI commands that require PQ signing.
    """
    import sys
    
    available, specific_error = check_pq_signing_available()
    if not available:
        print(get_pq_missing_error_message(), file=sys.stderr)
        if specific_error:
            print(f"\nAdditional info: {specific_error}", file=sys.stderr)
        sys.exit(1)
