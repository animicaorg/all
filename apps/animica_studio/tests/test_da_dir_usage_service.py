from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from animica_studio.services.da_dir_usage_service import DaDirUsageService


def test_get_dir_size_bytes_sums_nested_files(tmp_path: Path, monkeypatch):
    (tmp_path / "a.bin").write_bytes(b"a" * 10)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.bin").write_bytes(b"b" * 20)

    svc = DaDirUsageService(cache_ttl_seconds=0)
    monkeypatch.setattr(svc, "_try_du_fast_path", lambda _p: None)

    assert svc.get_dir_size_bytes(str(tmp_path)) == 30


def test_symlink_outside_not_traversed(tmp_path: Path, monkeypatch):
    outside = tmp_path.parent / "outside.bin"
    outside.write_bytes(b"z" * 100)
    local = tmp_path / "inside.bin"
    local.write_bytes(b"i" * 15)

    link = tmp_path / "outside_link"
    os.symlink(outside, link)

    svc = DaDirUsageService(cache_ttl_seconds=0)
    monkeypatch.setattr(svc, "_try_du_fast_path", lambda _p: None)
    size = svc.get_dir_size_bytes(str(tmp_path))

    assert size >= 15
    assert size < 115


def test_unreadable_file_is_skipped_gracefully(tmp_path: Path, monkeypatch):
    readable = tmp_path / "ok.bin"
    readable.write_bytes(b"o" * 7)
    blocked = tmp_path / "blocked.bin"
    blocked.write_bytes(b"x" * 99)

    svc = DaDirUsageService(cache_ttl_seconds=0)
    monkeypatch.setattr(svc, "_try_du_fast_path", lambda _p: None)


    def fake_scan_with_permission_error(root: Path, budget_seconds: float):
        total = 0
        warnings: list[str] = []
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    if entry.name == blocked.name:
                        raise PermissionError("denied")
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except PermissionError as exc:
                    warnings.append(str(exc))
        return total, "; ".join(warnings), False

    monkeypatch.setattr(svc, "_scan_with_scandir", fake_scan_with_permission_error)
    snap = svc.get_snapshot(str(tmp_path))

    assert snap.used_bytes == 7
    assert "denied" in snap.warning
