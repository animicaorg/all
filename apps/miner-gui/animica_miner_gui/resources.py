"""Resource management for the Animica GUI Miner."""

from pathlib import Path
from typing import Optional


def get_logo_path() -> Optional[Path]:
    """Get the path to the application logo.
    
    Returns:
        Path to logo.png if it exists, None otherwise.
    """
    # Try to find logo.png relative to the package
    # First, try relative to this file (in the package)
    package_dir = Path(__file__).parent.parent
    logo_path = package_dir / "logo.png"
    
    if logo_path.exists():
        return logo_path
    
    # If not found, return None
    return None
