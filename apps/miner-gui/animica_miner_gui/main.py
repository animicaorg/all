"""Main entry point for the Animica GUI Miner application."""

import logging
import multiprocessing
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

# Required for PyInstaller frozen executables on macOS/Windows to prevent
# infinite process spawning when using multiprocessing module.
# MUST be called at module level, not inside if __name__ == "__main__".
multiprocessing.freeze_support()

# Set up basic logging (will be enhanced with file logging in main)
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
        
        # Setup startup logging to file (for debugging launch issues)
        from animica_miner_gui.backend.startup_logging import setup_startup_logging, log_startup_stage
        log_file = setup_startup_logging(config_dir)
        log_startup_stage("Startup logging initialized")
        logger.info(f"Startup log: {log_file}")
        
        # Check for startup loop (defensive safety net)
        from animica_miner_gui.backend.startup_loop import create_loop_breaker
        loop_breaker = create_loop_breaker(config_dir)
        
        if not loop_breaker.check_and_record():
            # In a launch loop - show safe error dialog and exit
            logger.error("Startup loop detected, showing error dialog")
            
            # Create minimal Qt app just for the error dialog
            app = QApplication(sys.argv)
            
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Critical)
            msg.setWindowTitle("Startup Loop Detected")
            msg.setText(
                "Animica Miner GUI has detected that it is launching repeatedly. "
                "This may indicate a configuration issue."
            )
            msg.setInformativeText(
                "Click 'Reset Settings' to clear startup configuration and try again, "
                "or 'Exit' to quit the application."
            )
            reset_btn = msg.addButton("Reset Settings", QMessageBox.AcceptRole)
            exit_btn = msg.addButton("Exit", QMessageBox.RejectRole)
            msg.setDefaultButton(exit_btn)
            msg.exec()
            
            if msg.clickedButton() == reset_btn:
                # Reset startup loop history and auto-start settings
                loop_breaker.reset()
                logger.info("User chose to reset settings")
                
                # Try to disable auto-start in config
                try:
                    from animica_miner_gui.backend.config import load_config, save_config, get_default_config_path
                    config_path = get_default_config_path()
                    if config_path.exists():
                        config = load_config(config_path)
                        config.miner.auto_start = False
                        save_config(config, config_path)
                        logger.info("Disabled auto-start in config")
                except Exception as e:
                    logger.error(f"Failed to disable auto-start: {e}")
                
                # Show success message and exit (user can restart manually)
                info_msg = QMessageBox()
                info_msg.setIcon(QMessageBox.Information)
                info_msg.setWindowTitle("Settings Reset")
                info_msg.setText("Settings have been reset. Please restart the application.")
                info_msg.exec()
            
            return 0
        
        log_startup_stage("Startup loop check passed")
        
        # Create Qt application
        app = QApplication(sys.argv)
        app.setApplicationName("Animica Miner")
        app.setOrganizationName("Animica")
        app.setOrganizationDomain("animica.org")
        
        log_startup_stage("Qt application created")
        
        # Single instance guard (prevents multiple instances)
        from animica_miner_gui.backend.single_instance import SingleInstanceGuard
        guard = SingleInstanceGuard("animica.miner-gui")
        
        if not guard.check_and_acquire():
            # Another instance is already running
            logger.info("Another instance detected, exiting")
            log_startup_stage("Another instance running, exiting gracefully")
            return 0
        
        log_startup_stage("Single instance lock acquired")
        
        # Set application icon
        from animica_miner_gui.resources import get_logo_path
        logo_path = get_logo_path()
        if logo_path:
            app.setWindowIcon(QIcon(str(logo_path)))
        
        # Import UI components
        from animica_miner_gui.ui.main_window import MainWindow
        from animica_miner_gui.ui.wizard import FirstRunWizard
        from animica_miner_gui.backend.config import get_default_config_path
        
        # Check if this is first run
        config_path = get_default_config_path()
        if not config_path.exists():
            logger.info("First run detected, showing setup wizard")
            log_startup_stage("Showing first-run wizard")
            wizard = FirstRunWizard()
            if wizard.exec() != wizard.DialogCode.Accepted:
                logger.info("Setup wizard cancelled, exiting")
                log_startup_stage("Wizard cancelled, exiting")
                guard.release()
                return 0
        
        log_startup_stage("Creating main window")
        
        # Show main window
        window = MainWindow()
        
        # Connect single instance guard to raise window
        guard.raise_requested.connect(window.raise_)
        guard.raise_requested.connect(window.activateWindow)
        
        window.show()
        QTimer.singleShot(0, window.backend.ensureNodeRunning)
        
        log_startup_stage("Main window shown, entering event loop")
        
        result = app.exec()
        
        # Release single instance lock
        guard.release()
        
        return result
    
    except Exception as e:
        logger.exception("Fatal error in main application")
        try:
            app = QApplication.instance() or QApplication(sys.argv)
            msg = QMessageBox()
            msg.setIcon(QMessageBox.Critical)
            msg.setWindowTitle("Animica Miner Error")
            msg.setText("A fatal error occurred while starting Animica Miner.")
            msg.setInformativeText(str(e))
            msg.exec()
        except Exception:
            logger.exception("Failed to show fatal error dialog")
        return 1


if __name__ == "__main__":
    sys.exit(main())
