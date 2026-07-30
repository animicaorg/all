"""Main entry point for the Animica GUI Miner application."""

import logging
import multiprocessing
import os
import platform
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from animica_miner_gui.backend.app_paths import (
    get_node_token_path,
    get_resources_dir,
    get_startup_log_path,
    get_logs_dir,
    get_last_crash_log,
)
from animica_miner_gui.backend.crash_reporter import clear_crash_marker, install_exception_hooks, load_last_crash
from animica_miner_gui.backend.node_paths import resolve_node_executable
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


def _frozen_qt_plugins_dir() -> Path | None:
    """Find the bundled Qt plugin directory across PyInstaller layouts.

    PySide6 does not use one layout: `PySide6/Qt/plugins` on Linux/macOS but
    `PySide6/plugins` on Windows, and PyInstaller sometimes flattens it to a
    bare `plugins`. Probing only the first meant the whole configuration block
    was skipped on Windows — it worked purely because PyInstaller's own
    pyi_rth_pyside6 hook had already set the paths, so this function was
    silently dead there rather than helping.

    Only accept a candidate that actually contains `platforms`; that is the
    subdirectory holding the platform plugin Qt refuses to start without.
    """
    resources = get_resources_dir()
    candidates = (
        resources / "PySide6" / "Qt" / "plugins",
        resources / "PySide6" / "plugins",
        resources / "plugins",
    )
    for candidate in candidates:
        if (candidate / "platforms").is_dir():
            return candidate
    logger.warning("no bundled Qt plugins dir with platforms/ under %s", resources)
    return None


def _configure_qt_plugins() -> None:
    plugins_dir: Path | None = None
    if getattr(sys, "frozen", False):
        plugins_dir = _frozen_qt_plugins_dir()
    else:
        plugins_path = ""
        try:
            plugins_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)
        except AttributeError:
            plugins_path = QLibraryInfo.location(QLibraryInfo.PluginsPath)
        if plugins_path:
            plugins_dir = Path(plugins_path)

    if plugins_dir and plugins_dir.exists():
        platforms_dir = plugins_dir / "platforms"
        os.environ["QT_PLUGIN_PATH"] = str(plugins_dir)
        if platforms_dir.exists():
            os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platforms_dir)
        QCoreApplication.setLibraryPaths([str(plugins_dir)])
        logger.info("qt_plugins_dir=%s", plugins_dir)
        if platforms_dir.exists():
            logger.info("qt_platforms_dir=%s", platforms_dir)


def _report_fatal_error(exc: BaseException) -> None:
    """Put a startup failure in front of the user instead of vanishing.

    The app is frozen with ``console=False``, so stderr goes nowhere the user
    will ever look: any exception before the main window appears just made the
    process disappear, which is what "it closed with errors" looks like from
    the outside. Show a dialog naming the error and the log file, so a failed
    launch is reportable instead of a mystery.

    Everything here is best-effort — if Qt itself is what failed we still have
    the log on disk, and on macOS we additionally shell out to AppleScript so
    there is *some* visible message even with no working Qt.
    """
    try:
        log_path = get_startup_log_path()
    except Exception:
        log_path = None

    detail = f"{type(exc).__name__}: {exc}"
    message = (
        "Animica Miner could not start.\n\n"
        f"{detail}\n\n"
        + (f"Details were written to:\n{log_path}\n\n" if log_path else "")
        + "Please share that log when reporting this."
    )

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Animica Miner — startup failed")
        box.setText("Animica Miner could not start.")
        box.setInformativeText(message)
        box.exec()
        return
    except Exception:
        pass

    if sys.platform == "darwin":
        # Last resort when Qt is unusable: a native dialog via osascript.
        try:
            import subprocess

            escaped = message.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display alert "Animica Miner — startup failed" message "{escaped}" as critical',
                ],
                timeout=60,
                check=False,
            )
        except Exception:
            pass

    print(message, file=sys.stderr)


#: The only modules the frozen binary will re-enter as __main__.
#:
#: Without this, --run-module was a general-purpose Python launcher wearing the
#: app's identity: `--run-module timeit -s '<code>'` executes arbitrary source
#: and `--run-module pdb -c continue file.py` executes an arbitrary script —
#: under a Developer-ID-signed, notarized binary whose entitlements allow JIT
#: and disable library validation, and before logging or the crash hooks are
#: installed. Only the two CLI entry points cli_runner actually invokes are
#: permitted; both are guaranteed present by the spec's REQUIRED_MODULES gate.
_ALLOWED_RUN_MODULES = frozenset({"animica.cli.main", "mining.cli.miner"})


def _audit_run_module(module: str, argv: list[str]) -> None:
    """Append one line about a CLI re-entry to the startup log.

    Deliberately writes the file directly instead of configuring the `logging`
    root: miner_runner merges the child's stderr into the stream it parses for
    mining events, and the wallet parses child output for a transaction hash —
    a stray log handler would corrupt both.
    """
    try:
        from animica_miner_gui.backend.app_paths import get_logs_dir, get_startup_log_path
        from datetime import datetime

        get_logs_dir().mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with get_startup_log_path().open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} [INFO] run-module: {module} argv={argv}\n")
    except Exception:
        pass


