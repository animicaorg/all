from __future__ import annotations

import json
from pathlib import Path

from animica_studio.services.bootstrap_info_manager import BootstrapInfoManager
from animica_studio.storage.config import Config


def _config() -> Config:
    cfg = Config()
    cfg.get_active_profile().rpc_url = "http://127.0.0.1:8545/rpc"
    cfg.ena["full_auto"] = {"model_channel": "ena-main"}
    return cfg


def test_local_cache_uses_pointer_check_without_payload_download(monkeypatch, tmp_path: Path) -> None:
    cfg = _config()
    calls: list[tuple[str, object]] = []

    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def resolve_method(self, requested: str, candidates):
            return candidates[0]

        def call_with_schema(self, method: str, params):
            calls.append((method, params))
            if method in {"da.get", "da_get"}:
                return {"commitment": "0xpointer1"}
            raise AssertionError(f"unexpected method {method}")

        def discover(self):
            return {"info": {"version": "v1"}, "methods": []}

        def get_chain_id(self):
            return 1

    monkeypatch.setattr("animica_studio.services.bootstrap_info_manager.RpcClient", FakeRpcClient)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    manager = BootstrapInfoManager(cfg, ttl_seconds=3600)
    manager.cache_path.parent.mkdir(parents=True, exist_ok=True)
    manager.cache_path.write_text(
        json.dumps(
            {
                "key": manager._cache_key(),
                "version_commitment": "0xpointer1",
                "fetched_at": 4_102_444_800,
                "ttl_seconds": 3600,
                "payload": {"schema": 1, "ok": True},
            }
        ),
        encoding="utf-8",
    )

    info = manager.load()

    assert info.source == "local"
    assert info.payload["ok"] is True
    assert not any(method in {"da.getBlob", "da_getBlob"} for method, _ in calls)


def test_da_only_mode_does_not_write_local_file(monkeypatch, tmp_path: Path) -> None:
    cfg = _config()
    cfg.ena["bootstrap_cache_mode"] = "da_only"

    pointer_blob = {
        "payload_commitment": "0xpayload1",
    }
    payload_blob = {
        "schema": 1,
        "rpc_discover": {"version": "v1", "capabilities": {}},
    }

    class FakeRpcClient:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def resolve_method(self, requested: str, candidates):
            return candidates[0]

        def call_with_schema(self, method: str, params):
            if method in {"da.get", "da_get"}:
                return {"commitment": "0xpointer1"}
            if method in {"da.getBlob", "da_getBlob"}:
                commitment = params.get("commitment")
                if commitment == "0xpointer1":
                    return {"data": "0x" + json.dumps(pointer_blob).encode("utf-8").hex()}
                if commitment == "0xpayload1":
                    return {"data": "0x" + json.dumps(payload_blob).encode("utf-8").hex()}
            raise AssertionError(f"unexpected {method} {params}")

        def discover(self):
            return {"info": {"version": "v1"}, "methods": []}

        def get_chain_id(self):
            return 1

    monkeypatch.setattr("animica_studio.services.bootstrap_info_manager.RpcClient", FakeRpcClient)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    manager = BootstrapInfoManager(cfg, ttl_seconds=3600)
    info = manager.load()

    assert info.source == "da"
    assert info.payload["schema"] == 1
    assert not manager.cache_path.exists()
