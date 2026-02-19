"""IdeBridge — QWebChannel bridge between the Monaco IDE and Python file services.

Security contract
-----------------
* All file paths are sanitised by :class:`~animica_studio.services.ide_service.IdeService`
  before any filesystem operation.
* Error messages are returned as plain strings — never dict dumps.
* File I/O runs in a worker thread; results are delivered via Qt signals.
* The bridge does NOT allow web content to access arbitrary paths.
"""

import json
import logging
import threading

from PySide6.QtCore import QObject, Signal, Slot

from animica_studio.services.ide_service import IdeService
from animica_studio.services.deterministic_runner import DeterministicRunner
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)


class IdeBridge(QObject):
    """Exposed to JS via QWebChannel as ``window.bridge``."""

    # Signals emitted back to JS via the web channel
    listDirResult = Signal(str, str)     # (requestId, json_result_or_error)
    readFileResult = Signal(str, str)    # (requestId, content_or_error)
    writeFileResult = Signal(str, str)   # (requestId, "ok" or error)
    createFileResult = Signal(str, str)
    createDirResult = Signal(str, str)
    renameResult = Signal(str, str)
    deleteResult = Signal(str, str)
    runScriptLine = Signal(str)          # streamed output line
    runScriptResult = Signal(str, str)   # (requestId, json result)
    workspaceChanged = Signal(str)       # new workspace path

    def __init__(self, ide_service: IdeService, parent: "QObject | None" = None) -> None:
        super().__init__(parent)
        self._svc = ide_service
        self._runner = DeterministicRunner()
        self._cancel_token: "CancelToken | None" = None

    # -- Workspace -------------------------------------------------------------

    @Slot(str)
    def setWorkspace(self, path: str) -> None:
        try:
            self._svc.set_workspace(path)
            self.workspaceChanged.emit(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("IdeBridge.setWorkspace: %s", exc)

    @Slot(result=str)
    def getWorkspace(self) -> str:
        ws = self._svc.workspace
        return str(ws) if ws else ""

    # -- Directory listing -----------------------------------------------------

    @Slot(str, str)
    def listDir(self, request_id: str, rel_path: str) -> None:
        def _work() -> None:
            try:
                entries = self._svc.list_dir(rel_path)
                self.listDirResult.emit(request_id, json.dumps({"ok": True, "entries": entries}))
            except Exception as exc:  # noqa: BLE001
                self.listDirResult.emit(request_id, json.dumps({"ok": False, "error": str(exc)}))
        threading.Thread(target=_work, daemon=True).start()

    # -- File read -------------------------------------------------------------

    @Slot(str, str)
    def readFile(self, request_id: str, rel_path: str) -> None:
        def _work() -> None:
            try:
                content = self._svc.read_file(rel_path)
                self.readFileResult.emit(request_id, json.dumps({"ok": True, "content": content}))
            except Exception as exc:  # noqa: BLE001
                self.readFileResult.emit(request_id, json.dumps({"ok": False, "error": str(exc)}))
        threading.Thread(target=_work, daemon=True).start()

    # -- File write ------------------------------------------------------------

    @Slot(str, str, str)
    def writeFile(self, request_id: str, rel_path: str, content: str) -> None:
        def _work() -> None:
            try:
                self._svc.write_file(rel_path, content)
                self.writeFileResult.emit(request_id, json.dumps({"ok": True}))
            except Exception as exc:  # noqa: BLE001
                self.writeFileResult.emit(request_id, json.dumps({"ok": False, "error": str(exc)}))
        threading.Thread(target=_work, daemon=True).start()

    # -- Create file/dir -------------------------------------------------------

    @Slot(str, str)
    def createFile(self, request_id: str, rel_path: str) -> None:
        def _work() -> None:
            try:
                self._svc.create_file(rel_path)
                self.createFileResult.emit(request_id, json.dumps({"ok": True}))
            except Exception as exc:  # noqa: BLE001
                self.createFileResult.emit(request_id, json.dumps({"ok": False, "error": str(exc)}))
        threading.Thread(target=_work, daemon=True).start()

    @Slot(str, str)
    def createDir(self, request_id: str, rel_path: str) -> None:
        def _work() -> None:
            try:
                self._svc.create_dir(rel_path)
                self.createDirResult.emit(request_id, json.dumps({"ok": True}))
            except Exception as exc:  # noqa: BLE001
                self.createDirResult.emit(request_id, json.dumps({"ok": False, "error": str(exc)}))
        threading.Thread(target=_work, daemon=True).start()

    # -- Rename / delete -------------------------------------------------------

    @Slot(str, str, str)
    def renamePath(self, request_id: str, old_rel: str, new_rel: str) -> None:
        def _work() -> None:
            try:
                self._svc.rename_path(old_rel, new_rel)
                self.renameResult.emit(request_id, json.dumps({"ok": True}))
            except Exception as exc:  # noqa: BLE001
                self.renameResult.emit(request_id, json.dumps({"ok": False, "error": str(exc)}))
        threading.Thread(target=_work, daemon=True).start()

    @Slot(str, str)
    def deletePath(self, request_id: str, rel_path: str) -> None:
        def _work() -> None:
            try:
                self._svc.delete_path(rel_path)
                self.deleteResult.emit(request_id, json.dumps({"ok": True}))
            except Exception as exc:  # noqa: BLE001
                self.deleteResult.emit(request_id, json.dumps({"ok": False, "error": str(exc)}))
        threading.Thread(target=_work, daemon=True).start()

    # -- Run script ------------------------------------------------------------

    @Slot(str, str)
    def runScript(self, request_id: str, rel_path: str) -> None:
        self._cancel_token = CancelToken()
        token = self._cancel_token

        def _work() -> None:
            def _on_line(line: str) -> None:
                self.runScriptLine.emit(line)

            result = self._runner.run_script(
                rel_path,
                cancel_token=token,
                on_line=_on_line,
            )
            self.runScriptResult.emit(
                request_id,
                json.dumps({
                    "ok": result.success,
                    "exit_code": result.exit_code,
                    "duration_ms": result.duration_ms,
                    "cancelled": result.cancelled,
                    "error": result.error,
                }),
            )
        threading.Thread(target=_work, daemon=True).start()

    @Slot()
    def cancelScript(self) -> None:
        if self._cancel_token:
            self._cancel_token.cancel()

    # -- Log -------------------------------------------------------------------

    @Slot(str)
    def log(self, msg: str) -> None:
        log.debug("IdeBridge[JS]: %s", msg)
