from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QCoreApplication

from animica_studio.services.da_engine import DaContributionEngine, DaEngineConfig, DaEngineState


class _FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def status(self):
        return {"enabled": True}

    def configure(self, params):
        self.calls.append(("configure", params))
        return {"ok": True}

    def upload_bytes(self, data: bytes, namespace=None):
        self.calls.append(("upload", len(data)))
        return {"blob_id": "blob-1"}

    def get_blob(self, blob_id: str):
        return b"hello"


def _engine(tmp_path: Path, *, enabled: bool = True, auto_start: bool = True) -> DaContributionEngine:
    app = QCoreApplication.instance() or QCoreApplication([])
    _ = app
    cfg = DaEngineConfig(
        enabled=enabled,
        auto_start=auto_start,
        host_data_dir=str(tmp_path),
        node_data_dir="/data/da",
        mode="quota",
        limit_bytes=2 * 1024**3,
        rpc_url="http://127.0.0.1:8545/rpc",
    )
    e = DaContributionEngine(cfg)
    fake = _FakeClient()
    e.client = lambda: fake  # type: ignore[method-assign]
    return e


def test_validate_config_limit(tmp_path: Path):
    e = _engine(tmp_path)
    ok, msg = e.validate_config(DaEngineConfig(enabled=True, host_data_dir=str(tmp_path), node_data_dir="/data/da", mode="quota", limit_bytes=0, rpc_url="http://x"))
    assert not ok
    assert "greater than 0" in msg


def test_state_transitions_start_stop(tmp_path: Path):
    e = _engine(tmp_path)
    assert e.apply_config(e.config)[0] is True
    assert e.state == DaEngineState.CONFIGURED
    e.start()
    assert e.state == DaEngineState.RUNNING
    e.stop()
    assert e.state == DaEngineState.CONFIGURED


def test_upload_verify_cycle(tmp_path: Path):
    e = _engine(tmp_path)
    test_file = tmp_path / "a.bin"
    test_file.write_bytes(b"hello")
    e.start()
    out = e._run_cycle()
    assert out["uploaded"]
    blob_id = out["uploaded"][0]["blob_id"]
    raw = e.client().get_blob(blob_id)
    assert raw == b"hello"
    e.stop()


def test_autostart_auto_enables_when_config_valid(tmp_path: Path):
    e = _engine(tmp_path, enabled=False, auto_start=True)
    assert e.state == DaEngineState.DISABLED
    changed = e.ensure_enabled_if_autostart()
    assert changed is True
    assert e.config.enabled is True
    assert e.state == DaEngineState.CONFIGURED


def test_start_enables_when_disabled(tmp_path: Path):
    e = _engine(tmp_path, enabled=False, auto_start=True)
    e.start()
    assert e.config.enabled is True
    assert e.state == DaEngineState.RUNNING
    e.stop()


def test_remaining_bytes_uses_quota_limit(tmp_path: Path):
    e = _engine(tmp_path)
    out = {"queued": 0, "uploaded": [], "da_used": 0, "disk_used": 10, "disk_total": 100, "scan_warning": "", "status": {}}
    e._on_cycle(out)
    assert e.metrics.remaining_bytes == e.config.limit_bytes


def test_start_configures_with_node_dir(tmp_path: Path):
    e = _engine(tmp_path)
    fake = e.client()
    e.start()
    cfg_calls = [c for c in fake.calls if c[0] == "configure"]
    assert cfg_calls
    assert cfg_calls[0][1]["dir"] == "/data/da"


def test_rejects_node_dir_outside_allowed_base_dirs(tmp_path: Path):
    e = _engine(tmp_path)
    ok, msg = e.validate_config(
        DaEngineConfig(
            enabled=True,
            host_data_dir=str(tmp_path),
            node_data_dir="/home/employee/da",
            mode="quota",
            limit_bytes=1024,
            rpc_url="http://x",
            allowed_base_dirs=["/data", "/var/lib/animica"],
        )
    )
    assert not ok
    assert "allowed base dirs" in msg