def _run_module_entrypoint() -> int:
    """Act as a CLI host: `AnimicaMiner --run-module <mod> [args...]`.

    A frozen bundle has no Python interpreter to invoke, so the usual
    ``[sys.executable, "-m", pkg, ...]`` pattern silently relaunches the GUI —
    PyInstaller's bootloader ignores ``-m``. Since the CLI packages are already
    inside the bundle, the binary can simply run them itself. This flag is the
    supported way to do that, and cli_runner builds commands against it.
    """
    import runpy

    argv = sys.argv[1:]
    idx = argv.index("--run-module")
    if idx + 1 >= len(argv):
        print("--run-module requires a module name", file=sys.stderr)
        return 2
    module = argv[idx + 1]
    forwarded = argv[idx + 2:]

    if module not in _ALLOWED_RUN_MODULES:
        print(
            f"error: --run-module does not allow '{module}' "
            f"(permitted: {', '.join(sorted(_ALLOWED_RUN_MODULES))})",
            file=sys.stderr,
        )
        return 2
    _audit_run_module(module, forwarded)

    # Present the module with the argv it would have seen under `python -m`.
    sys.argv = [module, *forwarded]
    try:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
        return 0
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        # A string exit code is a message, per SystemExit semantics.
        print(code, file=sys.stderr)
        return 1
    except ImportError as exc:
        # Only modules baked into the bundle are reachable, so this means a
        # caller asked for something that was not collected. Say that plainly
        # instead of dumping a frozen-runpy traceback the user cannot act on.
        print(
            f"error: module '{module}' is not available in this build ({exc}).",
            file=sys.stderr,
        )
        return 2


def main() -> int:
    """Main entry point for the GUI miner."""
    # Handled before anything else: this process is a CLI, not the GUI, so it
    # must not create a QApplication or trip the single-instance guard.
    if len(sys.argv) > 1 and sys.argv[1] == "--run-module":
        return _run_module_entrypoint()

    try:
        _setup_logging()
        install_exception_hooks()

        if "--verify-packaged" in sys.argv:
            from animica_miner_gui.packaging.verify import main as verify_main
            args = [arg for arg in sys.argv[1:] if arg != "--verify-packaged"]
            return verify_main(args)

        # `--smoke-test` walks the entire startup path — Qt platform plugin,
        # every lazy import, config, device detection, MainWindow construction —
        # and exits before the event loop. The build scripts run the frozen
        # binary this way and refuse to publish an artifact that fails, because
        # a `console=False` bundle that dies on startup is otherwise
        # indistinguishable from a successful build until a user reports "it
        # closed with errors".
        smoke_test = "--smoke-test" in sys.argv

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
        node_paths = resolve_node_executable()
        logger.info("node_payload=%s", node_paths.base_dir)
        logger.info("node_executable=%s", node_paths.exe_path)
        logger.info("node_resolve_mode=%s", node_paths.mode)
        logger.info("node_resolve_reason=%s", node_paths.reason)
        logger.info("token_path=%s", get_node_token_path())
        logger.info("selected_rpc_port=%s", "pending")
        logger.info("final_rpc_url=%s", "pending")
        last_crash = get_last_crash_log()
        logger.info("last_crash=%s", last_crash if last_crash else "none")

        _configure_qt_plugins()

        # Create Qt application
        app = QApplication(sys.argv)
        app.setApplicationName("Animica Miner")
        app.setOrganizationName("Animica")
        app.setOrganizationDomain("animica.org")
        
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
        
        # Check if this is first run. Skipped under --smoke-test: the wizard is
        # modal, so a headless CI check would hang forever waiting on it.
        config_path = get_default_config_path()
        if not smoke_test and not config_path.exists():
            logger.info("First run detected, showing setup wizard")
            wizard = FirstRunWizard()
            if wizard.exec() != wizard.DialogCode.Accepted:
                logger.info("Setup wizard cancelled, exiting")
                return 0

        crash_path = load_last_crash()
        if not smoke_test and crash_path and crash_path.exists():
            QMessageBox.warning(
                None,
                "Crash Report",
                f"The app previously crashed.\nCrash log: {crash_path}",
            )
            clear_crash_marker()
        
        # Launch straight into unified "mine + AI" mode when requested
        # (`animica gui full` / `animica gui miner --unified` /
        #  `animica-miner-gui --unified`).
        launch_unified = "--unified" in sys.argv or "--full" in sys.argv
        if launch_unified:
            logger.info("Launching in unified (mine + AI) mode")

        # Show main window
        window = MainWindow(unified=launch_unified)
        instance.raiseRequested.connect(window.raise_and_activate)
        window.show()

        if smoke_test:
            # Everything that can fail before the UI is interactive has now run.
            logger.info("smoke_test=ok")
            print("smoke_test=ok", flush=True)
            window.close()
            # MainWindow spins up non-daemon worker threads (node controller,
            # pollers) that keep the interpreter alive long after the window is
            # gone, so a plain `return 0` never actually exits. This is a probe,
            # not a session — hard-exit rather than wait on threads whose only
            # remaining job is to be torn down.
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(0)

        return app.exec()
    
    except Exception as e:
        try:
            logger.exception("Fatal error in main application")
        except Exception:
            pass
        _report_fatal_error(e)
        return 1


if __name__ == "__main__":
    # Required for PyInstaller frozen executables on macOS/Windows to prevent
    # infinite process spawning when using multiprocessing module.
    # MUST be called before main() to work correctly.
    multiprocessing.freeze_support()
    sys.exit(main())
