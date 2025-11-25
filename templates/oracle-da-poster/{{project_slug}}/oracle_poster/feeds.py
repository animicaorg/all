"""
Feeds: pluggable data sources for the Oracle DA Poster.

This module implements a small, dependency-free framework for pulling bytes from
a source (file or command), inferring an optional *numeric* signal for
change-detection, and packaging the result into a `FeedSample` suitable for:

  1) posting as a DA blob (payload/size/content_type)
  2) computing a commitment (sha256) to send on-chain
  3) deciding whether to skip an update when changes are too small (basis points)

The design intentionally avoids heavy dependencies to keep the template lean.
You can extend it with HTTP, gRPC, etc., in your project if needed.

Key ideas
---------
- **Payload-first**: we always return raw bytes to be posted to DA exactly as
  produced by the source.
- **Gentle inference**: we *try* to extract a numeric `value` for
  change-detection. If we can’t, updates are never filtered by MIN_CHANGE_BPS.
- **Deterministic commitment**: commitment is sha256(payload), hex-encoded with
  a `0x` prefix for ABI convenience.
- **Safety rails**: size limits, timeouts, and clear exceptions.

Typical usage
-------------
    from oracle_poster.config import resolve_config, validate_config
    from oracle_poster.feeds import build_feed

    cfg = resolve_config(".env")
    validate_config(cfg)
    feed = build_feed(cfg)

    # previous_value may be persisted across iterations to apply change thresholds
    previous_value = None
    sample = feed.sample(previous_value=previous_value, min_change_bps=cfg.min_change_bps)

    # sample.payload -> bytes for DA
    # sample.commitment_hex -> 0x… for the on-chain oracle update
    # sample.changed -> whether to proceed if throttling by basis-points
"""

from __future__ import annotations

import json
import math
import shlex
import subprocess
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

from . import PosterEnv, get_logger

_LOG = get_logger("oracle_poster.feeds")


# --------------------------------------------------------------------------------------
# Data types
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FeedSample:
    """
    Result of a single feed pull.

    Attributes:
        payload: Raw bytes to post as a DA blob.
        size: Length in bytes of payload (cached for convenience).
        content_type: Best-effort content type (e.g., application/json).
        commitment_hex: 0x-prefixed hex sha256(payload).
        value: Optional numeric value inferred from payload for change detection.
        changed: Whether the numeric change (if both prev and current exist)
                 exceeds or equals the configured basis-points threshold.
                 If value is None or threshold is None, this will be True.
                 (I.e., we don't suppress in the absence of a usable numeric.)
        meta: Free-form details (e.g., hints about how `value` was inferred).
    """
    payload: bytes
    size: int
    content_type: str
    commitment_hex: str
    value: Optional[float]
    changed: bool
    meta: Dict[str, Any]


class FeedError(RuntimeError):
    """Raised on recoverable feed issues (size limits, decoding errors, etc.)."""


# --------------------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------------------

def _commitment_hex(payload: bytes) -> str:
    return "0x" + sha256(payload).hexdigest()


def _is_probably_json_bytes(b: bytes) -> bool:
    # Lightweight sniff: skip leading whitespace, check first non-ws char.
    s = b.lstrip()[:1]
    return s in (b"{", b"[")


