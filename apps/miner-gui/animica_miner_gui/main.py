"""Main entry point for the Animica GUI Miner application."""

import logging
import multiprocessing
import os
import platform
import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from animica_miner_gui.backend.app_paths import (
    get_node_executable,
    get_node_payload_dir,
    get_node_token_path,
    get_resources_dir,
    get_startup_log_path,
    get_logs_dir,
    get_last_crash_log,
)
from animica_miner_gui.backend.crash_reporter import clear_crash_marker, install_exception_hooks, load_last_crash
from animica_miner_gui.backend.single_instance import SingleInstance


def _setup_logging() -> None:
    get_logs_dir().mkdir(parents=True, exist_ok=True)
    log_path = get_startup_log_path()
    handlers = [
        logging.FileHandler(log_path, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
    )


logger = logging.getLogger(__name__)


def main() -> int:
    """Main entry point for the GUI miner."""
    try:
        _setup_logging()
        install_exception_hooks()

        if "--verify-packaged" in sys.argv:
            from animica_miner_gui.packaging.verify import main as verify_main
            args = [arg for arg in sys.argv[1:] if arg != "--verify-packaged"]
            return verify_main(args)

        # Ensure config directory exists
        from animica_miner_gui.backend.config import get_default_config_dir
        config_dir = get_default_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Startup diagnostics:")
        logger.info("sys.executable=%s", sys.executable)
        logger.info("sys.frozen=%s", getattr(sys, "frozen", False))
        logger.info("sys._MEIPASS=%s", getattr(sys, "_MEIPASS", None))
        logger.info("argv=%s", sys.argv)
        logger.info("cwd=%s", os.getcwd())
        logger.info("platform=%s", platform.platform())
        logger.info("arch=%s", platform.machine())
        logger.info("resources=%s", get_resources_dir())
        logger.info("node_payload=%s", get_node_payload_dir())
        logger.info("node_executable=%s", get_node_executable())
        logger.info("token_path=%s", get_node_token_path())
        logger.info("selected_rpc_port=%s", "pending")
        logger.info("final_rpc_url=%s", "pending")
        last_crash = get_last_crash_log()
        logger.info("last_crash=%s", last_crash if last_crash else "none")
        
        # Create Qt application
        app = QApplication(sys.argv)
        app.setApplicationName("Animica Miner")
        app.setOrganizationName("Animica")
        app.setOrganizationDomain("animica.org")

        if getattr(sys, "frozen", False):
            plugins_dir = get_resources_dir() / "PySide6" / "Qt" / "plugins"
            if plugins_dir.exists():
                QCoreApplication.setLibraryPaths([str(plugins_dir)])
        
        # Set application icon
        from animica_miner_gui.resources import get_logo_path
        logo_path = get_logo_path()
        if logo_path:
            app.setWindowIcon(QIcon(str(logo_path)))

        instance = SingleInstance("animica-miner-gui")
        if not instance.start():
            instance.notify_existing()
            logger.info("Secondary instance detected. Exiting.")
            return 0
        
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

        crash_path = load_last_crash()
        if crash_path and crash_path.exists():
            QMessageBox.warning(
                None,
                "Crash Report",
                f"The app previously crashed.\nCrash log: {crash_path}",
            )
            clear_crash_marker()
        
        # Show main window
        window = MainWindow()
        instance.raiseRequested.connect(window.raise_and_activate)
        window.show()
        
        return app.exec()
    
    except Exception as e:
        logger.exception("Fatal error in main application")
        return 1


if __name__ == "__main__":
    # Required for PyInstaller frozen executables on macOS/Windows to prevent
    # infinite process spawning when using multiprocessing module.
    # MUST be called before main() to work correctly.
    multiprocessing.freeze_support()
    sys.exit(main())
