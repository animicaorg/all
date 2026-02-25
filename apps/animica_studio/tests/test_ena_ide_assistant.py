from __future__ import annotations

from animica_studio.services.ena_service import EnaIdeAssistantProvider


def test_available_status_reports_missing_model(monkeypatch):
    cfg = {"ide_assistant": {}}
    provider = EnaIdeAssistantProvider(cfg)
    ok, reason = provider.available_status()
    assert ok is False
    assert reason == "No model selected"


def test_available_status_reports_backend_missing(tmp_path, monkeypatch):
    model = tmp_path / "model.ckpt"
    model.write_text("x", encoding="utf-8")
    cfg = {"ide_assistant": {"model_path": str(model)}}
    provider = EnaIdeAssistantProvider(cfg)
    monkeypatch.setattr("animica_studio.services.ena_service._local_inference_backend_ready", lambda: False)
    ok, reason = provider.available_status()
    assert ok is False
    assert reason == "Inference backend missing"


def test_available_status_ready(tmp_path, monkeypatch):
    model = tmp_path / "model.ckpt"
    model.write_text("x", encoding="utf-8")
    cfg = {"ide_assistant": {"model_path": str(model)}}
    provider = EnaIdeAssistantProvider(cfg)
    monkeypatch.setattr("animica_studio.services.ena_service._local_inference_backend_ready", lambda: True)
    ok, reason = provider.available_status()
    assert ok is True
    assert reason == "ready"
