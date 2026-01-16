"""Utilities for detecting frozen/packaged execution and locating bundled resources."""

import os
import sys
from pathlib import Path
from typing import Optional


def is_frozen() -> bool:
    """Check if running as a PyInstaller frozen executable.
    
    Returns:
        True if running as frozen executable, False otherwise
    """
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def get_executable_dir() -> Path:
    """Get the directory containing the executable.
    
    Returns:
        Path to executable directory
    """
    if is_frozen():
        # PyInstaller sets sys.executable to the actual binary
        return Path(sys.executable).parent
    else:
        # Development mode: return the package directory
        return Path(__file__).parent.parent


def get_bundled_resources_dir() -> Path:
    """Get the directory containing bundled resources.
    
    In frozen mode:
        - macOS: Contents/Resources/ in the .app bundle
        - Linux/Windows: Same as executable directory
    
    In dev mode: Returns the app directory
    
    Returns:
        Path to resources directory
    """
    if is_frozen():
        if sys.platform == 'darwin':
            # macOS: sys.executable is at Contents/MacOS/AnimicaMinerGUI
            # Resources are at Contents/Resources/
            exe_dir = Path(sys.executable).parent
            resources_dir = exe_dir.parent / 'Resources'
            if resources_dir.exists():
                return resources_dir
            else:
                # Fallback if directory structure is different
                return exe_dir
        else:
            # Linux/Windows: Resources are bundled with the executable
            # PyInstaller extracts to a temp directory (_MEIPASS)
            if hasattr(sys, '_MEIPASS'):
                return Path(sys._MEIPASS)
            else:
                return get_executable_dir()
    else:
        # Development mode
        return Path(__file__).parent.parent.parent


def get_bundled_bin_path(binary_name: str) -> Optional[Path]:
    """Get the path to a bundled binary (e.g., animica-node).
    
    Searches in these locations (in order):
    1. Frozen mode (macOS): Contents/Resources/bin/{binary_name}
    2. Frozen mode (Linux/Windows): {_MEIPASS}/bin/{binary_name}
    3. Dev mode: repo_root/dist/{binary_name}
    4. Dev mode: sys.executable (if animica CLI is on PATH)
    
    Args:
        binary_name: Name of the binary (e.g., "animica-node")
    
    Returns:
        Path to binary if found, None otherwise
    """
    if is_frozen():
        # Frozen mode: Look for bundled binary
        resources = get_bundled_resources_dir()
        
        # Check in bin/ subdirectory
        bin_path = resources / 'bin' / binary_name
        if bin_path.exists() and bin_path.is_file():
            return bin_path
        
        # Check directly in resources directory (fallback)
        bin_path = resources / binary_name
        if bin_path.exists() and bin_path.is_file():
            return bin_path
        
        return None
    else:
        # Development mode: Look in standard locations
        
        # Try repo dist/ directory
        repo_root = Path(__file__).parent.parent.parent.parent
        dist_path = repo_root / 'dist' / binary_name
        if dist_path.exists() and dist_path.is_file():
            return dist_path
        
        # Try PATH (for system-installed animica)
        import shutil
        path_binary = shutil.which(binary_name)
        if path_binary:
            return Path(path_binary)
        
        # Try .venv/bin (for dev installs)
        venv_path = repo_root / '.venv' / 'bin' / binary_name
        if venv_path.exists() and venv_path.is_file():
            return venv_path
        
        return None


def should_use_bundled_node() -> bool:
    """Determine if we should use a bundled node binary.
    
    Returns:
        True if frozen (always use bundled), False otherwise
    """
    return is_frozen()


def get_python_executable() -> str:
    """Get the Python executable to use for running Python modules.
    
    Returns:
        Path to Python executable
        
    Raises:
        RuntimeError: If frozen and Python execution is attempted
    """
    if is_frozen():
        raise RuntimeError(
            "Cannot execute Python modules in frozen mode. "
            "Use bundled binaries instead."
        )
    return sys.executable
