"""Main entry point for the Animica GUI Miner application."""

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

logger = logging.getLogger(__name__)


def main() -> int:
    """Main entry point for the GUI miner."""
    try:
        # Ensure config directory exists
        from animica_miner_gui.backend.config import get_default_config_dir
        config_dir = get_default_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)
        
        # Create Qt application
        app = QApplication(sys.argv)
        app.setApplicationName("Animica Miner")
        app.setOrganizationName("Animica")
        app.setOrganizationDomain("animica.org")
        
        # Import UI components
        from animica_miner_gui.ui.main_window import MainWindow
        from animica_miner_gui.ui.wizard import FirstRunWizard
        from animica_miner_gui.backend.config import get_default_config_path
        
        # Check if this is first run
        config_path = get_default_config_path()
        if not config_path.exists():
            logger.info("First run detected, showing setup wizard")
            wizard = FirstRunWizard()
            if wizard.exec() != wizard.DialogCode.Accepted:
                logger.info("Setup wizard cancelled, exiting")
                return 0
        
        # Show main window
        window = MainWindow()
        window.show()
        
        return app.exec()
    
    except Exception as e:
        logger.exception("Fatal error in main application")
        return 1


if __name__ == "__main__":
    sys.exit(main())
