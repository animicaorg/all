from __future__ import annotations

from animica_studio.services.ena_full_auto_engine import (
    NodeToHostPathMapper,
    _bootstrap_cycle,
    _build_da_configure_params,
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
