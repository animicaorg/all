from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

log = logging.getLogger("animica.p2p.store")

_UMASK_APPLIED = False


def apply_umask_from_env() -> Optional[int]:
    """
    Apply ANIMICA_UMASK (octal) if set. Returns previous umask or None.
    """
    global _UMASK_APPLIED
    if _UMASK_APPLIED:
        return None
    umask_raw = os.environ.get("ANIMICA_UMASK")
    if not umask_raw:
        return None
    try:
        mask = int(umask_raw, 8)
    except ValueError:
        log.warning("Invalid ANIMICA_UMASK value %r", umask_raw)
        return None
    _UMASK_APPLIED = True
    return os.umask(mask)


def _try_chmod(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except (OSError, PermissionError):
        return


def _try_chgrp(path: Path) -> None:
    try:
        gid = os.getgid()
        os.chown(path, -1, gid)
    except (OSError, PermissionError):
        return


def _dir_is_writable(path: Path) -> bool:
    try:
        tmp_path = path / f".writecheck-{uuid.uuid4().hex}"
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write("ok")
        tmp_path.unlink(missing_ok=True)  # type: ignore[call-arg]
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class WritablePath:
    path: Path
    fallback_path: Optional[Path] = None
    used_fallback: bool = False


def ensure_writable(path: Path) -> WritablePath:
    """
    Ensure a store directory is writable. If not, fall back to a per-user store under
    ~/.animica/p2p-local while preserving the relative path.
    """
    apply_umask_from_env()
    path = path.expanduser()
    target_dir = path if path.suffix == "" else path.parent
    try:
        target_dir.mkdir(mode=0o775, parents=True, exist_ok=True)
    except Exception:
        pass
    _try_chgrp(target_dir)
    _try_chmod(target_dir, 0o775)

    home = Path(os.environ.get("ANIMICA_HOME", Path.home() / ".animica")).expanduser()
    fallback_root = home / "p2p-local"
    try:
        rel = target_dir.relative_to(home)
    except ValueError:
        rel = Path(target_dir.name)
    fallback_dir = fallback_root / rel
    try:
        fallback_dir.mkdir(mode=0o775, parents=True, exist_ok=True)
    except Exception:
        pass
    _try_chgrp(fallback_dir)
    _try_chmod(fallback_dir, 0o775)
    fallback_path = fallback_dir / path.name if path.suffix else fallback_dir
    if _dir_is_writable(target_dir):
        return WritablePath(path=path, fallback_path=fallback_path, used_fallback=False)
    return WritablePath(path=fallback_path, fallback_path=fallback_path, used_fallback=True)


def read_peers_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return {"peers": []}


def write_peers_json(path: Path, data: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(mode=0o775, parents=True, exist_ok=True)
    except Exception:
        pass
    tmp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(tmp_path, path)
        return True
    except Exception as exc:
        log.warning("Failed to write peers json %s: %s", path, exc)
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return False


def merge_peer_snapshots(target: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    target_peers = {p.get("peer_id"): dict(p) for p in target.get("peers", []) if p}
    for peer in incoming.get("peers", []) or []:
        if not isinstance(peer, dict):
            continue
        pid = peer.get("peer_id")
        if not pid:
            continue
        existing = target_peers.get(pid, {"peer_id": pid, "addrs": []})
        addrs = list(dict.fromkeys((existing.get("addrs") or []) + (peer.get("addrs") or [])))
        existing.update(peer)
        existing["addrs"] = addrs
        target_peers[pid] = existing
    return {"peers": list(target_peers.values())}


def merge_peer_files(target_path: Path, source_paths: Iterable[Path]) -> bool:
    target = read_peers_json(target_path) if target_path.exists() else {"peers": []}
    merged = target
    changed = False
    for src in source_paths:
        if not src.exists():
            continue
        incoming = read_peers_json(src)
        merged = merge_peer_snapshots(merged, incoming)
        changed = True
    if not changed:
        return False
    return write_peers_json(target_path, merged)
