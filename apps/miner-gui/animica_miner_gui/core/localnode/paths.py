"""Binary and path resolution for local node.

Handles finding the animica node binary in both development and packaged modes,
with proper macOS .app bundle support.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def is_frozen() -> bool:
    """Check if running as a frozen/packaged executable."""
    return getattr(sys, 'frozen', False)


def get_bundle_dir() -> Optional[Path]:
    """Get the bundle directory for macOS .app or other packaged formats.
    
    Returns:
        Path to the bundle directory, or None if not in a bundle
    """
    if not is_frozen():
        return None
    
    # On macOS .app bundle, sys.executable is AnimicaMiner.app/Contents/MacOS/AnimicaMiner
    # The binary should be at AnimicaMiner.app/Contents/Resources/bin/
    exe_path = Path(sys.executable).resolve()
    
    # Check for macOS .app structure
    if exe_path.parent.name == "MacOS" and exe_path.parent.parent.name == "Contents":
        resources_dir = exe_path.parent.parent / "Resources"
        if resources_dir.exists():
            return resources_dir

    # PyInstaller sets sys._MEIPASS to the temporary extraction directory
    if hasattr(sys, '_MEIPASS'):
        return Path(sys._MEIPASS)
    
    # For other platforms, use the executable directory
    return exe_path.parent


def get_bundled_node_dir() -> Optional[Path]:
    """Get the bundled node directory (PyInstaller onedir layout)."""
    bundle_dir = get_bundle_dir()
    if not bundle_dir:
        return None

    node_dir = bundle_dir / "node" / "animica-node"
    if node_dir.exists():
        return node_dir

    return None


def _find_libpython(internal_dir: Path) -> Optional[Path]:
    if sys.platform == "darwin":
        matches = list(internal_dir.glob("libpython*.dylib"))
    else:
        matches = list(internal_dir.glob("libpython*.so*"))
    return matches[0] if matches else None


def validate_bundled_node_layout() -> tuple[bool, str]:
    """Validate bundled node layout and return (ok, message)."""
    if not is_frozen():
        return True, ""

    node_dir = get_bundled_node_dir()
    if node_dir is None:
        return False, "Node bundle directory not found in app resources."

    node_binary = node_dir / "animica-node"
    if not node_binary.exists():
        return False, f"Node binary not found at {node_binary}"

    internal_dir = node_dir / "_internal"
    if not internal_dir.exists():
        return False, f"Node runtime folder missing at {internal_dir}"

    libpython = _find_libpython(internal_dir)
    if libpython is None:
        return False, f"Node runtime missing libpython in {internal_dir}"

    return True, ""


def get_repo_root() -> Path:
    """Get repository root directory (dev mode only)."""
    # This file is at apps/miner-gui/animica_miner_gui/core/localnode/paths.py
    return Path(__file__).resolve().parents[5]


def resolve_node_binary() -> Optional[Path]:
    """Resolve the animica node binary path.
    
    Priority:
    1. Packaged mode: look in bundle's bin/ directory
    2. Dev mode: look for dist/animica-node or built binary
    3. Dev mode fallback: use Python module (python -m rpc)
    
    Returns:
        Path to node binary or None if not found
        
    Raises:
        FileNotFoundError: If no suitable binary can be found
    """
    if is_frozen():
        # Packaged mode - look in bundle
        node_dir = get_bundled_node_dir()
        if node_dir:
            binary_path = node_dir / "animica-node"
            if binary_path.exists() and os.access(binary_path, os.X_OK):
                logger.info(f"Found node binary in bundle: {binary_path}")
                return binary_path

        # Fallback to legacy bin location
        bundle_dir = get_bundle_dir()
        if bundle_dir:
            for binary_name in ['animica-node', 'animicad', 'animica']:
                binary_path = bundle_dir / 'bin' / binary_name
                if binary_path.exists() and os.access(binary_path, os.X_OK):
                    logger.info(f"Found node binary in bundle: {binary_path}")
                    return binary_path

        # Also check next to the executable
        exe_dir = Path(sys.executable).parent
        for binary_name in ['animica-node', 'animicad', 'animica']:
            binary_path = exe_dir / binary_name
            if binary_path.exists() and os.access(binary_path, os.X_OK):
                logger.info(f"Found node binary next to executable: {binary_path}")
                return binary_path

        raise FileNotFoundError(
            "Node binary not found in packaged application. "
            "The application bundle may be incomplete or corrupted."
        )
    
    else:
        # Dev mode - look for built binary or use Python module
        repo_root = get_repo_root()
        
        # Check dist directory for built binary (onedir)
        dist_dir = repo_root / 'dist'
        if dist_dir.exists():
            for binary_name in ['animica-node', 'animicad', 'animica']:
                onedir_path = dist_dir / 'animica-node' / binary_name
                if onedir_path.exists() and os.access(onedir_path, os.X_OK):
                    logger.info(f"Found node binary in dist: {onedir_path}")
                    return onedir_path

                flat_path = dist_dir / binary_name
                if flat_path.exists() and os.access(flat_path, os.X_OK):
                    logger.info(f"Found node binary in dist: {flat_path}")
                    return flat_path
        
        # Check if we can run via Python module
        # Verify that 'rpc' module is available (the node is started via python -m rpc)
        try:
            import rpc
            logger.info("Using Python module for node (python -m rpc)")
            # Return None to indicate we'll use Python module mode
            return None
        except ImportError:
            pass
        
        raise FileNotFoundError(
            "Node binary not found. Please build the project first:\n"
            "  cd /path/to/animica && make build\n"
            "Or ensure the 'rpc' Python module is available."
        )


def resolve_cli_binary() -> Optional[Path]:
    """Resolve the animica CLI binary path.
    
    Returns:
        Path to CLI binary or None if using Python module
    """
    if is_frozen():
        # Packaged mode - look in bundle
        bundle_dir = get_bundle_dir()
        if bundle_dir:
            for binary_name in ['animica-cli', 'animica']:
                binary_path = bundle_dir / 'bin' / binary_name
                if binary_path.exists() and os.access(binary_path, os.X_OK):
                    logger.info(f"Found CLI binary in bundle: {binary_path}")
                    return binary_path
        
        # Check next to executable
        exe_dir = Path(sys.executable).parent
        for binary_name in ['animica-cli', 'animica']:
            binary_path = exe_dir / binary_name
            if binary_path.exists() and os.access(binary_path, os.X_OK):
                logger.info(f"Found CLI binary next to executable: {binary_path}")
                return binary_path
        
        # In packaged mode without CLI binary, we can't run CLI commands
        logger.warning("CLI binary not found in packaged application")
        return None
    
    else:
        # Dev mode - check for built binary or use Python module
        repo_root = get_repo_root()
        
        dist_dir = repo_root / 'dist'
        if dist_dir.exists():
            for binary_name in ['animica-cli', 'animica']:
                binary_path = dist_dir / binary_name
                if binary_path.exists() and os.access(binary_path, os.X_OK):
                    logger.info(f"Found CLI binary in dist: {binary_path}")
                    return binary_path
        
        # Use Python module
        logger.info("Using Python module for CLI (python -m animica)")
        return None


def get_data_directory(network: str = "devnet") -> Path:
    """Get the data directory for node data.
    
    Args:
        network: Network name (mainnet, testnet, devnet)
    
    Returns:
        Path to data directory
    """
    base = Path.home() / ".animica"
    
    # Map network to chain ID
    chain_id_map = {
        "mainnet": 1,
        "testnet": 2,
        "devnet": 1337,
    }
    chain_id = chain_id_map.get(network, 1337)
    
    # Use chain-{id} directory structure
    data_dir = base / f"chain-{chain_id}"
    data_dir.mkdir(parents=True, exist_ok=True)
    
    return data_dir


def get_log_directory() -> Path:
    """Get the directory for node logs.
    
    Returns:
        Path to log directory
    """
    log_dir = Path.home() / ".animica" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_rpc_token_path() -> Path:
    """Get the path to the RPC auth token file.
    
    Returns:
        Path to token file
    """
    token_dir = Path.home() / ".animica" / "gui-miner"
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir / "rpc-token"
