"""Unit tests for DAContributionService — no Qt, no network required."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# _validate_config
# ---------------------------------------------------------------------------


def test_validate_config_empty_directory_raises():
    from animica_studio.services.da_contribution_service import _validate_config
    with pytest.raises(ValueError, match="must not be empty"):
        _validate_config("", 10 * 1024 ** 3)


def test_validate_config_below_1gb_raises():
    from animica_studio.services.da_contribution_service import _validate_config
    with pytest.raises(ValueError, match="at least 1 GB"):
        _validate_config("/tmp", 500 * 1024 * 1024)  # 500 MB


def test_validate_config_valid_path(tmp_path):
    from animica_studio.services.da_contribution_service import _validate_config
    _validate_config(str(tmp_path), 2 * 1024 ** 3)  # should not raise


def test_validate_config_not_writable(tmp_path):
    from animica_studio.services.da_contribution_service import _validate_config
    # Make a read-only directory (skip on Windows/root where chmod is unreliable)
    if os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0):
        pytest.skip("chmod test not meaningful on Windows or as root")
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    ro_dir.chmod(0o555)
    try:
        with pytest.raises(ValueError, match="not writable"):
            _validate_config(str(ro_dir), 2 * 1024 ** 3)
    finally:
        ro_dir.chmod(0o755)


def test_validate_config_path_is_file_raises(tmp_path):
    from animica_studio.services.da_contribution_service import _validate_config
    f = tmp_path / "somefile.txt"
    f.write_text("hello")
    with pytest.raises(ValueError, match="not a directory"):
        _validate_config(str(f), 2 * 1024 ** 3)


# ---------------------------------------------------------------------------
# DAContributionService.configure
# ---------------------------------------------------------------------------


def test_configure_creates_directory(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    target = tmp_path / "new_store"
    result = svc.configure(enabled=True, directory=str(target), max_bytes=2 * 1024 ** 3)
    assert result["ok"] is True
    assert target.is_dir()


def test_configure_stores_settings(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService, ReserveMode
    svc = DAContributionService()
    result = svc.configure(
        enabled=True,
        directory=str(tmp_path),
        max_bytes=5 * 1024 ** 3,
        reserve_mode="preallocate",
    )
    assert result["ok"] is True
    assert svc._enabled is True
    assert svc._limit_bytes == 5 * 1024 ** 3
    assert svc._reserve_mode == ReserveMode.PREALLOCATE


def test_configure_invalid_returns_error():
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    result = svc.configure(enabled=True, directory="", max_bytes=2 * 1024 ** 3)
    assert result["ok"] is False
    assert "error" in result


def test_configure_unknown_reserve_mode_defaults_to_quota(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService, ReserveMode
    svc = DAContributionService()
    svc.configure(enabled=True, directory=str(tmp_path), max_bytes=2 * 1024 ** 3, reserve_mode="bogus")
    assert svc._reserve_mode == ReserveMode.QUOTA


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


def test_start_without_configure_returns_error():
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    result = svc.start()
    assert result["ok"] is False


def test_start_when_disabled_returns_error(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    svc.configure(enabled=False, directory=str(tmp_path), max_bytes=2 * 1024 ** 3)
    result = svc.start()
    assert result["ok"] is False
    assert "disabled" in result["error"].lower()


def test_start_and_stop(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    svc.configure(enabled=True, directory=str(tmp_path), max_bytes=2 * 1024 ** 3)
    assert svc.start()["ok"] is True
    assert svc._running is True
    assert svc.stop()["ok"] is True
    assert svc._running is False


def test_start_idempotent(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    svc.configure(enabled=True, directory=str(tmp_path), max_bytes=2 * 1024 ** 3)
    svc.start()
    result = svc.start()
    assert result["ok"] is True
    assert "already" in result.get("message", "").lower()


def test_stop_when_not_running_ok():
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    result = svc.stop()
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_offline_before_configure():
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    st = svc.status()
    assert st.health == "offline"
    assert st.enabled is False
    assert st.running is False


def test_status_online_after_start(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    svc.configure(enabled=True, directory=str(tmp_path), max_bytes=2 * 1024 ** 3)
    svc.start()
    st = svc.status()
    assert st.health == "online"
    assert st.running is True
    assert st.enabled is True


def test_status_misconfigured_on_last_error(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    svc.configure(enabled=True, directory=str(tmp_path), max_bytes=2 * 1024 ** 3)
    svc.start()
    svc._last_error = "something went wrong"
    st = svc.status()
    assert st.health == "misconfigured"


def test_status_used_bytes_reflects_chunks(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService, _ChunkRecord
    svc = DAContributionService()
    svc.configure(enabled=True, directory=str(tmp_path), max_bytes=2 * 1024 ** 3)
    svc._chunks = [_ChunkRecord(name="a", size=1000), _ChunkRecord(name="b", size=2000)]
    st = svc.status()
    assert st.used_bytes == 3000
    assert st.available_bytes == 2 * 1024 ** 3 - 3000


# ---------------------------------------------------------------------------
# Cap enforcement (LRU eviction)
# ---------------------------------------------------------------------------


def test_evict_lru_removes_oldest_first(tmp_path):
    import time as _time
    from animica_studio.services.da_contribution_service import DAContributionService, _ChunkRecord
    svc = DAContributionService()
    # Directly set internal state to test eviction without GB validation
    svc._directory = tmp_path
    svc._limit_bytes = 3000

    # Create real files
    names = ["chunk_a", "chunk_b", "chunk_c"]
    ts = [1000.0, 2000.0, 3000.0]  # oldest first
    for name, t in zip(names, ts):
        f = tmp_path / name
        f.write_bytes(b"x" * 1200)
        svc._chunks.append(_ChunkRecord(name=name, size=1200, last_access=t))

    # 3 chunks × 1200 bytes = 3600, limit=3000 → must evict at least one
    svc._evict_lru()

    # Oldest chunk should be gone
    remaining_names = [c.name for c in svc._chunks]
    assert "chunk_a" not in remaining_names
    assert svc._compute_used_bytes() <= 3000


def test_evict_lru_saves_manifest(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService, _ChunkRecord
    svc = DAContributionService()
    svc._directory = tmp_path
    svc._limit_bytes = 1000
    f = tmp_path / "big"
    f.write_bytes(b"x" * 2000)
    svc._chunks = [_ChunkRecord(name="big", size=2000, last_access=1.0)]
    svc._evict_lru()
    manifest = tmp_path / ".da_manifest.json"
    assert manifest.exists()


# ---------------------------------------------------------------------------
# Preallocate reserve file
# ---------------------------------------------------------------------------


def test_preallocate_creates_reserve_file(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    svc.configure(
        enabled=True, directory=str(tmp_path), max_bytes=1024 ** 3, reserve_mode="preallocate"
    )
    svc._create_reserve_file()
    reserve = tmp_path / ".reserve"
    assert reserve.exists()
    # Sparse file should report the correct size in metadata
    assert reserve.stat().st_size == 1024 ** 3


def test_remove_reserve_file(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    svc.configure(
        enabled=True, directory=str(tmp_path), max_bytes=1024 ** 3, reserve_mode="preallocate"
    )
    svc._create_reserve_file()
    svc.remove_reserve_file()
    reserve = tmp_path / ".reserve"
    assert not reserve.exists()


# ---------------------------------------------------------------------------
# Manifest persistence
# ---------------------------------------------------------------------------


def test_manifest_round_trip(tmp_path):
    import time as _time
    from animica_studio.services.da_contribution_service import DAContributionService, _ChunkRecord
    svc = DAContributionService()
    svc.configure(enabled=True, directory=str(tmp_path), max_bytes=2 * 1024 ** 3)
    svc._chunks = [_ChunkRecord(name="chunk1", size=512, last_access=123.0)]
    svc._served_bytes = 99
    svc._save_manifest()

    svc2 = DAContributionService()
    svc2.configure(enabled=True, directory=str(tmp_path), max_bytes=2 * 1024 ** 3)
    assert len(svc2._chunks) == 1
    assert svc2._chunks[0].name == "chunk1"
    assert svc2._chunks[0].size == 512
    assert svc2._served_bytes == 99


def test_manifest_load_empty_dir(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    svc.configure(enabled=True, directory=str(tmp_path), max_bytes=2 * 1024 ** 3)
    assert svc._chunks == []


# ---------------------------------------------------------------------------
# Log callback
# ---------------------------------------------------------------------------


def test_log_callback_receives_lines(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    received: list[str] = []
    svc.set_log_callback(received.append)
    svc.configure(enabled=True, directory=str(tmp_path), max_bytes=2 * 1024 ** 3)
    svc.start()
    assert any("started" in line.lower() for line in received)


def test_get_log_lines(tmp_path):
    from animica_studio.services.da_contribution_service import DAContributionService
    svc = DAContributionService()
    svc.configure(enabled=True, directory=str(tmp_path), max_bytes=2 * 1024 ** 3)
    svc.start()
    lines = svc.get_log_lines()
    assert isinstance(lines, list)
    assert len(lines) > 0


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


def test_config_has_da_contribution_defaults():
    from animica_studio.storage.config import Config
    cfg = Config()
    dc = cfg.da_contribution
    assert dc["enabled"] is True
    assert dc["max_gb"] == 50
    assert dc["reserve_mode"] == "quota"
    assert dc["auto_start"] is True


def test_config_da_contribution_round_trip(tmp_path):
    from animica_studio.storage.config import Config, save_config, load_config
    import animica_studio.storage.config as cfg_mod
    from unittest.mock import patch

    test_path = tmp_path / "config.json"
    with patch.object(cfg_mod, "config_file", return_value=test_path):
        cfg = Config()
        cfg.da_contribution["enabled"] = True
        cfg.da_contribution["studio_contrib_dir"] = "/custom/da"
        cfg.da_contribution["node_da_dir"] = "/data/da"
        cfg.da_contribution["max_gb"] = 100
        save_config(cfg)

        cfg2 = load_config()
        assert cfg2.da_contribution["enabled"] is True
        assert cfg2.da_contribution["studio_contrib_dir"] == "/custom/da"
        assert cfg2.da_contribution["host_data_dir"] == "/custom/da"
        assert cfg2.da_contribution["node_da_dir"] == "/data/da"
        assert cfg2.da_contribution["max_gb"] == 100


def test_config_da_contribution_defaults_on_missing_key(tmp_path):
    """Loading a config without da_contribution key should provide defaults."""
    import json
    from animica_studio.storage.config import load_config
    import animica_studio.storage.config as cfg_mod
    from unittest.mock import patch

    test_path = tmp_path / "config.json"
    test_path.write_text(json.dumps({}), encoding="utf-8")
    with patch.object(cfg_mod, "config_file", return_value=test_path):
        cfg = load_config()
    assert isinstance(cfg.da_contribution, dict)
    assert cfg.da_contribution["enabled"] is True


def test_config_migrates_legacy_data_dir_under_data(tmp_path):
    import json
    from animica_studio.storage.config import load_config
    import animica_studio.storage.config as cfg_mod
    from animica_studio.util.paths import default_da_contrib_dir
    from unittest.mock import patch

    test_path = tmp_path / "config.json"
    test_path.write_text(json.dumps({"da_contribution": {"data_dir": "/data/chain-1/da", "node_data_dir": "/data/chain-1/da"}}), encoding="utf-8")
    with patch.object(cfg_mod, "config_file", return_value=test_path):
        cfg = load_config()

    assert cfg.da_contribution["studio_contrib_dir"] == str(default_da_contrib_dir())
    assert cfg.da_contribution["node_da_dir"] == "/data/da"


def test_validate_config_rejects_node_data_path():
    from animica_studio.services.da_contribution_service import _validate_config
    with pytest.raises(ValueError, match="Studio contribution dir must be a host path"):
        _validate_config("/data/da", 2 * 1024 ** 3)
