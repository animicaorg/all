"""
animica.ena.remote
==================

Client side of distributed pool training/serving. Lets a worker machine on its
own filesystem participate in a pool whose authoritative state + dataset shards
live on a remote ``animica ena serve`` coordinator (e.g. pool.animica.org):

* :class:`RemotePool` — thin HTTP client for the coordinator ``/pool/*`` routes,
  including the distributed-training artifact routes (shard-data download,
  checkpoint upload, promoted-checkpoint download).
* :class:`RemotePoolFacade` — adapts :class:`RemotePool` to the ``ena.pool``
  surface that :mod:`animica.ena.serving` expects, fetching + caching the
  promoted checkpoint locally so the OpenAI-compatible server can load it.
* tar/base64 artifact helpers used to move shard data + checkpoints over the
  coordinator's JSON transport.

Artifacts move as base64-in-JSON over the existing JSON service. That's fine for
LoRA adapters + jsonl shards (MBs); it is not meant for multi-GB full-model
checkpoints.
"""

from __future__ import annotations

import base64
import io
import json
import tarfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote


# ---------------------------------------------------------------------------
# artifact helpers (tar <-> base64; safe extraction)
# ---------------------------------------------------------------------------

def b64_of_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def bytes_of_b64(content_b64: str) -> bytes:
    return base64.b64decode((content_b64 or "").encode("ascii"))


def tar_path_b64(path: str | Path) -> str:
    """gzip-tar a file or directory and return it base64-encoded."""
    p = Path(path)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file():
                    tf.add(str(f), arcname=str(f.relative_to(p)))
        elif p.is_file():
            tf.add(str(p), arcname=p.name)
    return b64_of_bytes(buf.getvalue())


# Files the coordinator needs to merge + serve a LoRA adapter. Uploading only
# these — vs the whole output dir, which also holds the multi-MB tokenizer and
# per-epoch checkpoint subdirs — keeps the upload small and reliable (a big tar
# was silently failing the upload, leaving the coordinator with no weights to
# merge → no warm-start head, nothing servable).
_ADAPTER_FILES = ("adapter_config.json", "adapter_model.safetensors",
                  "adapter_model.bin", "chat_template.jinja")


def tar_adapter_b64(path: str | Path) -> str:
    """gzip-tar JUST the LoRA adapter files from a training output dir (base64).
    Falls back to the whole tree when no adapter is present (e.g. a full
    fine-tune), so non-LoRA runs still upload a usable checkpoint."""
    p = Path(path)
    if p.is_dir() and (p / "adapter_config.json").is_file():
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for name in _ADAPTER_FILES:
                f = p / name
                if f.is_file():
                    tf.add(str(f), arcname=name)
        return b64_of_bytes(buf.getvalue())
    return tar_path_b64(path)


def _safe_extractall(tf: tarfile.TarFile, dest: Path) -> None:
    dest = dest.resolve()
    for m in tf.getmembers():
        target = (dest / m.name).resolve()
        if not str(target).startswith(str(dest) + "/") and target != dest:
            raise ValueError(f"unsafe path in tar archive: {m.name}")
    tf.extractall(dest)  # nosec B202 - members validated above


