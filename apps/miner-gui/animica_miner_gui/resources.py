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
            # Get logo.png from within the package
            package_files = files('animica_miner_gui')
            logo_path = package_files / "logo.png"
            if hasattr(logo_path, 'exists') and logo_path.exists():
                return Path(str(logo_path))
        except (ImportError, AttributeError, TypeError):
            pass
    
    # Fallback: try to find logo.png next to the package directory
    # This works in development mode where logo.png is at the repo root
    package_dir = Path(__file__).parent
    
    # First try inside the package (for wheel installations)
    logo_in_package = package_dir / "logo.png"
    if logo_in_package.exists():
        return logo_in_package
    
    # Then try in parent directory (for development mode)
    logo_in_parent = package_dir.parent / "logo.png"
    if logo_in_parent.exists():
        return logo_in_parent
    
    # If not found, return None
    return None
