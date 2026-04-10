from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin

import httpx

from core.snapshot.manifest import SnapshotManifest, iter_manifests

log = logging.getLogger("animica.snapshot.download")


def fetch_manifests(url: str, *, timeout: float = 20.0) -> list[SnapshotManifest]:
    log.debug("Fetching snapshot manifest", extra={"url": url})
    with httpx.Client(timeout=timeout) as client:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
    return list(iter_manifests(payload))


def _chunk_url(manifest_url: str, chunk_name: str, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    base = manifest_url.rsplit("/", 1)[0] + "/"
    return urljoin(base, chunk_name)


def download_chunks(
    manifest_url: str,
    manifest: SnapshotManifest,
    dest_dir: Path,
    *,
    timeout: float = 600.0,
) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for chunk in manifest.chunks:
            chunk_path = dest_dir / chunk.name
            url = _chunk_url(manifest_url, chunk.name, chunk.url)
            existing = chunk_path.stat().st_size if chunk_path.exists() else 0
            headers = {}
            mode = "wb"
            if existing and existing < chunk.size:
                headers["Range"] = f"bytes={existing}-"
                mode = "ab"
                log.info(
                    "Resuming snapshot chunk",
                    extra={"chunk": chunk.name, "have": existing, "size": chunk.size},
                )
            else:
                if existing >= chunk.size > 0:
                    downloaded.append(chunk_path)
                    continue
            response = client.get(url, headers=headers)
            if response.status_code not in (200, 206):
                raise RuntimeError(
                    f"Failed to download {chunk.name}: HTTP {response.status_code}"
                )
            with open(chunk_path, mode) as handle:
                handle.write(response.content)
            downloaded.append(chunk_path)
            log.info(
                "Downloaded snapshot chunk",
                extra={"chunk": chunk.name, "bytes": chunk_path.stat().st_size},
            )
    return downloaded
