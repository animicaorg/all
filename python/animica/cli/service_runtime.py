"""Helpers for running local operator services under the CLI."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class ServiceState:
    """Filesystem state for a managed local service."""

    name: str
    root: Path
    pid_file: Path
    log_file: Path
    meta_file: Path


def service_state(name: str) -> ServiceState:
    base_dir = Path(
        os.environ.get("ANIMICA_SERVICE_STATE_DIR")
        or (Path.home() / ".animica" / "services")
    )
    root = base_dir / name
    root.mkdir(parents=True, exist_ok=True)
    return ServiceState(
        name=name,
        root=root,
        pid_file=root / f"{name}.pid",
        log_file=root / f"{name}.log",
        meta_file=root / f"{name}.json",
    )


def read_pid(state: ServiceState) -> Optional[int]:
    if not state.pid_file.exists():
        return None
    try:
        return int(state.pid_file.read_text().strip())
    except Exception:  # noqa: BLE001
        return None


def is_running(pid: Optional[int]) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def write_metadata(state: ServiceState, payload: Dict[str, object]) -> None:
    state.meta_file.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def read_metadata(state: ServiceState) -> Dict[str, object]:
    if not state.meta_file.exists():
        return {}
    try:
        return json.loads(state.meta_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def start_daemon(
    state: ServiceState,
    *,
    cmd: list[str],
    env: Dict[str, str],
    cwd: Path,
    metadata: Dict[str, object],
) -> int:
    with state.log_file.open("ab") as log_handle:
        process = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    state.pid_file.write_text(str(process.pid), encoding="utf-8")
    write_metadata(state, metadata | {"pid": process.pid, "started_at": int(time.time())})
    return process.pid


def stop_daemon(state: ServiceState, *, timeout_s: float = 10.0) -> bool:
    pid = read_pid(state)
    if pid is None or not is_running(pid):
        if state.pid_file.exists():
            state.pid_file.unlink()
        return False

    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not is_running(pid):
            break
        time.sleep(0.2)

    if is_running(pid):
        os.kill(pid, signal.SIGKILL)

    if state.pid_file.exists():
        state.pid_file.unlink()
    return True
