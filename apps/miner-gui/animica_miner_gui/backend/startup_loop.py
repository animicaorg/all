"""Startup loop detection and breaking mechanism."""

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class StartupLoopBreaker:
    """Detects and breaks infinite startup loops.
    
    Writes a timestamped launch marker file. If the app launches too many
    times in a short period, it's likely caught in a spawn loop. In that
    case, refuse to auto-start anything and show a safe error dialog.
    """
    
    def __init__(self, config_dir: Path, max_launches: int = 5, window_seconds: float = 30.0):
        """Initialize the loop breaker.
        
        Args:
            config_dir: Directory to store state files
            max_launches: Maximum launches allowed in window
            window_seconds: Time window in seconds
        """
        self.config_dir = config_dir
        self.max_launches = max_launches
        self.window_seconds = window_seconds
        self.marker_file = config_dir / "launch_marker.txt"
    
    def check_and_record(self) -> bool:
        """Check if we're in a launch loop and record this launch.
        
        Returns:
            True if safe to continue, False if in a launch loop
        """
        now = time.time()
        
        # Read previous launches
        launches = self._read_launches()
        
        # Remove old launches outside the window
        cutoff = now - self.window_seconds
        recent_launches = [t for t in launches if t >= cutoff]
        
        # Add current launch
        recent_launches.append(now)
        
        # Write back
        self._write_launches(recent_launches)
        
        # Check if we're in a loop
        if len(recent_launches) > self.max_launches:
            logger.error(
                f"Startup loop detected: {len(recent_launches)} launches "
                f"in {self.window_seconds} seconds (max: {self.max_launches})"
            )
            return False
        
        return True
    
    def reset(self) -> None:
        """Reset the launch history (call after user confirms reset)."""
        if self.marker_file.exists():
            self.marker_file.unlink()
        logger.info("Startup loop history reset")
    
    def _read_launches(self) -> list[float]:
        """Read launch timestamps from marker file."""
        if not self.marker_file.exists():
            return []
        
        try:
            with open(self.marker_file, 'r') as f:
                lines = f.readlines()
            
            launches = []
            for line in lines:
                line = line.strip()
                if line:
                    try:
                        launches.append(float(line))
                    except ValueError:
                        pass
            
            return launches
        except Exception as e:
            logger.warning(f"Failed to read launch marker: {e}")
            return []
    
    def _write_launches(self, launches: list[float]) -> None:
        """Write launch timestamps to marker file."""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            with open(self.marker_file, 'w') as f:
                for t in launches:
                    f.write(f"{t}\n")
        except Exception as e:
            logger.warning(f"Failed to write launch marker: {e}")


def create_loop_breaker(config_dir: Optional[Path] = None) -> StartupLoopBreaker:
    """Create a startup loop breaker with default settings.
    
    Args:
        config_dir: Optional config directory override
    
    Returns:
        Configured StartupLoopBreaker instance
    """
    if config_dir is None:
        from animica_miner_gui.backend.config import get_default_config_dir
        config_dir = get_default_config_dir()
    
    return StartupLoopBreaker(
        config_dir=config_dir,
        max_launches=5,  # Allow 5 launches
        window_seconds=30.0  # In 30 seconds
    )
