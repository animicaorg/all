"""ConsoleService: manage command presets, history, and run records."""
from __future__ import annotations
import logging
import time
import uuid
from typing import Callable

from animica_studio.models.console_models import CommandPreset, RunRecord
from animica_studio.models.exec_models import ExecResult, StreamEvent
from animica_studio.services.cli_runner import CliRunner
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)

_MAX_HISTORY = 200
_MAX_RUN_RECORDS = 100

_DEFAULT_PRESETS = [
    # Node group
    {"group": "Node", "label": "Node Status", "argv": ["animica", "node", "status"]},
    {"group": "Node", "label": "Node Start", "argv": ["animica", "node", "start"]},
    {"group": "Node", "label": "Node Stop",  "argv": ["animica", "node", "stop"]},
    {"group": "Node", "label": "Sync Status","argv": ["animica", "sync", "status"]},
    # Chain/RPC
    {"group": "Chain/RPC", "label": "RPC Discover", "argv": ["animica", "rpc", "call", "rpc.discover"]},
    {"group": "Chain/RPC", "label": "Chain Head",   "argv": ["animica", "rpc", "call", "chain_getHead"]},
    # Wallet
    {"group": "Wallet", "label": "Wallet List",    "argv": ["animica", "wallet", "list"]},
    # AICF
    {"group": "AICF", "label": "AICF Status",      "argv": ["animica", "aicf", "status"]},
    {"group": "AICF", "label": "AICF Jobs List",   "argv": ["animica", "aicf", "jobs", "list"]},
]


def _default_presets() -> list[CommandPreset]:
    presets = []
    for raw in _DEFAULT_PRESETS:
        p = CommandPreset.make(
            group=raw["group"],
            label=raw["label"],
            argv=raw["argv"],
        )
        presets.append(p)
    return presets


class ConsoleService:
    """Manages presets, history and run-records for the Console page."""

    def __init__(self) -> None:
        self._presets: list[CommandPreset] = _default_presets()
        self._history: list[str] = []
        self._run_records: list[RunRecord] = []
        self._runner = CliRunner()

    # -- Presets ----------------------------------------------------------------

    def get_presets(self) -> list[CommandPreset]:
        return list(self._presets)

    def load_presets(self, raw: list[dict]) -> None:
        if raw:
            try:
                self._presets = [CommandPreset.from_dict(d) for d in raw]
            except Exception as exc:  # noqa: BLE001
                log.warning("ConsoleService: failed to load presets: %s", exc)

    def save_presets_to(self) -> list[dict]:
        return [p.to_dict() for p in self._presets]

    # -- History ----------------------------------------------------------------

    def push_history(self, cmd_str: str) -> None:
        if not cmd_str:
            return
        # Remove duplicate if present
        if cmd_str in self._history:
            self._history.remove(cmd_str)
        self._history.append(cmd_str)
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

    def get_history(self) -> list[str]:
        return list(reversed(self._history))  # newest first

    def load_history(self, raw: list[str]) -> None:
        self._history = list(raw)[-_MAX_HISTORY:]

    # -- Run records -----------------------------------------------------------

    def get_run_records(self) -> list[RunRecord]:
        return list(self._run_records)

    def _add_record(self, record: RunRecord) -> None:
        self._run_records.append(record)
        if len(self._run_records) > _MAX_RUN_RECORDS:
            self._run_records = self._run_records[-_MAX_RUN_RECORDS:]

    # -- Execution -------------------------------------------------------------

    def run(
        self,
        argv: list[str],
        cwd: str | None = None,
        profile_name: str | None = None,
        timeout_s: float = 120.0,
        cancel_token: CancelToken | None = None,
        stream_cb: Callable[[StreamEvent], None] | None = None,
    ) -> RunRecord:
        """Run *argv* and return a :class:`RunRecord`."""
        cmd_str = " ".join(argv)
        self.push_history(cmd_str)

        record_id = str(uuid.uuid4())
        started_ts = time.time()

        result: ExecResult = self._runner.run(
            argv,
            cwd=cwd,
            timeout_s=timeout_s,
            cancel_token=cancel_token,
            stream_cb=stream_cb,
        )

        record = RunRecord(
            id=record_id,
            started_ts=started_ts,
            ended_ts=result.end_ts,
            argv=argv,
            cwd=cwd,
            profile_name=profile_name,
            exit_code=result.returncode,
            duration_ms=result.duration_ms,
            cancelled=result.cancelled,
            error=result.error,
            stdout_snippet=result.stdout[-500:] if result.stdout else "",
        )
        self._add_record(record)
        log.info(
            "ConsoleService: run completed exit_code=%s duration_ms=%d cancelled=%s argv=%r",
            result.returncode, result.duration_ms, result.cancelled, argv,
        )
        return record
