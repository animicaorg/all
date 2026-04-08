from __future__ import annotations

import json
import uuid
from pathlib import Path

from animica_studio.models.profile_models import ProfileType, RpcProfile
from animica_studio.services.settings_service import SettingsService
from animica_studio.storage.config import Config
from animica_studio.util.paths import animica_wallets_file


def _profile(*, local_node: bool = False) -> RpcProfile:
    return RpcProfile(
        id=str(uuid.uuid4()),
        name="Test Profile",
        type=ProfileType.LOCAL_NODE if local_node else ProfileType.REMOTE_RPC,
        rpc_url="http://127.0.0.1:8545/rpc" if local_node else "https://mainnet.animica.org/rpc",
        node_rpc_url="http://127.0.0.1:8545/rpc" if local_node else None,
        chain_id_expected=1,
        node_start_cmd=["animica", "node", "start"] if local_node else None,
        node_datadir="/tmp/animica-chain" if local_node else None,
    )


def test_settings_service_contacts_and_onboarding_state() -> None:
    cfg = Config()
    service = SettingsService(cfg)

    service.add_wallet_contact("Alice", "anim1aliceabcdefghij")
    service.add_wallet_contact("Bob", "anim1bobabcdefghijk")
    assert service.list_wallet_contacts() == [
        {"label": "Alice", "address": "anim1aliceabcdefghij"},
        {"label": "Bob", "address": "anim1bobabcdefghijk"},
    ]

    service.set_last_selected_wallet("anim1aliceabcdefghij")
    assert service.last_selected_wallet() == "anim1aliceabcdefghij"
    service.remove_wallet_contact("anim1aliceabcdefghij")
    assert service.list_wallet_contacts() == [{"label": "Bob", "address": "anim1bobabcdefghijk"}]

    service.mark_onboarding_complete("testnet")
    assert cfg.first_run_completed is True
    assert cfg.onboarding["last_network"] == "testnet"
    assert cfg.onboarding["wizard_version"] == 2
    assert cfg.onboarding["completed_at"] is not None

    service.rerun_onboarding()
    assert cfg.first_run_completed is False
    assert cfg.onboarding["completed_at"] is None


def test_settings_service_import_wallet_store_merges_unique_wallets(tmp_path: Path) -> None:
    cfg = Config()
    service = SettingsService(cfg)
    target = animica_wallets_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "wallets": [
                    {"label": "Existing", "address": "anim1existingaddr000000000000000"},
                ]
            }
        ),
        encoding="utf-8",
    )

    source = tmp_path / "incoming-wallets.json"
    source.write_text(
        json.dumps(
            {
                "wallets": [
                    {"label": "Existing Duplicate", "address": "anim1existingaddr000000000000000"},
                    {"label": "Imported", "address": "anim1importedaddr00000000000000"},
                ]
            }
        ),
        encoding="utf-8",
    )

    count, saved_path = service.import_wallet_store(source)
    payload = json.loads(saved_path.read_text(encoding="utf-8"))

    assert count == 2
    assert saved_path == target
    assert payload["wallets"] == [
        {"label": "Existing", "address": "anim1existingaddr000000000000000"},
        {"label": "Imported", "address": "anim1importedaddr00000000000000"},
    ]


def test_settings_service_applies_network_preset_and_profile_save() -> None:
    cfg = Config()
    service = SettingsService(cfg)
    profile = _profile()

    updated = service.apply_network_preset(profile, "testnet", local_node=True)
    assert updated.chain_id_expected == 2
    assert updated.rpc_url == "http://127.0.0.1:18546/rpc"
    assert updated.node_rpc_url == "http://127.0.0.1:18546/rpc"
    assert cfg.onboarding["last_network"] == "testnet"

    updated = service.save_active_profile_settings(
        updated,
        rpc_url="http://127.0.0.1:18546/rpc",
        explorer_url="https://explorer.testnet.animica.org",
        chain_id=2,
        node_start_cmd="animica node start --network testnet",
        node_datadir="/tmp/testnet-node",
    )
    assert updated.chain_id_expected == 2
    assert updated.rpc_url == "http://127.0.0.1:18546/rpc"
    assert updated.explorer_base_url == "https://explorer.testnet.animica.org"
    assert updated.node_start_cmd == ["animica", "node", "start", "--network", "testnet"]
    assert updated.node_datadir == "/tmp/testnet-node"
