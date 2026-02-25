from __future__ import annotations

from unittest.mock import MagicMock, patch

from animica_studio.services.da_status_service import DaStatusService
from animica_studio.storage.config import Config


def _mock_registry() -> MagicMock:
    reg = MagicMock()

    def _resolve_any(candidates):
        return candidates[0] if candidates else None

    reg.resolve_any.side_effect = _resolve_any
    reg.get_method_meta.return_value = {}
    return reg


def test_da_status_service_reads_enabled_status() -> None:
    cfg = Config(
        active_profile_id="p1",
        rpc_profiles=[{"id": "p1", "rpc_url": "http://127.0.0.1:8545/rpc"}],
    )
    svc = DaStatusService(cfg)

    with patch("animica_studio.services.da_status_service.RpcClient") as mock_client_cls:
        cli = MagicMock()
        cli.registry.return_value = _mock_registry()
        cli.get_param_spec.return_value = [{"name": "allow_remote_put", "required": False}]
        cli.call_with_schema.return_value = {
            "enabled": True,
            "dir": "/data/da",
            "on_full": "evict",
            "max_bytes": 123,
            "allow_remote_put": False,
            "version": "1.0.0",
            "writable": True,
        }
        cli.call.return_value = "animica-node/1.2.3"
        mock_client_cls.return_value = cli
        out = svc.get_status()

    assert out["ok"] is True
    assert out["enabled"] is True
    assert out["configured_dir"] == "/data/da"
    assert out["server_version"] == "animica-node/1.2.3"
    assert out["can_configure_allow_remote_put"] is True


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
            {"enabled": False, "da_methods": {"configure": "da.configure"}, "raw": {}, "default_dir": "/data/da", "allowed_base_dirs": ["/data"]},
            {"enabled": True, "ok": True, "writable": True, "dir": "/data/da", "da_methods": {"configure": "da.configure"}},
        ],
    ):
        cli = MagicMock()
        cli.registry.return_value = _mock_registry()
        cli.get_param_spec.return_value = [
            {"name": "enabled", "required": False},
            {"name": "dir", "required": False},
            {"name": "max_bytes", "required": False},
        ]
        # First get_status() in enable_da reports disabled; second reports enabled.
        cli.call_with_schema.side_effect = [
            {"enabled": False, "allow_remote_put": False},
            {"ok": True},
            {
                "enabled": True,
                "dir": "/data/da",
                "on_full": "evict",
                "max_bytes": 50 * 1024**3,
                "allow_remote_put": True,
            },
        ]
        cli.call.return_value = "animica-node/1.2.3"
        mock_client_cls.return_value = cli
        out = svc.enable_da("/data/da", 50 * 1024**3)

    assert out["ok"] is True
    configure_call = cli.call.call_args_list[0]
    assert configure_call.args[0] == "da_configure"
    payload = configure_call.args[1]
    assert payload["enabled"] is True
    assert payload["dir"] == "/data/da"
    assert out["param_encoding"] == "object"
    assert "curl_configure" in out
