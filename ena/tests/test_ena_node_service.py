from __future__ import annotations

import importlib
from pathlib import Path


def _load_node(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ENA_DEV_MODE", "1")
    monkeypatch.setenv("ENA_DB_PATH", str(tmp_path / "ena.db"))
    monkeypatch.setenv("ENA_LOG_FILE", str(tmp_path / "logs" / "ena.log"))
    monkeypatch.setenv("ENA_TRAINING_DIR", str(tmp_path / "training"))
    monkeypatch.setenv("ENA_CHECKPOINTS_DIR", str(tmp_path / "checkpoints"))
    monkeypatch.setenv("ENA_MODELS_DIR", str(tmp_path / "models"))

    config_module = importlib.import_module("ena.services.ena_node.config")
    importlib.reload(config_module)
    db_module = importlib.import_module("ena.services.ena_node.database")
    importlib.reload(db_module)
    module = importlib.import_module("ena.services.ena_node.main")
    return importlib.reload(module)


def test_ena_node_import_creates_log_parent(tmp_path: Path, monkeypatch) -> None:
    module = _load_node(tmp_path, monkeypatch)
    assert Path(module.Config.LOG_FILE).parent.exists()
