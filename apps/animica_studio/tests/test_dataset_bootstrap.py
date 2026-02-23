from __future__ import annotations

import threading
from pathlib import Path
from urllib.error import HTTPError

from animica_studio.services.dataset_bootstrap_service import (
    BootstrapOptions,
    DatasetBootstrapService,
    DownloadManager,
    ProviderUrlCandidate,
    _ShardWriter,
)
from animica_studio.services.dataset_manager import DatasetManager


def test_bootstrap_estimate_contains_guardrails() -> None:
    svc = DatasetBootstrapService()
    est = svc.estimate("big")
    assert est["target_bytes"] >= 50 * 1024**3
    assert est["disk_needed_bytes"] > est["target_bytes"]
    assert est["download_bytes"] > 0
    assert len(est["eta_hours_range"]) == 2


def test_bootstrap_cancel_writes_resumable_state(tmp_path: Path) -> None:
    svc = DatasetBootstrapService()
    cancel = threading.Event()
    cancel.set()
    out = svc.bootstrap(
        options=BootstrapOptions(name="cancel-test", size_preset="starter", output_dir=tmp_path / "cancel-test", shard_size_bytes=1024 * 1024),
        progress_cb=lambda _p: None,
        cancel=cancel,
    )
    assert out["cancelled"] is True
    assert (tmp_path / "cancel-test" / "build_state.json").exists()


def test_shard_writer_rotates_and_hashes(tmp_path: Path) -> None:
    writer = _ShardWriter(tmp_path / "shards", shard_size_bytes=120)
    writer.write({"text": "a" * 80})
    writer.write({"text": "b" * 80})
    shards = writer.close()
    assert len(shards) >= 2
    assert all(s["size_bytes"] > 0 and s["sha256"] for s in shards)


def test_dataset_manager_exposes_bootstrap_estimate() -> None:
    manager = DatasetManager()
    est = manager.estimate_bootstrap("starter")
    assert est["target_bytes"] >= 5 * 1024**3


def test_download_manager_mirror_fallback_records_diagnostics(monkeypatch, tmp_path: Path) -> None:
    manager = DownloadManager(tmp_path)

    class _Resp:
        status = 200
        headers = {"Content-Length": "4", "Content-Type": "text/plain"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, n: int = -1) -> bytes:
            if n == 200:
                return b""
            if hasattr(self, "_done"):
                return b""
            self._done = True
            return b"ok!!"

    def _fake_urlopen(req, timeout=30):  # noqa: ANN001,ARG001
        url = req.full_url
        if "bad" in url:
            raise HTTPError(url, 404, "Not Found", hdrs={"Content-Type": "text/plain"}, fp=None)
        return _Resp()

    monkeypatch.setattr("animica_studio.services.dataset_bootstrap_service.urlopen", _fake_urlopen)
    out = manager.download_with_mirrors(
        source="wikipedia",
        candidates=[
            ProviderUrlCandidate(name="bad", url="https://example.invalid/bad"),
            ProviderUrlCandidate(name="good", url="https://example.invalid/good"),
        ],
        dest=tmp_path / "sample.txt",
        progress_cb=lambda _p: None,
        cancel=threading.Event(),
    )
    assert out.exists()
    diags = manager.diagnostics()
    assert diags and diags[0]["status"] == 404


def test_download_manager_offline_mode_requires_cache(tmp_path: Path) -> None:
    manager = DownloadManager(tmp_path, source_settings={"offline_mode": True})
    cached = tmp_path / "cached.txt"
    cached.write_text("hello", encoding="utf-8")
    out = manager.download_with_mirrors(
        source="wikipedia",
        candidates=[ProviderUrlCandidate(name="cached", url="https://example.invalid/cached")],
        dest=cached,
        progress_cb=lambda _p: None,
        cancel=threading.Event(),
    )
    assert out == cached
