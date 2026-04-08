from __future__ import annotations

import types
import uuid

from animica_studio.models.profile_models import ProfileType, RpcProfile
from animica_studio.services.balance_service import BalanceResult
from animica_studio.services.settings_service import SettingsService
from animica_studio.services.studio_status_service import StudioStatusService
from animica_studio.services.wallet_repository import WalletRecord
from animica_studio.storage.config import Config


def _local_profile() -> RpcProfile:
    return RpcProfile(
        id=str(uuid.uuid4()),
        name="Local Mainnet",
        type=ProfileType.LOCAL_NODE,
        rpc_url="http://127.0.0.1:8545/rpc",
        node_rpc_url="http://127.0.0.1:8545/rpc",
        chain_id_expected=1,
        node_start_cmd=["animica", "node", "start"],
        node_datadir="/tmp/animica-chain",
        explorer_base_url="https://explorer.animica.org",
    )


class _FakeProcessManager:
    def status(self) -> dict[str, object]:
        return {
            "running": True,
            "rpc_reachable": True,
            "log_file": "/tmp/animica-node.log",
            "last_log_lines": ["node started", "syncing"],
        }

    def start(self) -> dict[str, object]:
        return {"running": True, "rpc_reachable": True}

    def stop(self) -> dict[str, object]:
        return {"running": False}

    def restart(self) -> dict[str, object]:
        return {"running": True, "rpc_reachable": True}


class _FakeRpcClient:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def get_head(self):
        return types.SimpleNamespace(number=120, hash="0xabc123", timestamp=None)

    def get_chain_id(self) -> int:
        return 1

    def call(self, *_args, **_kwargs):
        return {}

    def close(self) -> None:
        return None


def test_studio_status_service_collects_snapshot_without_network(monkeypatch) -> None:
    cfg = Config()
    profile = _local_profile()
    cfg.rpc_profiles = [profile.to_dict()]
    cfg.active_profile_id = profile.id
    cfg.mining_defaults["miner_address"] = "anim1qqqqqqqqqqqqqqqq"

    settings = SettingsService(cfg)
    settings.set_last_selected_wallet("anim1acdefghjklmnpqrstuvwxyz023456")
    service = StudioStatusService(cfg, settings)

    monkeypatch.setattr(
        service._wallet_repo,
        "load_wallets",
        lambda: [WalletRecord(label="Primary", address="anim1acdefghjklmnpqrstuvwxyz023456", sig_scheme="dilithium3")],
    )  # noqa: SLF001
    monkeypatch.setattr(service, "_process_manager", lambda _profile: _FakeProcessManager())
    monkeypatch.setattr(service, "_load_cli_sync_payload", lambda _rpc_url: {
        "rpc_reachable": True,
        "height": 120,
        "network_height": 160,
        "sync_percent": 75.0,
        "peer_count": 6,
        "head_hash": "0xabc123",
        "chain_id": 1,
        "phase": "syncing",
    })
    monkeypatch.setattr(
        service,
        "_load_wallet_balance",
        lambda _address, _profile: BalanceResult(
            ok=True,
            amount_smallest=2_500_000_000,
            formatted="2.5 ANM",
            error_reason=None,
            source="test",
        ),
    )
    monkeypatch.setattr("animica_studio.services.studio_status_service.RpcClient", _FakeRpcClient)
    monkeypatch.setattr(service._da_status, "get_status", lambda: {"enabled": True, "last_error": ""})  # noqa: SLF001
    monkeypatch.setattr(
        service._da_usage,
        "get_snapshot",
        lambda _path: types.SimpleNamespace(used_bytes=1024, warning=""),
    )  # noqa: SLF001

    snapshot = service.collect_snapshot()
    probe = service.probe_onboarding()
    diagnostics = service.sync_diagnostics_text()

    assert snapshot.wallet.wallet_count == 1
    assert snapshot.wallet.total_balance_text == "2.5 ANM"
    assert snapshot.node.running is True
    assert snapshot.node.rpc_reachable is True
    assert snapshot.node.peer_count == 6
    assert snapshot.node.sync.progress_pct == 75.0
    assert snapshot.node.sync.state == "SYNCING"
    assert snapshot.mining.state == "ready"
    assert snapshot.ena.state in {"ready", "attention", "disabled"}
    assert probe.has_wallet is True
    assert probe.rpc_reachable is True
    assert probe.node_running is True
    assert probe.sync_complete is False
    assert "Profile:" in diagnostics
    assert "Sync state: SYNCING" in diagnostics


def test_studio_status_service_extracts_json_from_mixed_output() -> None:
    service = StudioStatusService(Config(), SettingsService(Config()))
    payload = service._extract_json_output("warning: stale cache\n{\"height\": 10, \"peer_count\": 2}\n")  # noqa: SLF001
    assert payload == {"height": 10, "peer_count": 2}
