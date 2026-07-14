"""Media checkpoint registry + distribution.

A trained media adapter (LoRA) is a small directory of weights. This module gives it a stable,
content-addressed id, indexes it locally, and packs/unpacks it for distribution — so a miner can
train a model further, publish the checkpoint, and other miners can download + serve it.

Layout:  <checkpoints_dir>/<adapter_id>/   (adapter weights)  +  index.json (registry)
adapter_id = "mad1" + sha3_256(sorted file bytes) hex[:32]  (MediaADapter, content-addressed)
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
import time
from pathlib import Path
from typing import Any, Optional


def checkpoints_dir() -> Path:
    d = Path(os.environ.get("ANIMICA_MEDIA_CHECKPOINTS",
                            str(Path.home() / ".animica" / "media-checkpoints")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return checkpoints_dir() / "index.json"


def _load_index() -> dict[str, Any]:
    p = _index_path()
    if p.is_file():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _save_index(idx: dict[str, Any]) -> None:
    _index_path().write_text(json.dumps(idx, indent=2, sort_keys=True))


def adapter_id(adapter_dir: str | Path) -> str:
    """Content-address an adapter by hashing its files in a stable order."""
    d = Path(adapter_dir)
    files = sorted(p for p in d.rglob("*") if p.is_file() and p.name != "meta.json")
    h = hashlib.sha3_256()
    for f in files:
        h.update(f.relative_to(d).as_posix().encode())
        h.update(f.read_bytes())
    return "mad1" + h.hexdigest()[:32]


def register(adapter_dir: str | Path, *, base_model: str, tier: str = "standard",
             kind: str = "image_lora", notes: str = "") -> dict[str, Any]:
    """Copy a freshly-trained adapter into the registry under its content id and index it."""
    src = Path(adapter_dir)
    if not src.is_dir() or not any(src.rglob("*")):
        raise ValueError(f"adapter dir empty or missing: {adapter_dir}")
    aid = adapter_id(src)
    dst = checkpoints_dir() / aid
    if not dst.exists():
        shutil.copytree(src, dst)
    size = sum(p.stat().st_size for p in dst.rglob("*") if p.is_file())
    rec = {"adapter_id": aid, "base_model": base_model, "tier": tier, "kind": kind,
           "notes": notes, "size": size, "created_at": int(time.time()), "path": str(dst)}
    idx = _load_index()
    idx[aid] = rec
    _save_index(idx)
    (dst / "meta.json").write_text(json.dumps(rec, indent=2))
    return rec


def get(adapter_id_: str) -> Optional[dict[str, Any]]:
    return _load_index().get(adapter_id_)


def path_for(adapter_id_: str) -> Optional[Path]:
    rec = get(adapter_id_)
    if not rec:
        return None
    p = Path(rec["path"])
    return p if p.is_dir() else None


def list_checkpoints() -> list[dict[str, Any]]:
    return sorted(_load_index().values(), key=lambda r: r.get("created_at", 0), reverse=True)


def pack(adapter_id_: str) -> bytes:
    """Tar.gz an adapter for download/distribution."""
    p = path_for(adapter_id_)
    if not p:
        raise ValueError(f"unknown adapter: {adapter_id_}")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(p, arcname=adapter_id_)
    return buf.getvalue()


def install_from_bytes(data: bytes) -> dict[str, Any]:
    """Unpack a downloaded adapter tarball into the registry, verifying its content id."""
    buf = io.BytesIO(data)
    tmp = checkpoints_dir() / f".incoming-{int(time.time()*1000)}"
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            _safe_extract(tar, tmp)
        # The tarball's single top dir is the claimed id; verify by re-hashing.
        subdirs = [d for d in tmp.iterdir() if d.is_dir()]
        if len(subdirs) != 1:
            raise ValueError("malformed adapter tarball")
        claimed = subdirs[0].name
        recomputed = adapter_id(subdirs[0])
        if recomputed != claimed:
            raise ValueError(f"adapter id mismatch: claimed {claimed}, got {recomputed}")
        meta = {}
        mp = subdirs[0] / "meta.json"
        if mp.is_file():
            try:
                meta = json.loads(mp.read_text())
            except Exception:
                meta = {}
        rec = register(subdirs[0], base_model=meta.get("base_model", "unknown"),
                       tier=meta.get("tier", "standard"), kind=meta.get("kind", "image_lora"),
                       notes=meta.get("notes", "downloaded"))
        return rec
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for m in tar.getmembers():
        target = (dest / m.name).resolve()
        if not str(target).startswith(str(dest)):
            raise ValueError(f"unsafe path in tarball: {m.name}")
    tar.extractall(dest)
