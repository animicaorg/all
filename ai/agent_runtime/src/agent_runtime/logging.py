"""Structured logging, stage banners, per-stage log files, manifest emission.

Used by the pipeline driver and individual stage scripts. The CLI chat REPL
uses :func:`get_chat_logger` for a slimmer surface (rich console only, no
JSONL files).

Design constraints:

- All log records that land in a JSONL file have a stable schema (see
  ``_RECORD_SCHEMA_VERSION``).
- Stage manifests are emitted atomically (write to .tmp, rename) so a
  killed stage never leaves a half-written manifest.
- The pipeline status JSON is the single source of truth for "what's
  currently running"; updates are atomic and small.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional


_RECORD_SCHEMA_VERSION = 1
_STATUS_SCHEMA_VERSION = 1
_MANIFEST_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- #
# Public dataclasses                                                          #
# --------------------------------------------------------------------------- #

@dataclass
class StageStatus:
    """A point-in-time status record for a single pipeline stage."""

    name: str
    state: str  # pending | running | completed | failed | skipped
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    duration_sec: Optional[float] = None
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    skipped_reason: Optional[str] = None
    error: Optional[str] = None


@dataclass
class RunStatus:
    """Live status JSON for the entire pipeline run."""

    schema: int = _STATUS_SCHEMA_VERSION
    run_id: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0
    completed_at: Optional[float] = None
    mode_requested: str = ""
    mode_effective: str = ""
    stages: list[StageStatus] = field(default_factory=list)
    pipeline_state: str = "running"  # running | completed | failed | aborted

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class StageManifest:
    """Emitted at the end of every stage, success or fail."""

    schema: int = _MANIFEST_SCHEMA_VERSION
    stage: str = ""
    run_id: str = ""
    state: str = ""              # completed | failed | skipped
    started_at: float = 0.0
    completed_at: float = 0.0
    duration_sec: float = 0.0
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    output_sizes_bytes: dict[str, int] = field(default_factory=dict)
    output_sha256: dict[str, str] = field(default_factory=dict)
    skipped_reason: Optional[str] = None
    error: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunSummary:
    """Final summary written when the pipeline exits (any reason)."""

    schema: int = _MANIFEST_SCHEMA_VERSION
    run_id: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    duration_sec: float = 0.0
    mode_requested: str = ""
    mode_effective: str = ""
    pipeline_state: str = ""
    stages: list[StageStatus] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    exit_code: int = 0


# --------------------------------------------------------------------------- #
# Atomic writers                                                              #
# --------------------------------------------------------------------------- #

def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=False, default=str)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _hash_file(path: Path) -> Optional[str]:
    """Return sha256 hex of a file, or None if unreadable or oversize."""
    import hashlib
    if not path.is_file():
        return None
    try:
        size = path.stat().st_size
        if size > 1_073_741_824:  # 1 GiB cap; skip giant artifacts
            return None
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _size_of(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return -1
    if path.is_dir():
        total = 0
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total
    return -1


# --------------------------------------------------------------------------- #
# JSONL log writer                                                            #
# --------------------------------------------------------------------------- #

class JsonlLogger:
    """Append-only JSONL log writer with a stable record schema.

    Each record carries: schema, ts (unix), level, stage, msg, **extra.
    Records are also mirrored to stderr at the configured Python log level
    so operators see them live.
    """

    def __init__(self, path: Path, *, run_id: str, stage: str,
                 mirror_stderr: bool = True) -> None:
        self.path = path
        self.run_id = run_id
        self.stage = stage
        self.mirror_stderr = mirror_stderr
        path.parent.mkdir(parents=True, exist_ok=True)
        # Open in append-binary for atomicity within line.
        self._fh: io.TextIOBase = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def log(self, level: str, msg: str, **extra: Any) -> None:
        rec = {
            "schema": _RECORD_SCHEMA_VERSION,
            "ts": time.time(),
            "level": level,
            "run_id": self.run_id,
            "stage": self.stage,
            "msg": msg,
        }
        rec.update(extra)
        line = json.dumps(rec, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
        if self.mirror_stderr:
            print(f"[{level:>5}] [{self.stage}] {msg}",
                  file=sys.stderr, flush=True)

    def info(self, msg: str, **extra: Any) -> None:
        self.log("INFO", msg, **extra)

    def warn(self, msg: str, **extra: Any) -> None:
        self.log("WARN", msg, **extra)

    def error(self, msg: str, **extra: Any) -> None:
        self.log("ERROR", msg, **extra)

    def debug(self, msg: str, **extra: Any) -> None:
        if os.environ.get("FLAGSHIP_DEBUG") == "1":
            self.log("DEBUG", msg, **extra)

    def close(self) -> None:
        with self._lock:
            try:
                self._fh.flush()
                self._fh.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Stage banners                                                               #
# --------------------------------------------------------------------------- #

def stage_banner(stage: str, *, fh: Optional[io.TextIOBase] = None) -> None:
    """Print a visually distinctive stage start banner."""
    out = fh or sys.stderr
    line = "=" * 70
    out.write(f"\n{line}\n")
    out.write(f"  STAGE: {stage}\n")
    out.write(f"  ts:    {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    out.write(f"{line}\n\n")
    out.flush()


def stage_footer(stage: str, *, state: str, duration_sec: float,
                 fh: Optional[io.TextIOBase] = None) -> None:
    out = fh or sys.stderr
    line = "-" * 70
    out.write(f"\n{line}\n")
    out.write(f"  STAGE: {stage}  state={state}  duration={duration_sec:.1f}s\n")
    out.write(f"{line}\n\n")
    out.flush()


# --------------------------------------------------------------------------- #
# RunRecorder — the high-level driver entry point                              #
# --------------------------------------------------------------------------- #

class RunRecorder:
    """Owns the per-run filesystem layout and writes status/manifest files.

    Layout under ``<run_root>/<run_id>/_pipeline/``:

      status.json                live status JSON (updated per stage tick)
      run.summary.json           final summary (written on exit)
      config.snapshot.{yaml,json}   per-run snapshot of effective config
      <stage>.log                per-stage stderr capture (line-oriented)
      <stage>.jsonl              per-stage structured log
      <stage>.manifest.json      per-stage manifest

    Usage::

        rec = RunRecorder(run_root, run_id, mode_requested, mode_effective)
        rec.start()
        with rec.stage("inventory_repo", inputs=["..."]) as stage:
            stage.log("info", "scanning ...", extra=42)
            stage.outputs.append("runs/<run_id>/inventory/files.jsonl")
        rec.finish(exit_code=0)
    """

    def __init__(self, run_root: Path, run_id: str, *,
                 mode_requested: str, mode_effective: str) -> None:
        self.run_root = run_root.resolve()
        self.run_id = run_id
        self.run_dir = self.run_root / run_id
        self.pipeline_dir = self.run_dir / "_pipeline"
        self.pipeline_dir.mkdir(parents=True, exist_ok=True)
        self._status = RunStatus(
            run_id=run_id,
            started_at=time.time(),
            updated_at=time.time(),
            mode_requested=mode_requested,
            mode_effective=mode_effective,
            stages=[],
            pipeline_state="running",
        )

    @property
    def status_path(self) -> Path:
        return self.pipeline_dir / "status.json"

    @property
    def summary_path(self) -> Path:
        return self.pipeline_dir / "run.summary.json"

    def start(self) -> None:
        self._tick()

    def _tick(self) -> None:
        self._status.updated_at = time.time()
        _atomic_write_json(self.status_path, self._status.to_dict())

    @contextmanager
    def stage(self, name: str, *, inputs: Optional[list[str]] = None
              ) -> Iterator["_StageHandle"]:
        st = StageStatus(name=name, state="running",
                         started_at=time.time(),
                         inputs=list(inputs or []))
        self._status.stages.append(st)
        self._tick()
        stage_banner(name)
        logger = JsonlLogger(
            self.pipeline_dir / f"{name}.jsonl",
            run_id=self.run_id, stage=name,
        )
        handle = _StageHandle(name=name, status=st, logger=logger,
                              run_id=self.run_id,
                              pipeline_dir=self.pipeline_dir)
        try:
            yield handle
        except _StageSkip as skip:
            st.state = "skipped"
            st.skipped_reason = skip.reason
            st.completed_at = time.time()
            st.duration_sec = st.completed_at - (st.started_at or 0)
            handle._emit_manifest()
            stage_footer(name, state="skipped",
                         duration_sec=st.duration_sec)
        except Exception as exc:
            st.state = "failed"
            st.error = f"{type(exc).__name__}: {exc}"
            st.completed_at = time.time()
            st.duration_sec = st.completed_at - (st.started_at or 0)
            handle._emit_manifest(error=st.error)
            stage_footer(name, state="failed",
                         duration_sec=st.duration_sec)
            self._status.pipeline_state = "failed"
            self._tick()
            logger.close()
            raise
        else:
            st.state = "completed"
            st.completed_at = time.time()
            st.duration_sec = st.completed_at - (st.started_at or 0)
            handle._emit_manifest()
            stage_footer(name, state="completed",
                         duration_sec=st.duration_sec)
        finally:
            logger.close()
            st.outputs = list(handle.outputs)
            self._tick()

    def skip_stage(self, name: str, reason: str) -> None:
        """Record a stage as skipped without entering its context manager."""
        st = StageStatus(name=name, state="skipped",
                         started_at=time.time(),
                         completed_at=time.time(),
                         duration_sec=0.0,
                         skipped_reason=reason)
        self._status.stages.append(st)
        # Emit a minimal manifest for the skipped stage too.
        m = StageManifest(stage=name, run_id=self.run_id, state="skipped",
                          started_at=st.started_at or 0.0,
                          completed_at=st.completed_at or 0.0,
                          duration_sec=0.0,
                          skipped_reason=reason)
        _atomic_write_json(self.pipeline_dir / f"{name}.manifest.json",
                           asdict(m))
        self._tick()

    def finish(self, exit_code: int = 0,
               artifacts: Optional[list[str]] = None) -> None:
        now = time.time()
        if self._status.pipeline_state == "running":
            self._status.pipeline_state = "completed" if exit_code == 0 else "failed"
        self._status.completed_at = now
        self._tick()
        summary = RunSummary(
            run_id=self.run_id,
            started_at=self._status.started_at,
            completed_at=now,
            duration_sec=now - self._status.started_at,
            mode_requested=self._status.mode_requested,
            mode_effective=self._status.mode_effective,
            pipeline_state=self._status.pipeline_state,
            stages=list(self._status.stages),
            artifacts=list(artifacts or []),
            exit_code=exit_code,
        )
        _atomic_write_json(self.summary_path, asdict(summary))


class _StageSkip(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _StageHandle:
    """Per-stage helper passed into the with-block."""

    name: str
    status: StageStatus
    logger: JsonlLogger
    run_id: str
    pipeline_dir: Path
    outputs: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        """Raises to mark this stage as skipped."""
        raise _StageSkip(reason)

    def info(self, msg: str, **kw: Any) -> None:
        self.logger.info(msg, **kw)

    def warn(self, msg: str, **kw: Any) -> None:
        self.logger.warn(msg, **kw)

    def error(self, msg: str, **kw: Any) -> None:
        self.logger.error(msg, **kw)

    def debug(self, msg: str, **kw: Any) -> None:
        self.logger.debug(msg, **kw)

    def add_output(self, path: str) -> None:
        self.outputs.append(path)

    def _emit_manifest(self, error: Optional[str] = None) -> None:
        m = StageManifest(
            stage=self.name,
            run_id=self.run_id,
            state=self.status.state,
            started_at=self.status.started_at or 0.0,
            completed_at=self.status.completed_at or 0.0,
            duration_sec=self.status.duration_sec or 0.0,
            inputs=list(self.status.inputs),
            outputs=list(self.outputs),
            error=error,
            skipped_reason=self.status.skipped_reason,
            extra=dict(self.extra),
        )
        # Resolve output paths relative to run_dir to compute size/hash.
        run_dir = self.pipeline_dir.parent
        for o in self.outputs:
            p = run_dir.parent / o if not Path(o).is_absolute() else Path(o)
            sz = _size_of(p)
            if sz >= 0:
                m.output_sizes_bytes[o] = sz
            h = _hash_file(p) if p.is_file() else None
            if h:
                m.output_sha256[o] = h
        _atomic_write_json(
            self.pipeline_dir / f"{self.name}.manifest.json",
            asdict(m),
        )


# --------------------------------------------------------------------------- #
# Chat-REPL logger (slimmer surface)                                          #
# --------------------------------------------------------------------------- #

def get_chat_logger(name: str = "animica.chat") -> logging.Logger:
    """Return a python stdlib logger configured for the REPL.

    Rich-format output goes to stderr; verbose mode controlled by
    ANIMICA_CHAT_DEBUG=1.
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(handler)
    level = (logging.DEBUG if os.environ.get("ANIMICA_CHAT_DEBUG") == "1"
             else logging.INFO)
    logger.setLevel(level)
    logger.propagate = False
    return logger


# --------------------------------------------------------------------------- #
# Test helpers                                                                #
# --------------------------------------------------------------------------- #

def make_run_id(prefix: str = "flagship") -> str:
    """Deterministic-ish run-id mint suitable for tests and live use.

    Format: ``<prefix>-<ts>-<short-uuid>``.
    """
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
