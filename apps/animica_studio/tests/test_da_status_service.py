from __future__ import annotations

from unittest.mock import MagicMock, patch

from animica_studio.services.da_status_service import DaStatusService
from animica_studio.storage.config import Config


def test_da_status_service_reads_enabled_status() -> None:
    cfg = Config(
        active_profile_id="p1",
        rpc_profiles=[{"id": "p1", "rpc_url": "http://127.0.0.1:8545/rpc"}],
    )
    svc = DaStatusService(cfg)

    with patch("animica_studio.services.da_status_service.RpcClient") as mock_client_cls:
        cli = MagicMock()
        registry = MagicMock()
        registry.resolve_any.side_effect = ["da.putBlob", "da.getBlob", "da.configure", "da.getStatus"]
        registry.dump_methods.return_value = ["da.getStatus", "da.putBlob"]
        cli.registry.return_value = registry
        cli.call_with_schema.return_value = {"enabled": True, "dir": "/data/da", "on_full": "evict", "max_bytes": 123}
        cli.call.side_effect = ["animica-node/1.2.3"]
        mock_client_cls.return_value = cli
        out = svc.get_status()

    assert out["ok"] is True
    assert out["enabled"] is True
    assert out["configured_dir"] == "/data/da"
    assert out["server_version"] == "animica-node/1.2.3"


def test_da_status_service_enable_calls_da_configure_with_enabled_true() -> None:
    cfg = Config(
        active_profile_id="p1",
        rpc_profiles=[{"id": "p1", "rpc_url": "http://127.0.0.1:8545/rpc"}],
    )
    svc = DaStatusService(cfg)

    with patch("animica_studio.services.da_status_service.RpcClient") as mock_client_cls, patch.object(
        svc,
        "get_status",
        side_effect=[
            {"enabled": False, "da_methods": {"configure": "da.configure"}},
            {"enabled": True, "dir": "/data/da", "da_methods": {"configure": "da.configure"}},
        ],
    ):
        cli = MagicMock()
        registry = MagicMock()
        registry.resolve_any.return_value = "da.configure"
        cli.registry.return_value = registry
        cli.get_param_spec.return_value = [{"name": "enabled"}, {"name": "dir"}, {"name": "max_bytes"}, {"name": "on_full"}]
        cli.call_with_schema.return_value = {"ok": True}
        mock_client_cls.return_value = cli
        out = svc.enable_da("/data/da", 50 * 1024**3)

    assert out["ok"] is True
    configure_args = cli.call_with_schema.call_args_list[0].args
    assert configure_args[0] == "da.configure"
    assert configure_args[1]["enabled"] is True
    assert configure_args[1]["dir"] == "/data/da"
