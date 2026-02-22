"""ConsoleService: manage command presets, history, and run records."""
from __future__ import annotations
import logging
import time
import uuid
from typing import Callable

from animica_studio.models.console_models import CommandPreset, RunRecord
from animica_studio.models.exec_models import ExecResult, StreamEvent
from animica_studio.services.cli_capabilities import get_cli_ops
from animica_studio.services.cli_ops import CliOperation
from animica_studio.services.cli_runner import CliRunner
from animica_studio.services.job_runner import run_cli_blocking
from animica_studio.storage.config import Config, load_config
from animica_studio.util.cancel import CancelToken

log = logging.getLogger(__name__)

_MAX_HISTORY = 200
_MAX_RUN_RECORDS = 100


class ConsoleService:
    """Manages presets, history and run-records for the Console page."""

    def __init__(self, config: Config | None = None) -> None:
        self._config = config or load_config()
        self._presets: list[CommandPreset] = self._default_presets()
        self._history: list[str] = []
        self._run_records: list[RunRecord] = []
        self._runner = CliRunner()

    def _default_presets(self) -> list[CommandPreset]:
        presets: list[dict[str, object]] = [
            {"group": "Node", "label": "Node Status", "argv": ["node", "status"]},
            {"group": "Node", "label": "Node Start", "argv": ["node", "start"]},
            {"group": "Node", "label": "Node Stop", "argv": ["node", "stop"]},
            {"group": "Node", "label": "Sync Status", "argv": ["sync", "status"]},
            {"group": "Chain/RPC", "label": "RPC Discover", "argv": ["rpc", "call", "rpc.discover"]},
            {"group": "Chain/RPC", "label": "Chain Head", "argv": ["rpc", "call", "chain_getHead"]},
        ]
        try:
            ops = get_cli_ops(self._config)
            presets.append({"group": "Wallet", "label": "Wallet List", "argv": ops.build(CliOperation.WALLET_LIST)})
            presets.append({"group": "AICF", "label": "AICF Status", "argv": ops.build(CliOperation.AICF_STATUS)})
        except Exception as exc:  # noqa: BLE001
            log.warning("ConsoleService: failed to build op presets: %s", exc)
        return [CommandPreset.make(group=str(p["group"]), label=str(p["label"]), argv=list(p["argv"])) for p in presets]

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

    def push_history(self, cmd_str: str) -> None:
        if not cmd_str:
            return
        if cmd_str in self._history:
            self._history.remove(cmd_str)
        self._history.append(cmd_str)
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

    def get_history(self) -> list[str]:
        return list(reversed(self._history))

    def load_history(self, raw: list[str]) -> None:
        self._history = list(raw)[-_MAX_HISTORY:]

    def get_run_records(self) -> list[RunRecord]:
        return list(self._run_records)

    def _add_record(self, record: RunRecord) -> None:
        self._run_records.append(record)
        if len(self._run_records) > _MAX_RUN_RECORDS:
            self._run_records = self._run_records[-_MAX_RUN_RECORDS:]

    def run(
        self,
        argv: list[str],
        cwd: str | None = None,
        profile_name: str | None = None,
        timeout_s: float = 120.0,
        cancel_token: CancelToken | None = None,
        stream_cb: Callable[[StreamEvent], None] | None = None,
    ) -> RunRecord:
        cmd_str = " ".join(argv)
        self.push_history(cmd_str)

        record_id = str(uuid.uuid4())
        started_ts = time.time()

        cp = run_cli_blocking(argv, cwd=cwd, timeout_s=int(timeout_s), config=self._config)
        result = ExecResult(
            cmd=cp.args if isinstance(cp.args, list) else argv,
            returncode=cp.returncode,
            timed_out=False,
            cancelled=bool(cancel_token and cancel_token.is_cancelled),
            start_ts=started_ts,
            end_ts=time.time(),
            duration_ms=int((time.time() - started_ts) * 1000),
            stdout=cp.stdout or "",
            stderr=cp.stderr or "",
            stdout_lines=(cp.stdout or "").splitlines(),
            stderr_lines=(cp.stderr or "").splitlines(),
            error=None if cp.returncode == 0 else (cp.stderr or cp.stdout or "").strip(),
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
        return record
