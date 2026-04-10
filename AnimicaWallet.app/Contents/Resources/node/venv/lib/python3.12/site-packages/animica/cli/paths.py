"""
Shared path resolution and directory creation helpers for Animica CLI.

Provides centralized functions to:
- Resolve ~/.animica paths consistently
- Auto-create directories with appropriate permissions
- Handle sensitive vs non-sensitive data directory permissions

Directory Permissions:
- Sensitive (wallets, keys): 0o700 (owner-only access)
- Non-sensitive (state, p2p, data): 0o755 (world-readable)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def get_animica_home() -> Path:
    """
    Get the Animica home directory.
    
    Returns ~/.animica by default, or $ANIMICA_HOME if set.
    """
    env_home = os.environ.get("ANIMICA_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".animica"


def ensure_dir(
    path: Path,
    *,
    mode: int = 0o755,
    parents: bool = True,
    exist_ok: bool = True,
) -> Path:
    """
    Ensure a directory exists, creating it if necessary with specified permissions.
    
    Args:
        path: Directory path to ensure exists
        mode: Permission mode (default 0o755 for non-sensitive, use 0o700 for sensitive)
        parents: Create parent directories if needed (default True)
        exist_ok: Don't raise if directory already exists (default True)
        
    Returns:
        The path that was ensured (for chaining)
        
    Note:
        On Windows or restricted filesystems, permission changes may be ignored.
    """
    path.mkdir(mode=mode, parents=parents, exist_ok=exist_ok)
    
    # Try to set permissions explicitly in case umask interfered
    try:
        path.chmod(mode)
    except (OSError, PermissionError):
        # Ignore permission errors (e.g., on Windows or restricted filesystems)
        pass
    
    return path


def ensure_animica_dir(
    subpath: Optional[str] = None,
    *,
    sensitive: bool = False,
) -> Path:
    """
    Ensure a subdirectory under ~/.animica exists with appropriate permissions.
    
    Args:
        subpath: Optional subdirectory path relative to ~/.animica (e.g., "keys", "p2p")
        sensitive: If True, use restrictive permissions (0o700) for owner-only access
        
    Returns:
        The resolved directory path
        
    Examples:
        ensure_animica_dir()                    # ~/.animica with 0o755
        ensure_animica_dir("keys", sensitive=True)  # ~/.animica/keys with 0o700
        ensure_animica_dir("p2p")               # ~/.animica/p2p with 0o755
    """
    base = get_animica_home()
    
    if subpath:
        path = base / subpath
    else:
        path = base
    
    mode = 0o700 if sensitive else 0o755
    return ensure_dir(path, mode=mode)


def ensure_file_dir(
    file_path: Path,
    *,
    sensitive: bool = False,
) -> Path:
    """
    Ensure the parent directory of a file exists with appropriate permissions.
    
    Args:
        file_path: File path whose parent directory should be ensured
        sensitive: If True, use restrictive permissions (0o700) for owner-only access
        
    Returns:
        The parent directory path
        
    Examples:
        ensure_file_dir(Path("~/.animica/wallets.json"), sensitive=True)
        ensure_file_dir(Path("~/.animica/p2p/peers.json"))
    """
    parent = file_path.parent
    mode = 0o700 if sensitive else 0o755
    return ensure_dir(parent, mode=mode)


def secure_file(path: Path, mode: int = 0o600) -> None:
    """
    Set secure permissions on a file.
    
    Args:
        path: File path to secure
        mode: Permission mode (default 0o600 for owner read/write only)
        
    Note:
        On Windows or restricted filesystems, permission changes may be ignored.
    """
    try:
        path.chmod(mode)
    except (OSError, PermissionError):
        # Ignore permission errors (e.g., on Windows or restricted filesystems)
        pass


__all__ = [
    "get_animica_home",
    "ensure_dir",
    "ensure_animica_dir",
    "ensure_file_dir",
    "secure_file",
]
