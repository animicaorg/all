"""QApplication bootstrap, global exception handler, and theme defaults."""

from __future__ import annotations

import logging
import os
import sys
import traceback
from typing import Type
import faulthandler

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from animica_studio import __app_name__, __org_name__, __version__
from animica_studio.storage.config import load_config
from animica_studio.util.logging import setup_logging
from animica_studio.util.paths import logs_dir

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global exception hook
# ---------------------------------------------------------------------------


def _exception_hook(
    exc_type: Type[BaseException],
    exc_value: BaseException,
    exc_tb: object,
) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    log.critical("Unhandled exception:\n%s", tb_str)

    app = QApplication.instance()
    if app is not None:
        msg = QMessageBox()
        msg.setWindowTitle("Unexpected Error")
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setText(
            f"<b>An unexpected error occurred.</b><br><br>"
            f"<code>{exc_type.__name__}: {exc_value}</code>"
        )
        msg.setDetailedText(tb_str)
        msg.exec()


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def _create_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    app.setApplicationName(__app_name__)
    app.setOrganizationName(__org_name__)
    app.setApplicationVersion(__version__)
    app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    return app  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Bootstrap and run the Animica Studio application.

    Supports a ``doctor`` subcommand::

        animica-studio doctor [--json] [--rpc-url URL] [--verbose]
    """
    # Handle non-GUI subcommands before touching Qt.
    _argv = sys.argv[1:]
    if _argv and _argv[0] == "doctor":
        from animica_studio.doctor import doctor_main  # noqa: PLC0415

        sys.exit(doctor_main(_argv[1:]))

    # Logging must be set up before anything else
    setup_logging(logs_dir(), app_version=__version__)

    import platform  # noqa: PLC0415
    uid = getattr(os, "geteuid", lambda: -1)()
    log.info(
        "Studio bootstrap start (version=%s, platform=%s, uid=%s, cwd=%s)",
        __version__,
        platform.platform(),
        uid,
        os.getcwd(),
    )
    log.info("Starting %s v%s", __app_name__, __version__)

    # Capture fatal traces from crashes/segfaults as early as possible.
    try:
        faulthandler.enable(all_threads=True)
    except Exception:
        log.exception("Failed to enable faulthandler")

    # Install global exception hook
    sys.excepthook = _exception_hook  # type: ignore[assignment]

    # Load config (creates defaults on first run)
    config = load_config()

    # Load cached CLI registry early so pages can resolve commands quickly.
    try:
        from animica_studio.services.cli_capabilities import get_cli_registry  # noqa: PLC0415

        get_cli_registry(config)
    except Exception:
        log.exception("CLI registry preload failed")

    # Create application
    app = _create_app()

    # Import here so Qt is already initialised
    from animica_studio.services.profile_service import ProfileService  # noqa: PLC0415
    from animica_studio.ui.main_window import MainWindow  # noqa: PLC0415

    # Initialise profile service (runs migration + ensure_defaults)
    profile_service = ProfileService(config)

    safe_mode = os.getenv("ANIMICA_STUDIO_SAFE_MODE", "").strip() == "1"
    window = MainWindow(config, profile_service, safe_mode=safe_mode)
    window.show()

    def _post_start_init() -> None:
        try:
            window.run_post_start_init()

            # Launch wizard if first run not completed or no profiles configured
            should_run_wizard = (
                not config.first_run_completed
                or not config.rpc_profiles
            )
            if should_run_wizard:
                from animica_studio.ui.wizard.wizard_window import SetupWizard  # noqa: PLC0415

                def _launch_wizard() -> None:
                    try:
                        dlg = SetupWizard(profile_service, parent=window)
                        result = dlg.exec()
                        if result != dlg.DialogCode.Accepted:
                            window.show_no_profile_banner()
                        else:
                            window.refresh_header()
                    except Exception:
                        log.exception("Startup wizard launch failed")
                        window.show_startup_degraded_banner(
                            "Startup degraded mode: setup wizard unavailable."
                        )

                QTimer.singleShot(200, _launch_wizard)
        except Exception:
            log.exception("Post-start initialisation failed")
            window.show_startup_degraded_banner(
                "Startup degraded mode: optional startup tasks failed."
            )

    QTimer.singleShot(0, _post_start_init)

    log.info("Application window shown")
    exit_code = app.exec()
    log.info("Application exiting with code %d", exit_code)
    sys.exit(exit_code)
