"""Startup logging to help diagnose launch issues."""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


def setup_startup_logging(config_dir: Optional[Path] = None) -> Path:
    """Setup detailed startup logging to a file in the app data directory.
    
    Logs startup information including:
    - Timestamp, PID, PPID
    - sys.executable, sys.frozen status
    - Command-line arguments
    - PyInstaller _MEIPASS (if frozen)
    - Startup stage markers
    
    Args:
        config_dir: Optional config directory override
    
    Returns:
        Path to the log file
    """
    if config_dir is None:
        from animica_miner_gui.backend.config import get_default_config_dir
        config_dir = get_default_config_dir()
    
    # Create logs directory
    logs_dir = config_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Create log file with date
    timestamp = datetime.now().strftime("%Y%m%d")
    log_file = logs_dir / f"startup-{timestamp}.log"
    
    # Setup file handler
    file_handler = logging.FileHandler(log_file, mode='a')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    
    # Add to root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    
    # Log startup information
    startup_logger = logging.getLogger("startup")
    startup_logger.info("=" * 80)
    startup_logger.info("Application startup")
    startup_logger.info(f"Timestamp: {datetime.now().isoformat()}")
    startup_logger.info(f"PID: {os.getpid()}")
    startup_logger.info(f"PPID: {os.getppid()}")
    startup_logger.info(f"sys.executable: {sys.executable}")
    startup_logger.info(f"sys.frozen: {getattr(sys, 'frozen', False)}")
    startup_logger.info(f"sys._MEIPASS: {getattr(sys, '_MEIPASS', 'N/A')}")
    startup_logger.info(f"sys.argv: {sys.argv}")
    startup_logger.info(f"Platform: {sys.platform}")
    startup_logger.info(f"Python version: {sys.version}")
    
    # Log environment variables that might affect behavior
    for var in ['PYTHONPATH', 'PATH', 'HOME', 'USER']:
        value = os.environ.get(var, 'N/A')
        # Truncate long values
        if len(value) > 200:
            value = value[:200] + "... (truncated)"
        startup_logger.info(f"{var}: {value}")
    
    return log_file


def log_startup_stage(stage: str) -> None:
    """Log a startup stage marker.
    
    Args:
        stage: Description of the startup stage
    """
    logger = logging.getLogger("startup")
    logger.info(f"STAGE: {stage}")
