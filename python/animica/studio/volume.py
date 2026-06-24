"""Volumes — named persistent storage mounted into functions.

Dev/local: a volume is a directory under ``~/.animica/studio/volumes/<name>``
mounted (bind) into the sandbox at ``/mnt/<name>``. State persists across calls
on this machine.

Distributed (target): a volume is content-addressed and backed by the Animica
DA layer (``animica da submit/get``) or an S3/MinIO bucket, so any worker that
runs a function sees the same data. That replication path is a build-out tracked
in ROADMAP.md; ``commit()``/``reload()`` raise a clear ``NotImplementedError``
in distributed mode rather than silently diverging.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _volumes_root() -> Path:
    root = Path(os.environ.get("ANIMICA_STUDIO_VOLUME_DIR") or (Path.home() / ".animica" / "studio" / "volumes"))
    return root


@dataclass(frozen=True)
class Volume:
    name: str
    mount_path: str = ""
    persisted: bool = True

    @staticmethod
    def from_name(name: str, *, mount_path: str | None = None, create: bool = True) -> "Volume":
        mp = mount_path or f"/mnt/{name}"
        if create:
            local = _volumes_root() / name
            local.mkdir(parents=True, exist_ok=True)
        return Volume(name=name, mount_path=mp, persisted=True)

    @staticmethod
    def ephemeral(name: str = "scratch", *, mount_path: str | None = None) -> "Volume":
        return Volume(name=name, mount_path=mount_path or f"/mnt/{name}", persisted=False)

    def local_path(self) -> Path:
        return _volumes_root() / self.name

    def to_spec(self) -> dict:
        return {"name": self.name, "mount_path": self.mount_path, "persisted": self.persisted}

    def commit(self) -> None:
        """Flush local writes to the replicated backend (distributed mode)."""
        if os.environ.get("ANIMICA_STUDIO_MODE", "auto") == "remote":
            raise NotImplementedError(
                "distributed volume commit (DA-backed) is not implemented yet — see ROADMAP.md"
            )
        # local mode: writes already on disk, nothing to do.
