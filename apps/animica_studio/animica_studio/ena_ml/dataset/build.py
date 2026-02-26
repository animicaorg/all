from __future__ import annotations

from pathlib import Path

from .manifest import write_manifest


def bootstrap_dataset(texts: list[str], out_dir: Path, shard_target_bytes: int = 128 * 1024 * 1024) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    shards: list[Path] = []
    shard_idx = 0
    buf = bytearray()
    for text in texts:
        line = (text.strip() + "\n").encode("utf-8", errors="replace")
        if buf and len(buf) + len(line) > shard_target_bytes:
            p = out_dir / f"shard-{shard_idx:05d}.txt"
            p.write_bytes(bytes(buf))
            shards.append(p)
            shard_idx += 1
            buf.clear()
        buf.extend(line)
    if buf:
        p = out_dir / f"shard-{shard_idx:05d}.txt"
        p.write_bytes(bytes(buf))
        shards.append(p)
    manifest_path = out_dir / "manifest.json"
    write_manifest(shards, manifest_path, provenance={"sources": "local-bootstrap"})
    return manifest_path