def extract_tar_b64(content_b64: str, dest: str | Path) -> Path:
    """Extract a base64 gzip-tar into ``dest`` (path-traversal safe)."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(bytes_of_b64(content_b64)), mode="r:gz") as tf:
        try:
            tf.extractall(dest, filter="data")  # py>=3.12 hardened extraction
        except TypeError:  # pragma: no cover - older interpreters
            _safe_extractall(tf, dest)
    return dest


def write_b64_file(content_b64: str, dest: str | Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(bytes_of_b64(content_b64))
    return dest


# ---------------------------------------------------------------------------
# remote coordinator client
# ---------------------------------------------------------------------------

class RemoteError(RuntimeError):
    pass


class RemotePool:
    """HTTP client for a remote pool coordinator's ``/pool/*`` routes.

    ``endpoint`` is the coordinator base (e.g. ``https://pool.animica.org/api/ena``).
    """

    def __init__(self, endpoint: str, *, timeout: float = 120.0,
                 token: Optional[str] = None) -> None:
        self.base = (endpoint or "").rstrip("/")
        self.timeout = timeout
        # Sent as Authorization: Bearer for coordinators with ANIMICA_ENA_API_TOKEN set.
        import os as _os
        self.token = token or _os.environ.get("ANIMICA_ENA_API_TOKEN", "") or None

    def _call(self, path: str, method: str = "GET",
              body: Optional[dict] = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"content-type": "application/json",
                   "user-agent": "animica-ena-trainer"}
        if self.token:
            headers["authorization"] = "Bearer " + self.token
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # nosec B310
                return json.loads(resp.read().decode("utf-8") or "null")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300] if exc.fp else ""
            # Surface coordinator-side {"error": ...} bodies verbatim.
            try:
                msg = json.loads(detail).get("error", detail)
            except Exception:
                msg = detail
            raise RemoteError(f"HTTP {exc.code}: {msg}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RemoteError(f"cannot reach {self.base}: {exc}") from exc

    # -- read --------------------------------------------------------------
    def list_pools(self, status: Optional[str] = None) -> list[dict]:
        path = "/pool/list" + (f"?status={quote(status)}" if status else "")
        return (self._call(path) or {}).get("pools", [])

    def get_pool(self, pool_id: str) -> dict:
        return self._call(f"/pool/status?pool_id={quote(pool_id)}")

    # -- training ----------------------------------------------------------
    def claim(self, pool_id: str, worker_id: str) -> Optional[dict]:
        return (self._call("/pool/claim", "POST",
                           {"pool_id": pool_id, "worker_id": worker_id})
                or {}).get("claimed")

    def download_shard(self, shard_id: str) -> dict:
        return self._call(f"/pool/shard/data?shard_id={quote(shard_id)}")

    def release(self, pool_id: str, shard_id: str, worker_id: str) -> dict:
        return self._call("/pool/release", "POST",
                          {"pool_id": pool_id, "shard_id": shard_id,
                           "worker_id": worker_id})

    def heartbeat(self, pool_id: str, worker_id: str) -> dict:
        return self._call("/pool/heartbeat", "POST",
                          {"pool_id": pool_id, "worker_id": worker_id})

    def upload_checkpoint(self, pool_id: str, shard_id: str,
                          content_b64: str) -> dict:
        return self._call("/pool/checkpoint/upload", "POST",
                          {"pool_id": pool_id, "shard_id": shard_id,
                           "content_b64": content_b64})

    def submit(self, pool_id: str, shard_id: str, *, worker_id: str,
               run_id: Optional[str], checkpoint_path: Optional[str],
               metrics: Optional[dict], miner_address: Optional[str]) -> dict:
        return self._call("/pool/submit", "POST", {
            "pool_id": pool_id, "shard_id": shard_id, "worker_id": worker_id,
            "run_id": run_id, "checkpoint_path": checkpoint_path,
            "metrics": metrics or {}, "miner_address": miner_address})

    def download_eval(self, pool_id: str) -> dict:
        return self._call(f"/pool/eval-data?pool_id={quote(pool_id)}")

    # -- serving -----------------------------------------------------------
    def download_promoted(self, pool_id: str) -> dict:
        return self._call(f"/pool/checkpoint?pool_id={quote(pool_id)}")

    def record_served(self, pool_id: str, worker_id: str, tokens: int, *,
                      address: Optional[str] = None,
                      run_id: Optional[str] = None,
                      latency_ms: Optional[float] = None,
                      served_round: Optional[int] = None) -> dict:
        return self._call("/pool/served", "POST", {
            "pool_id": pool_id, "worker_id": worker_id, "tokens": tokens,
            "address": address, "run_id": run_id, "latency_ms": latency_ms,
            "served_round": served_round})


# ---------------------------------------------------------------------------
# serving facade — adapt RemotePool to the ena.pool surface serving uses
# ---------------------------------------------------------------------------

class RemotePoolFacade:
    """Exposes the subset of ``ena.pool`` that :mod:`animica.ena.serving` calls,
    backed by a remote coordinator. Downloads + caches the promoted checkpoint
    under ``cache_dir`` so the local server can load real weights."""

    def __init__(self, remote: RemotePool, cache_dir: str | Path) -> None:
        self.remote = remote
        self.cache_dir = Path(cache_dir)
        self._cached_hash: Optional[str] = None
        self._cached_path: Optional[str] = None

    def get_served_checkpoint(self, pool_id: str) -> dict[str, Any]:
        info = self.remote.download_promoted(pool_id)
        ckpt = info["checkpoint_hash"]
        if ckpt != self._cached_hash:
            dest = self.cache_dir / pool_id / ckpt
            if info.get("content_b64"):
                extract_tar_b64(info["content_b64"], dest)
            self._cached_hash, self._cached_path = ckpt, str(dest)
        return {
            "pool_id": pool_id,
            "model_id": info.get("model_id") or f"{pool_id}:{ckpt}",
            "base_model": info["base_model"], "round": info.get("round"),
            "checkpoint_hash": ckpt, "path": self._cached_path,
            "merged": info.get("merged", False), "eval": info.get("eval", {}),
            "created_at": info.get("created_at"),
        }

    def record_served(self, pool_id: str, worker_id: str, tokens: int,
                      **kwargs: Any) -> dict[str, Any]:
        try:
            return self.remote.record_served(pool_id, worker_id, tokens, **kwargs)
        except RemoteError:
            # Never let a metering hiccup take the inference server down.
            return {"recorded": False, "tokens": tokens}


class _ServingENA:
    """Minimal ENA-shaped wrapper so ``serving.serve`` can run against a remote
    pool: same ``cfg``/``store`` as the real facade, but ``pool`` is remote."""

    def __init__(self, ena: Any, pool: RemotePoolFacade) -> None:
        self.cfg = ena.cfg
        self.store = ena.store
        self.pool = pool


def coordinator_endpoint(pool_host: str) -> str:
    """Derive the ENA coordinator base URL from a pool host.

    ``pool.animica.org`` → ``https://pool.animica.org/api/ena`` (the nginx route
    that fronts the coordinator). A value that already looks like a URL or
    already targets ``/api/ena`` is passed through.
    """
    host = (pool_host or "").strip().rstrip("/")
    if not host:
        return ""
    if "://" not in host:
        host = "https://" + host
    if "/api/ena" in host or host.endswith("/ena"):
        return host
    return host + "/api/ena"
