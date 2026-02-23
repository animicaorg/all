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
        cli.call.side_effect = [
            {"enabled": True, "dir": "/data/da", "on_full": "evict", "max_bytes": 123},
            "animica-node/1.2.3",
        ]
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

    with patch("animica_studio.services.da_status_service.RpcClient") as mock_client_cls:
        cli = MagicMock()
        cli.call.side_effect = [
            {"enabled": True},
            {"enabled": True, "dir": "/data/da", "on_full": "evict", "max_bytes": 50 * 1024**3},
            "animica-node/1.2.3",
        ]
        mock_client_cls.return_value = cli
        out = svc.enable_da("/data/da", 50 * 1024**3)

    assert out["ok"] is True
    configure_args = cli.call.call_args_list[0].args
    assert configure_args[0] == "da.configure"
    assert configure_args[1][0]["enabled"] is True
    assert configure_args[1][0]["dir"] == "/data/da"
