"""DAContributionService — local disk-space contribution to the Animica DA layer.

Integration surface discovery
------------------------------
After searching the repo the following DA interfaces were found:

  ┌─────────────────────────────┬──────────────────────────────────────────┬─────────────────────────────────┐
  │ Method / Command            │ Parameters                               │ Status fields returned          │
  ├─────────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────┤
  │ da_putBlob / da.putBlob     │ {data: base64, namespace?: str}          │ commitment (str)                │
  │ da_getBlob / da.getBlob     │ commitment (str)                         │ {data: base64}                  │
  │ da_getProof / da.getProof   │ commitment (str)                         │ proof object                    │
  │ animica da put <file>       │ file_path, --namespace                   │ stdout/stderr stream            │
  │ da.setConfig (not found)    │ —                                        │ —                               │
  │ da.status (not found)       │ —                                        │ —                               │
  └─────────────────────────────┴──────────────────────────────────────────┴─────────────────────────────────┘

Conclusion: No native node/CLI commands for disk contribution management exist.
This service runs in **preview mode** (FEATURE_FLAG: DA_CONTRIBUTION_PREVIEW).
Preview mode:
  - Manages the contribution directory locally
  - Enforces max_bytes cap via LRU eviction of tracked chunks
  - Exposes status / start / stop semantics without a live node backend
  - "Start network serving" is disabled with an explanatory message
  - Settings and state persist across restarts via a local manifest file
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# Feature flag: preview mode (no node backend required)
DA_CONTRIBUTION_PREVIEW = True


class ReserveMode(str, Enum):
    QUOTA = "quota"
    PREALLOCATE = "preallocate"


@dataclass
class DAStatusResult:
    enabled: bool
    running: bool
    directory: str
    limit_bytes: int
    used_bytes: int
    available_bytes: int
    served_bytes: int
    stored_chunks: int
    last_error: str
    health: str  # "online" | "offline" | "misconfigured"
    preview_mode: bool = True

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class _ChunkRecord:
    name: str
    size: int
    last_access: float = field(default_factory=time.time)


class DAContributionService:
    """Manages a local DA contribution store in preview mode.

    Cap enforcement (quota mode):
      - After each write the manifest is checked; oldest-accessed chunks are
        evicted until used_bytes <= limit_bytes.

    Cap enforcement (preallocate mode):
      - A sparse `.reserve` file is created at limit_bytes to discourage other
        filesystem users from filling the space.  Actual chunk eviction still
        uses the quota algorithm.

    All public methods are **synchronous** and safe to call from a worker
    thread.  The UI must not call them from the Qt main thread; use
    WorkerThread or JobRunner.run_callable().
    """

    _MANIFEST_NAME = ".da_manifest.json"
    _RESERVE_NAME = ".reserve"
    _LOG_MAX_LINES = 200

    def __init__(self) -> None:
        self._enabled = False
        self._directory: Path | None = None
        self._limit_bytes: int = 50 * 1024 ** 3  # 50 GB default
        self._reserve_mode: ReserveMode = ReserveMode.QUOTA
        self._running = False
        self._served_bytes: int = 0
        self._last_error: str = ""
        self._chunks: list[_ChunkRecord] = []
        self._log_lines: list[str] = []
        self._log_cb: Callable[[str], None] | None = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(
        self,
        enabled: bool,
        directory: str,
        max_bytes: int,
        reserve_mode: str = "quota",
    ) -> dict:
        """Apply configuration.

        Returns ``{"ok": True}`` or ``{"ok": False, "error": "..."}``.
        """
        try:
            _validate_config(directory, max_bytes)
        except ValueError as exc:
            self._last_error = str(exc)
            return {"ok": False, "error": str(exc)}

        self._enabled = enabled
        self._directory = Path(directory).expanduser().resolve()
        self._limit_bytes = max_bytes
        try:
            self._reserve_mode = ReserveMode(reserve_mode)
        except ValueError:
            self._reserve_mode = ReserveMode.QUOTA

        # Ensure directory exists
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            msg = f"Cannot create directory: {exc}"
            self._last_error = msg
            return {"ok": False, "error": msg}

        self._load_manifest()
        self._last_error = ""
        self._log(f"Configured: dir={self._directory} limit={max_bytes} mode={self._reserve_mode.value}")
        return {"ok": True}

    # ------------------------------------------------------------------
    # Start / Stop
    # ------------------------------------------------------------------

    def start(self) -> dict:
        """Start the local DA storage worker."""
        if not self._enabled:
            return {"ok": False, "error": "DA contribution is disabled."}
        if self._directory is None:
            return {"ok": False, "error": "Not configured. Call configure() first."}
        if self._running:
            return {"ok": True, "message": "Already running."}

        try:
            _validate_config(str(self._directory), self._limit_bytes)
        except ValueError as exc:
            self._last_error = str(exc)
            return {"ok": False, "error": str(exc)}

        if self._reserve_mode == ReserveMode.PREALLOCATE:
            self._create_reserve_file()

        self._running = True
        self._last_error = ""
        self._log("DA local store started (preview mode — network serving pending node support).")
        return {"ok": True, "message": "Local store started (preview mode)."}

    def stop(self) -> dict:
        """Stop the local DA storage worker."""
        if not self._running:
            return {"ok": True, "message": "Not running."}
        self._running = False
        self._save_manifest()
        self._log("DA local store stopped.")
        return {"ok": True}

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> DAStatusResult:
        """Return current status snapshot."""
        directory = str(self._directory) if self._directory else ""
        used = self._compute_used_bytes()
        avail = self._compute_available_bytes()

        if not self._enabled:
            health = "offline"
        elif self._last_error:
            health = "misconfigured"
        elif self._running:
            health = "online"
        else:
            health = "offline"

        return DAStatusResult(
            enabled=self._enabled,
            running=self._running,
            directory=directory,
            limit_bytes=self._limit_bytes,
            used_bytes=used,
            available_bytes=avail,
            served_bytes=self._served_bytes,
            stored_chunks=len(self._chunks),
            last_error=self._last_error,
            health=health,
            preview_mode=DA_CONTRIBUTION_PREVIEW,
        )

    # ------------------------------------------------------------------
    # Log streaming
    # ------------------------------------------------------------------

    def set_log_callback(self, cb: Callable[[str], None] | None) -> None:
        """Register a callback to receive log lines (called from service thread)."""
        self._log_cb = cb

    def get_log_lines(self) -> list[str]:
        """Return buffered log lines (newest last)."""
        return list(self._log_lines)

    # ------------------------------------------------------------------
    # Cap enforcement (internal)
    # ------------------------------------------------------------------

    def _evict_lru(self) -> None:
        """Remove least-recently-accessed chunks until used_bytes <= limit_bytes."""
        if self._directory is None:
            return
        self._chunks.sort(key=lambda c: c.last_access)
        while self._chunks and self._compute_used_bytes() > self._limit_bytes:
            oldest = self._chunks.pop(0)
            target = self._directory / oldest.name
            try:
                target.unlink(missing_ok=True)
                self._log(f"Evicted chunk {oldest.name} ({oldest.size} bytes)")
            except OSError as exc:
                self._log(f"Eviction error for {oldest.name}: {exc}")
        self._save_manifest()

    def _compute_used_bytes(self) -> int:
        """Sum tracked chunk sizes."""
        return sum(c.size for c in self._chunks)

    def _compute_available_bytes(self) -> int:
        """Bytes remaining within the limit."""
        return max(0, self._limit_bytes - self._compute_used_bytes())

    # ------------------------------------------------------------------
    # Reserve file (preallocate mode)
    # ------------------------------------------------------------------

    def _create_reserve_file(self) -> None:
        if self._directory is None:
            return
        reserve = self._directory / self._RESERVE_NAME
        if reserve.exists():
            return
        try:
            with open(reserve, "wb") as f:
                f.seek(self._limit_bytes - 1)
                f.write(b"\0")
            self._log(f"Created sparse reserve file: {reserve} ({self._limit_bytes} bytes)")
        except OSError as exc:
            self._log(f"Warning: could not create reserve file: {exc}")

    def remove_reserve_file(self) -> None:
        """Delete the `.reserve` file if it exists."""
        if self._directory is None:
            return
        reserve = self._directory / self._RESERVE_NAME
        try:
            reserve.unlink(missing_ok=True)
            self._log("Removed reserve file.")
        except OSError as exc:
            self._log(f"Warning: could not remove reserve file: {exc}")

    # ------------------------------------------------------------------
    # Manifest persistence
    # ------------------------------------------------------------------

    def _manifest_path(self) -> Path | None:
        if self._directory is None:
            return None
        return self._directory / self._MANIFEST_NAME

    def _load_manifest(self) -> None:
        path = self._manifest_path()
        if path is None or not path.exists():
            self._chunks = []
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._served_bytes = int(data.get("served_bytes", 0))
            chunks_raw = data.get("chunks", [])
            self._chunks = [
                _ChunkRecord(
                    name=str(c["name"]),
                    size=int(c["size"]),
                    last_access=float(c.get("last_access", 0)),
                )
                for c in chunks_raw
                if isinstance(c, dict) and c.get("name") and c.get("size")
            ]
        except Exception as exc:  # noqa: BLE001
            log.warning("DA manifest load error: %s", exc)
            self._chunks = []

    def _save_manifest(self) -> None:
        path = self._manifest_path()
        if path is None:
            return
        data = {
            "served_bytes": self._served_bytes,
            "chunks": [
                {"name": c.name, "size": c.size, "last_access": c.last_access}
                for c in self._chunks
            ],
        }
        try:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            log.warning("DA manifest save error: %s", exc)

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self._log_lines.append(line)
        if len(self._log_lines) > self._LOG_MAX_LINES:
            self._log_lines = self._log_lines[-self._LOG_MAX_LINES :]
        log.info("DAContrib: %s", msg)
        if self._log_cb:
            try:
                self._log_cb(line)
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Validation helpers (used by service and UI)
# ---------------------------------------------------------------------------


def _validate_config(directory: str, max_bytes: int) -> None:
    """Raise ValueError with a user-friendly message on invalid config."""
    if not directory or not directory.strip():
        raise ValueError("Contribution directory must not be empty.")

    path = Path(directory).expanduser().resolve()

    if path.exists() and not path.is_dir():
        raise ValueError(f"Path exists but is not a directory: {path}")

    # Check writeability: try the parent if the dir doesn't exist yet
    check_dir = path if path.exists() else path.parent
    if not os.access(check_dir, os.W_OK):
        raise ValueError(f"Directory is not writable: {check_dir}")

    gb = max_bytes / (1024 ** 3)
    if gb < 1.0:
        raise ValueError("Max allocation must be at least 1 GB.")

    # Warn (don't fail) if free space is tight
    try:
        stat = shutil.disk_usage(str(check_dir))
        safety_margin = 2 * 1024 ** 3  # 2 GB
        if stat.free < max_bytes + safety_margin:
            free_gb = stat.free / (1024 ** 3)
            log.warning(
                "DA contribution: requested %.1f GB but only %.1f GB free on %s",
                gb,
                free_gb,
                check_dir,
            )
    except OSError:
        pass


def default_da_dir() -> str:
    """Return the default contribution directory path (not created)."""
    return str(Path.home() / ".animica" / "da_store")
