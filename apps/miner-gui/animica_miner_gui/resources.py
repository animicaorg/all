"""Resource management for the Animica GUI Miner."""

import sys
from pathlib import Path
from typing import Optional


def get_logo_path() -> Optional[Path]:
    """Get the path to the application logo.
    
    Returns:
        Path to logo.png if it exists, None otherwise.
    """
    # Try using importlib.resources for Python 3.9+
    if sys.version_info >= (3, 9):
        try:
            from importlib.resources import files
            # Try to get logo.png from the package parent directory
            package_files = files('animica_miner_gui')
            logo_path = package_files.parent / "logo.png"
            if logo_path.exists():
                return Path(logo_path)
        except (ImportError, AttributeError, TypeError):
            pass
    
    # Fallback: try to find logo.png relative to this file
    package_dir = Path(__file__).parent.parent
    logo_path = package_dir / "logo.png"
    
    if logo_path.exists():
        return logo_path
    
    # If not found, return None
    return None
