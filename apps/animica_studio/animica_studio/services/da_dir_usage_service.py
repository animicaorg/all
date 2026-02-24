from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class DaDirUsageSnapshot:
    path: str
    used_bytes: int = 0
    scan_in_progress: bool = False
    warning: str = ""
    disk_used_bytes: int = 0
    disk_total_bytes: int = 0
    timestamp_monotonic: float = 0.0


class DaDirUsageService:
    """Compute DA directory size safely without traversing symlink targets."""

    def __init__(self, cache_ttl_seconds: float = 5.0, scan_time_budget_seconds: float = 2.5) -> None:
        self._cache_ttl = max(float(cache_ttl_seconds), 0.0)
        self._scan_budget = max(float(scan_time_budget_seconds), 0.25)
        self._last_snapshot = DaDirUsageSnapshot(path="", timestamp_monotonic=0.0)

    @property
    def last_snapshot(self) -> DaDirUsageSnapshot:
        return self._last_snapshot

    def get_dir_size_bytes(self, path: str) -> int:
        return self.get_snapshot(path).used_bytes

    def get_snapshot(self, path: str) -> DaDirUsageSnapshot:
        raw = str(path or "").strip()
        p = Path(raw).expanduser()
        now = time.monotonic()
        if self._last_snapshot.path == str(p) and (now - self._last_snapshot.timestamp_monotonic) < self._cache_ttl:
            return self._last_snapshot

        disk_used = 0
        disk_total = 0
        try:
            disk = shutil.disk_usage(p if p.exists() else p.parent)
            disk_used, disk_total = int(disk.used), int(disk.total)
        except Exception:
            pass

        if not p.exists():
            snap = DaDirUsageSnapshot(
                path=str(p),
                used_bytes=0,
                warning="Dir missing; will be created on start",
                disk_used_bytes=disk_used,
                disk_total_bytes=disk_total,
                timestamp_monotonic=now,
            )
            self._last_snapshot = snap
            return snap

        used = self._try_du_fast_path(p)
        warning = ""
        in_progress = False
        if used is None:
            used, warning, in_progress = self._scan_with_scandir(p, budget_seconds=self._scan_budget)

        snap = DaDirUsageSnapshot(
            path=str(p),
            used_bytes=max(int(used), 0),
            scan_in_progress=in_progress,
            warning=warning,
            disk_used_bytes=disk_used,
            disk_total_bytes=disk_total,
            timestamp_monotonic=now,
        )
        self._last_snapshot = snap
        return snap

    def _try_du_fast_path(self, path: Path) -> int | None:
        # Fast path disabled: rely on pure-Python scandir to avoid direct subprocess usage.
        return None

    def _scan_with_scandir(self, root: Path, budget_seconds: float) -> tuple[int, str, bool]:
        total = 0
        warnings: list[str] = []
        deadline = time.monotonic() + budget_seconds
        stack = [root]
        while stack:
            if time.monotonic() > deadline:
                warnings.append("Size scan in progress…")
                return total, "; ".join(warnings[:3]), True
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if entry.name.startswith("."):
                            continue
                        try:
                            if entry.is_symlink():
                                try:
                                    total += entry.stat(follow_symlinks=False).st_size
                                except OSError as exc:
                                    warnings.append(f"Skipped symlink: {entry.path} ({exc})")
                                continue
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                total += entry.stat(follow_symlinks=False).st_size
                        except PermissionError as exc:
                            msg = f"Skipped unreadable entry: {entry.path} ({exc})"
                            log.warning(msg)
                            warnings.append(msg)
                        except OSError as exc:
                            msg = f"Skipped inaccessible entry: {entry.path} ({exc})"
                            log.warning(msg)
                            warnings.append(msg)
            except PermissionError as exc:
                msg = f"Skipped unreadable directory: {current} ({exc})"
                log.warning(msg)
                warnings.append(msg)
            except OSError as exc:
                msg = f"Skipped inaccessible directory: {current} ({exc})"
                log.warning(msg)
                warnings.append(msg)
        return total, "; ".join(warnings[:3]), False
