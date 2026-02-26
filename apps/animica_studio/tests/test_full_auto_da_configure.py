from __future__ import annotations

from animica_studio.services.ena_full_auto_engine import (
    NodePathMapper,
    NodeToHostPathMapper,
    _bootstrap_cycle,
    _build_da_configure_params,
    _build_bootstrap_blocked_payload,
    is_host_path,
    is_node_path,
)
from animica_studio.services.rpc_client import RpcRegistry


def test_build_da_configure_params_always_includes_enabled() -> None:
    payload = _build_da_configure_params(
        {"enabled": False, "dir": "/data/old"},
        {"default_dir": "/data/da", "allowed_base_dirs": ["/data"], "max_bytes": 1024},
    )
    assert payload == {"enabled": True, "dir": "/data/old", "max_bytes": 1024}


def test_bootstrap_da_failure_stays_training_when_da_not_required(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "animica_studio.services.ena_full_auto_engine._ensure_da_ready",
        lambda _ctx: {"ok": False, "logs": [], "diagnostics": "fail"},
    )
    out = _bootstrap_cycle(
        {
            "cfg": {
                "model_channel": "ena-main",
                "train_locally_when_da_disabled": False,
                "require_da_uploads": False,
                "payout_address": "",
            },
            "storage": str(tmp_path),
            "steps": 0,
            "last_upload_step": 0,
            "last_upload_time": 0,
            "last_sync_time": 0,
        },
        has_pointer=False,
    )
    assert out["state"] == "training"
    assert out["detail"] == "LOCAL_ONLY_DA_DISABLED"


def test_rpc_registry_treats_openrpc_by_name_as_object_params() -> None:
    reg = RpcRegistry(
        {
            "methods": [
                {
                    "name": "da.configure",
                    "paramStructure": "by-name",
                    "params": [
                        {"name": "enabled", "required": True, "schema": {"type": "boolean"}},
                        {"name": "dir", "required": True, "schema": {"type": "string"}},
                    ],
                }
            ]
        }
    )
    meta = reg.get_method_meta("da.configure")
    assert meta.get("param_structure") == "object"


def test_path_classifier_and_node_to_host_mapping() -> None:
    assert is_node_path('/data/chain-1/da') is True
    assert is_host_path('/home/employee/.animica/chain-1/da') is True
    mapper = NodeToHostPathMapper('/home/employee/.animica/chain-1')
    mapped = mapper.map_node_da_dir('/data/chain-1/da')
    assert str(mapped) == '/home/employee/.animica/chain-1/da'


def test_node_to_host_mapping_requires_host_chain_dir() -> None:
    mapper = NodeToHostPathMapper(None)
    assert mapper.map_node_da_dir('/data/chain-1/da') is None


def test_node_path_mapper_maps_ingest_dir_from_chain_mapping() -> None:
    mapper = NodePathMapper('/home/employee/.animica/chain-1')
    out = mapper.map_ingest_dir('/data/da_ingest', '/data/chain-1/da', '/data')
    assert str(out) == '/home/employee/.animica/da_ingest'


def test_bootstrap_da_retryable_marks_payload(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "animica_studio.services.ena_full_auto_engine._ensure_da_ready",
        lambda _ctx: {"ok": False, "logs": [], "diagnostics": "permission denied", "retryable": True},
    )
    out = _bootstrap_cycle(
        {
            "cfg": {
                "model_channel": "ena-main",
                "train_locally_when_da_disabled": False,
                "require_da_uploads": True,
                "payout_address": "",
            },
            "storage": str(tmp_path),
            "steps": 0,
            "last_upload_step": 0,
            "last_upload_time": 0,
            "last_sync_time": 0,
        },
        has_pointer=False,
    )
    assert out["state"] == "error"
    assert out.get("bootstrap_retryable") is True