def _try_decode_utf8(b: bytes) -> Optional[str]:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    """
    Flatten nested dict/list structures into dotted keys for heuristic search.
    Example: {"a": {"b": [10, {"c": 42}]}} -> {"a.b[0]": 10, "a.b[1].c": 42}
    """
    out: Dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(_flatten(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{prefix}[{i}]"
            out.update(_flatten(v, key))
    else:
        out[prefix or ""] = obj
    return out


_NUMERIC_HINT_KEYS = (
    "price",
    "value",
    "val",
    "amount",
    "last",
    "close",
    "avg",
    "average",
    "median",
)


def _infer_numeric_from_json(data: Any) -> Tuple[Optional[float], Dict[str, Any]]:
    """
    Try hard to extract a single numeric value from a JSON-like object.
    Heuristics:
      1) If it's already a number -> done.
      2) If it's a dict, prefer keys with numeric hint names.
      3) Otherwise, first numeric in flattened traversal.
    """
    meta: Dict[str, Any] = {"strategy": None, "key": None}

    if isinstance(data, (int, float)) and not isinstance(data, bool):
        meta["strategy"] = "root_number"
        return float(data), meta

    flat = _flatten(data)

    # Try hint keys
    for k, v in flat.items():
        last = k.split(".")[-1]
        base = last.split("[", 1)[0]
        if base.lower() in _NUMERIC_HINT_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
            meta["strategy"] = "hint_key"
            meta["key"] = k
            return float(v), meta

    # Fallback: first numeric value
    for k, v in flat.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            meta["strategy"] = "first_numeric"
            meta["key"] = k
            return float(v), meta

    meta["strategy"] = "none"
    return None, meta


def _infer_numeric_from_text(text: str) -> Tuple[Optional[float], Dict[str, Any]]:
    """
    If the whole text is a number (int/float), parse it.
    """
    meta: Dict[str, Any] = {"strategy": "none"}
    s = text.strip()
    if not s:
        return None, meta
    try:
        # Robust float parse; reject NaN/inf
        val = float(s)
        if math.isnan(val) or math.isinf(val):
            return None, meta
        meta["strategy"] = "scalar_text"
        return val, meta
    except ValueError:
        return None, meta


def _infer_value_and_content_type(payload: bytes) -> Tuple[Optional[float], Dict[str, Any], str]:
    """
    Inspect bytes and return (numeric_value, meta, content_type).
    """
    meta: Dict[str, Any] = {}
    if _is_probably_json_bytes(payload):
        text = _try_decode_utf8(payload)
        if text is not None:
            try:
                data = json.loads(text)
                v, meta = _infer_numeric_from_json(data)
                return v, meta, "application/json"
            except json.JSONDecodeError:
                pass  # Fall through to text sniff
    # Plain text?
    text = _try_decode_utf8(payload)
    if text is not None:
        v, tmeta = _infer_numeric_from_text(text)
        meta.update(tmeta)
        return v, meta, "text/plain; charset=utf-8"

    # Opaque bytes
    meta["strategy"] = "bytes"
    return None, meta, "application/octet-stream"


def _bps_changed(prev: Optional[float], curr: Optional[float], min_change_bps: Optional[int]) -> bool:
    """
    Basis-points (1/100 of a percent) threshold. If either value is None,
    return True so we don't suppress updates blindly.
    """
    if min_change_bps is None:
        return True
    if prev is None or curr is None:
        return True
    baseline = abs(prev)
    # If baseline is tiny, use absolute comparison against 1 bp of 1.0 to avoid dead-zone.
    if baseline < 1e-12:
        return abs(curr - prev) >= (min_change_bps / 10000.0)
    return abs(curr - prev) >= (baseline * (min_change_bps / 10000.0))


def _enforce_size(payload: bytes, max_bytes: int) -> None:
    if len(payload) > max_bytes:
        raise FeedError(
            f"Payload size {len(payload)} exceeds DA_MAX_BLOB_BYTES={max_bytes}. "
            "Consider compressing or increasing the limit."
        )


# --------------------------------------------------------------------------------------
# Feed base & implementations
# --------------------------------------------------------------------------------------

class BaseFeed:
    """
    A minimal base class. Subclasses override `_read_bytes()`.

    The `sample()` method orchestrates:
      - reading raw bytes
      - size check
      - content-type & numeric inference
      - commitment computation
      - change detection
    """

    def __init__(self, cfg: PosterEnv) -> None:
        self.cfg = cfg

    # --- Overridable ----------------------------------------------------------

    def _read_bytes(self, timeout_sec: int) -> bytes:
        """Return raw bytes from the source. Must be implemented by subclasses."""
        raise NotImplementedError

    # --- Orchestration --------------------------------------------------------

    def sample(
        self,
        *,
        previous_value: Optional[float],
        min_change_bps: Optional[int],
    ) -> FeedSample:
        started = time.time()
        payload = self._read_bytes(timeout_sec=self.cfg.http_timeout_sec)
        elapsed_ms = int((time.time() - started) * 1000)

        _enforce_size(payload, self.cfg.da_max_blob_bytes)
        value, meta, content_type = _infer_value_and_content_type(payload)
        changed = _bps_changed(previous_value, value, min_change_bps)
        commit = _commitment_hex(payload)

        meta = {
            **meta,
            "elapsed_ms": elapsed_ms,
            "size": len(payload),
            "content_type": content_type,
            "changed_by_bps": changed,
        }

        return FeedSample(
            payload=payload,
            size=len(payload),
            content_type=content_type,
            commitment_hex=commit,
            value=value,
            changed=changed,
            meta=meta,
        )


class FileFeed(BaseFeed):
    """
    Read the entire contents of a file path on each sample.

    Notes:
      - Text vs JSON vs binary is detected automatically.
      - Consider pairing with a preprocessor that writes the file atomically.
    """

    def __init__(self, cfg: PosterEnv, path: str) -> None:
        super().__init__(cfg)
        self.path = Path(path).expanduser().resolve()

    def _read_bytes(self, timeout_sec: int) -> bytes:
        try:
            data = self.path.read_bytes()
            _LOG.debug("FileFeed read %d bytes from %s", len(data), self.path)
            return data
        except FileNotFoundError as e:
            raise FeedError(f"Source file not found: {self.path}") from e
        except OSError as e:
            raise FeedError(f"Error reading file {self.path}: {e}") from e


class CommandFeed(BaseFeed):
    """
    Execute a shell command and use its stdout bytes as the payload.

    Safety tips:
      - Prefer an *explicit* command string you control. This template uses
        `/bin/sh -c` to keep things simple.
      - Enforce timeouts via HTTP_TIMEOUT_SEC.
      - Ensure your command prints *only* the desired bytes to stdout.
    """

    def __init__(self, cfg: PosterEnv, command: str) -> None:
        super().__init__(cfg)
        self.command = command

    def _read_bytes(self, timeout_sec: int) -> bytes:
        # We use shell for convenience; projects can switch to a list/execve form as needed.
        try:
            _LOG.debug("CommandFeed executing: %s (timeout=%ss)", self.command, timeout_sec)
            # text=False -> bytes
            proc = subprocess.run(
                ["/bin/sh", "-c", self.command],
                check=False,
                capture_output=True,
                timeout=max(1, int(timeout_sec)),
                text=False,
            )
        except subprocess.TimeoutExpired as e:
            raise FeedError(f"Command timed out after {timeout_sec}s: {self.command}") from e
        except OSError as e:
            raise FeedError(f"Failed to execute command: {self.command} ({e})") from e

        if proc.returncode != 0:
            stderr = (proc.stderr or b"").decode("utf-8", errors="replace")
            raise FeedError(f"Command exited with code {proc.returncode}: {self.command}\n{stderr}")

        out = proc.stdout or b""
        _LOG.debug("CommandFeed captured %d bytes from stdout", len(out))
        return out


# --------------------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------------------

def build_feed(cfg: PosterEnv) -> BaseFeed:
    """
    Choose the appropriate feed based on configuration.

    Priority:
      1) SOURCE_FILE_PATH -> FileFeed
      2) SOURCE_COMMAND   -> CommandFeed
      3) Neither set      -> a no-op CommandFeed that prints nothing (warns)

    The last option is provided to keep the service runnable even if the source
    is not configured yet. It will yield empty payloads (size=0), which are
    still committed but unlikely to be useful. Pair with config validation to
    ensure you don't ship this to production.
    """
    if cfg.source_file_path:
        _LOG.info("Using FileFeed: %s", cfg.source_file_path)
        return FileFeed(cfg, cfg.source_file_path)
    if cfg.source_command:
        _LOG.info("Using CommandFeed: %s", cfg.source_command)
        return CommandFeed(cfg, cfg.source_command)

    _LOG.warning("No source configured; using a stub CommandFeed that emits empty payloads.")
    return CommandFeed(cfg, "printf ''")