def test_bootstrap_publish_path_missing_falls_back_when_not_required(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "animica_studio.services.ena_full_auto_engine._ensure_da_ready",
        lambda _ctx: {"ok": True, "logs": [], "diagnostics": "ok"},
    )
    monkeypatch.setattr(
        "animica_studio.services.ena_full_auto_engine._publish_checkpoint",
        lambda *_a, **_k: {"ok": False, "state": "error", "detail": "DA_UPLOAD_PATH_UNAVAILABLE: Node blocks remote put and does not provide local ingest. Update node to add da.ingestLocal or enable allow_remote_put for dev.", "logs": []},
    )
    out = _bootstrap_cycle(
        {
            "cfg": {
                "model_channel": "ena-main",
                "train_locally_when_da_disabled": True,
                "require_da_uploads": True,
                "payout_address": "",
            },
            "storage": str(tmp_path),
            "steps": 0,
            "last_upload_step": 0,
            "last_upload_time": 0,
            "last_sync_time": 0,
        },
        has_pointer=False,
    )
    assert out["state"] == "training"
    assert out["detail"] == "LOCAL_ONLY_DA_UPLOAD_UNAVAILABLE"


def test_bootstrap_publish_single_flight_until_manual_retry(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "animica_studio.services.ena_full_auto_engine._ensure_da_ready",
        lambda _ctx: {"ok": True, "logs": [], "diagnostics": "ok"},
    )
    calls = {"n": 0}
    def _fake_publish(*_a, **_k):
        calls["n"] += 1
        return {"ok": False, "state": "error", "detail": "DA_UPLOAD_PATH_UNAVAILABLE: Node blocks remote put and does not provide local ingest. Update node to add da.ingestLocal or enable allow_remote_put for dev.", "logs": []}

    monkeypatch.setattr("animica_studio.services.ena_full_auto_engine._publish_checkpoint", _fake_publish)

    ctx = {
        "cfg": {
            "model_channel": "ena-main",
            "train_locally_when_da_disabled": False,
            "require_da_uploads": True,
            "payout_address": "",
        },
        "storage": str(tmp_path),
        "steps": 0,
        "last_upload_step": 0,
        "last_upload_time": 0,
        "last_sync_time": 0,
        "bootstrap_publish_attempted": True,
    }
    out = _bootstrap_cycle(ctx, has_pointer=False)
    assert out["state"] == "idle"
    assert calls["n"] == 0

    ctx["manual_action"] = "retry"
    out2 = _bootstrap_cycle(ctx, has_pointer=False)
    assert calls["n"] == 1
    assert out2.get("bootstrap_publish_attempted") is True


def test_bootstrap_publish_mapping_missing_transitions_to_blocked(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "animica_studio.services.ena_full_auto_engine._ensure_da_ready",
        lambda _ctx: {"ok": True, "logs": [], "diagnostics": "ok"},
    )
    monkeypatch.setattr(
        "animica_studio.services.ena_full_auto_engine._publish_checkpoint",
        lambda *_a, **_k: {
            "ok": False,
            "state": "error",
            "detail": "Local ingest path mapping broken. Configure docker mount: host ~/.animica -> node /data. I wrote to host path /home/employee/.animica/da_ingest/pending/a.blob, node expected /data/da_ingest/pending/a.blob.",
            "logs": [],
        },
    )
    out = _bootstrap_cycle(
        {
            "cfg": {
                "model_channel": "ena-main",
                "train_locally_when_da_disabled": False,
                "require_da_uploads": True,
                "payout_address": "",
            },
            "storage": str(tmp_path),
            "steps": 0,
            "last_upload_step": 0,
            "last_upload_time": 0,
            "last_sync_time": 0,
        },
        has_pointer=False,
    )
    assert out["state"] == "bootstrap_blocked"
    assert out["detail"] == "MOUNT_MAPPING_MISSING"
    assert out.get("bootstrap_blocked_reason") == "MOUNT_MAPPING_MISSING"


def test_build_bootstrap_blocked_payload_includes_compose_fix() -> None:
    blocked = _build_bootstrap_blocked_payload(
        {},
        "Node cannot see host ingest directory. I wrote to host path /home/employee/.animica/da_ingest/pending/.studio_probe, node expected /data/da_ingest/pending/.studio_probe; mapping missing.",
    )
    assert blocked is not None
    assert "volumes:" in str(blocked.get("volume_snippet"))
    assert "/home/employee/.animica:/data" in str(blocked.get("volume_snippet"))
