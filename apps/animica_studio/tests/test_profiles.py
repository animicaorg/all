"""Unit tests for profile models and profile service — no Qt, no network required."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# RpcProfile model tests
# ---------------------------------------------------------------------------


def test_profile_type_values():
    from animica_studio.models.profile_models import ProfileType

    assert ProfileType.REMOTE_RPC.value == "remote_rpc"
    assert ProfileType.LOCAL_NODE.value == "local_node"


def test_validate_rpc_url_valid():
    from animica_studio.models.profile_models import validate_rpc_url

    assert validate_rpc_url("http://127.0.0.1:8545/rpc") == "http://127.0.0.1:8545/rpc"
    assert validate_rpc_url("https://mainnet.animica.org/rpc") == "https://mainnet.animica.org/rpc"


def test_validate_rpc_url_invalid():
    from animica_studio.models.profile_models import validate_rpc_url

    with pytest.raises(ValueError):
        validate_rpc_url("ws://mainnet.animica.org/rpc")
    with pytest.raises(ValueError):
        validate_rpc_url("not-a-url")


def test_sanitize_name_normal():
    from animica_studio.models.profile_models import sanitize_name

    assert sanitize_name("  Mainnet  ") == "Mainnet"
    assert sanitize_name("") == "Unnamed"
    assert sanitize_name(None) == "Unnamed"
    assert sanitize_name("   ") == "Unnamed"
    assert sanitize_name(None, fallback="Custom") == "Custom"




def test_default_chain_data_dir_uses_home(monkeypatch):
    from animica_studio.util.paths import default_chain_data_dir

    monkeypatch.setattr("animica_studio.util.paths.Path.home", lambda: Path("/home/employee"))
    assert default_chain_data_dir(1) == Path("/home/employee/.animica/chain-1")
    assert default_chain_data_dir(1337) == Path("/home/employee/.animica/chain-1337")

def test_rpc_profile_is_local_remote():
    from animica_studio.models.profile_models import RpcProfile, ProfileType

    remote = RpcProfile(
        id=str(uuid.uuid4()),
        name="Remote",
        type=ProfileType.REMOTE_RPC,
        rpc_url="https://mainnet.animica.org/rpc",
        chain_id_expected=1,
    )
    assert remote.is_remote()
    assert not remote.is_local()

    local = RpcProfile(
        id=str(uuid.uuid4()),
        name="Local",
        type=ProfileType.LOCAL_NODE,
        rpc_url="http://127.0.0.1:8545/rpc",
        chain_id_expected=1,
    )
    assert local.is_local()
    assert not local.is_remote()


def test_rpc_profile_effective_rpc_url_remote():
    from animica_studio.models.profile_models import RpcProfile, ProfileType

    p = RpcProfile(
        id=str(uuid.uuid4()),
        name="R",
        type=ProfileType.REMOTE_RPC,
        rpc_url="https://mainnet.animica.org/rpc",
        chain_id_expected=1,
    )
    assert p.effective_rpc_url() == "https://mainnet.animica.org/rpc"


def test_rpc_profile_effective_rpc_url_local_with_override():
    from animica_studio.models.profile_models import RpcProfile, ProfileType

    p = RpcProfile(
        id=str(uuid.uuid4()),
        name="L",
        type=ProfileType.LOCAL_NODE,
        rpc_url="http://example.org/rpc",
        chain_id_expected=1,
        node_rpc_url="http://127.0.0.1:8545/rpc",
    )
    assert p.effective_rpc_url() == "http://127.0.0.1:8545/rpc"


def test_rpc_profile_to_dict_round_trip():
    from animica_studio.models.profile_models import RpcProfile, ProfileType

    profile = RpcProfile(
        id="test-id-123",
        name="Test Profile",
        type=ProfileType.LOCAL_NODE,
        rpc_url="http://127.0.0.1:8545/rpc",
        chain_id_expected=42,
        node_start_cmd=["animica", "node", "start"],
        node_datadir="/tmp/node",
        node_datadir_custom=True,
        node_rpc_url="http://127.0.0.1:8545/rpc",
        notes="a note",
    )
    d = profile.to_dict()
    restored = RpcProfile.from_dict(d)

    assert restored.id == profile.id
    assert restored.name == profile.name
    assert restored.type == profile.type
    assert restored.rpc_url == profile.rpc_url
    assert restored.chain_id_expected == profile.chain_id_expected
    assert restored.node_start_cmd == profile.node_start_cmd
    assert restored.node_datadir == profile.node_datadir
    assert restored.notes == profile.notes


def test_rpc_profile_from_dict_heals_bad_url():
    from animica_studio.models.profile_models import RpcProfile

    d = {
        "id": "abc",
        "name": "Bad URL",
        "type": "remote_rpc",
        "rpc_url": "ws://bad-url",
        "chain_id_expected": 1,
    }
    p = RpcProfile.from_dict(d)
    # Should fall back to default
    assert p.rpc_url == "https://mainnet.animica.org/rpc"


def test_rpc_profile_from_dict_heals_bad_type():
    from animica_studio.models.profile_models import RpcProfile, ProfileType

    d = {
        "id": "abc",
        "name": "Unknown Type",
        "type": "unknown_type",
        "rpc_url": "http://localhost/rpc",
        "chain_id_expected": 1,
    }
    p = RpcProfile.from_dict(d)
    assert p.type == ProfileType.REMOTE_RPC




def test_rpc_profile_from_dict_implicit_datadir_follows_chain(monkeypatch):
    from animica_studio.models.profile_models import RpcProfile

    monkeypatch.setattr("animica_studio.util.paths.Path.home", lambda: Path("/home/employee"))
    p = RpcProfile.from_dict(
        {
            "id": "abc",
            "name": "Local",
            "type": "local_node",
            "rpc_url": "http://127.0.0.1:8545/rpc",
            "chain_id_expected": 1337,
            "node_datadir_custom": False,
        }
    )
    assert p.node_datadir == "/home/employee/.animica/chain-1337"
    assert p.node_datadir_custom is False

def test_rpc_profile_make_default_remote():
    from animica_studio.models.profile_models import RpcProfile, ProfileType

    p = RpcProfile.make_default_remote()
    assert p.type == ProfileType.REMOTE_RPC
    assert p.rpc_url == "https://mainnet.animica.org/rpc"
    assert p.chain_id_expected == 1
    assert p.id  # has a UUID




def test_rpc_profile_custom_datadir_preserved_when_marked_custom():
    from animica_studio.models.profile_models import RpcProfile

    p = RpcProfile.from_dict(
        {
            "id": "abc",
            "name": "Local",
            "type": "local_node",
            "rpc_url": "http://127.0.0.1:8545/rpc",
            "chain_id_expected": 1,
            "node_datadir": "/tmp/custom-dir",
            "node_datadir_custom": True,
        }
    )
    assert p.node_datadir == "/tmp/custom-dir"
    assert p.node_datadir_custom is True

def test_rpc_profile_make_default_local():
    from animica_studio.models.profile_models import RpcProfile, ProfileType

    p = RpcProfile.make_default_local("/tmp/node")
    assert p.type == ProfileType.LOCAL_NODE
    assert p.node_datadir == "/tmp/node"
    assert p.node_start_cmd == ["animica", "node", "start"]


# ---------------------------------------------------------------------------
# Config schema tests
# ---------------------------------------------------------------------------


def test_config_new_fields_defaults():
    from animica_studio.storage.config import Config

    cfg = Config()
    assert cfg.first_run_completed is False
    assert cfg.active_profile_id is None
    assert cfg.rpc_profiles == []


def test_config_round_trip_new_fields(tmp_path):
    from animica_studio.storage.config import Config, save_config, load_config

    with patch("animica_studio.storage.config.config_file", return_value=tmp_path / "cfg.json"):
        with patch("animica_studio.util.paths.app_data_dir", return_value=tmp_path):
            cfg = Config()
            cfg.first_run_completed = True
            cfg.active_profile_id = "some-id"
            cfg.rpc_profiles = [{"id": "some-id", "name": "Test", "type": "remote_rpc",
                                  "rpc_url": "https://mainnet.animica.org/rpc",
                                  "chain_id_expected": 1}]
            save_config(cfg)

            cfg2 = load_config()
            assert cfg2.first_run_completed is True
            assert cfg2.active_profile_id == "some-id"
            assert len(cfg2.rpc_profiles) == 1
            assert cfg2.rpc_profiles[0]["name"] == "Test"


def test_config_migration_from_legacy(tmp_path):
    """Legacy profiles list should be migrated to rpc_profiles."""
    from animica_studio.storage.config import Config, Profile, save_config, load_config  # noqa: F401

    with patch("animica_studio.storage.config.config_file", return_value=tmp_path / "cfg.json"), \
         patch("animica_studio.util.paths.app_data_dir", return_value=tmp_path), \
         patch("animica_studio.storage.config.app_data_dir", return_value=tmp_path):

        cfg = Config(
            active_profile="Mainnet",
            profiles=[Profile(name="Mainnet", rpc_url="https://mainnet.animica.org/rpc")],
            rpc_profiles=[],  # no new profiles
        )
        save_config(cfg)

        cfg2 = load_config()
        # Migration should have created rpc_profiles
        assert len(cfg2.rpc_profiles) >= 1
        assert cfg2.rpc_profiles[0]["name"] == "Mainnet"


# ---------------------------------------------------------------------------
# ProfileService tests
# ---------------------------------------------------------------------------


def test_profile_service_ensure_defaults(tmp_path):
    from animica_studio.storage.config import Config
    from animica_studio.services.profile_service import ProfileService

    with patch("animica_studio.storage.config.config_file", return_value=tmp_path / "cfg.json"), \
         patch("animica_studio.util.paths.app_data_dir", return_value=tmp_path):

        cfg = Config()  # no rpc_profiles
        svc = ProfileService(cfg)
        profiles = svc.list_profiles()
        assert len(profiles) >= 1


def test_profile_service_add_and_list(tmp_path):
    from animica_studio.models.profile_models import RpcProfile, ProfileType
    from animica_studio.storage.config import Config
    from animica_studio.services.profile_service import ProfileService

    with patch("animica_studio.storage.config.config_file", return_value=tmp_path / "cfg.json"), \
         patch("animica_studio.util.paths.app_data_dir", return_value=tmp_path):

        cfg = Config()
        svc = ProfileService(cfg)
        initial_count = len(svc.list_profiles())

        p = RpcProfile(
            id=str(uuid.uuid4()),
            name="My Test Profile",
            type=ProfileType.REMOTE_RPC,
            rpc_url="https://test.animica.org/rpc",
            chain_id_expected=99,
        )
        svc.add_profile(p)
        profiles = svc.list_profiles()
        assert any(pr.name == "My Test Profile" for pr in profiles)


def test_profile_service_set_active(tmp_path):
    from animica_studio.models.profile_models import RpcProfile, ProfileType
    from animica_studio.storage.config import Config
    from animica_studio.services.profile_service import ProfileService

    with patch("animica_studio.storage.config.config_file", return_value=tmp_path / "cfg.json"), \
         patch("animica_studio.util.paths.app_data_dir", return_value=tmp_path):

        cfg = Config()
        svc = ProfileService(cfg)

        p = RpcProfile(
            id=str(uuid.uuid4()),
            name="Second",
            type=ProfileType.REMOTE_RPC,
            rpc_url="https://second.animica.org/rpc",
            chain_id_expected=2,
        )
        svc.add_profile(p)
        svc.set_active(p.id)
        assert cfg.active_profile_id == p.id
        active = svc.get_active()
        assert active.id == p.id


def test_profile_service_set_active_invalid(tmp_path):
    from animica_studio.storage.config import Config
    from animica_studio.services.profile_service import ProfileService

    with patch("animica_studio.storage.config.config_file", return_value=tmp_path / "cfg.json"), \
         patch("animica_studio.util.paths.app_data_dir", return_value=tmp_path):

        cfg = Config()
        svc = ProfileService(cfg)
        with pytest.raises(ValueError):
            svc.set_active("nonexistent-id")


def test_profile_service_delete_last_raises(tmp_path):
    from animica_studio.storage.config import Config
    from animica_studio.services.profile_service import ProfileService

    with patch("animica_studio.storage.config.config_file", return_value=tmp_path / "cfg.json"), \
         patch("animica_studio.util.paths.app_data_dir", return_value=tmp_path):

        cfg = Config()
        svc = ProfileService(cfg)
        # Ensure exactly one profile
        profiles = svc.list_profiles()
        # delete all but one if more than one
        while len(svc.list_profiles()) > 1:
            svc.delete_profile(svc.list_profiles()[-1].id)
        last_id = svc.list_profiles()[0].id
        with pytest.raises(ValueError):
            svc.delete_profile(last_id)


def test_profile_service_update_profile(tmp_path):
    from animica_studio.models.profile_models import RpcProfile, ProfileType
    from animica_studio.storage.config import Config
    from animica_studio.services.profile_service import ProfileService

    with patch("animica_studio.storage.config.config_file", return_value=tmp_path / "cfg.json"), \
         patch("animica_studio.util.paths.app_data_dir", return_value=tmp_path):

        cfg = Config()
        svc = ProfileService(cfg)
        p = svc.list_profiles()[0]
        updated = RpcProfile(
            id=p.id,
            name="Updated Name",
            type=p.type,
            rpc_url=p.rpc_url,
            chain_id_expected=p.chain_id_expected,
        )
        svc.update_profile(updated)
        refreshed = svc.list_profiles()
        assert any(pr.name == "Updated Name" for pr in refreshed)


def test_profile_service_observer(tmp_path):
    from animica_studio.models.profile_models import RpcProfile, ProfileType
    from animica_studio.storage.config import Config
    from animica_studio.services.profile_service import ProfileService

    with patch("animica_studio.storage.config.config_file", return_value=tmp_path / "cfg.json"), \
         patch("animica_studio.util.paths.app_data_dir", return_value=tmp_path):

        cfg = Config()
        svc = ProfileService(cfg)

        notified: list[RpcProfile] = []
        svc.subscribe(notified.append)

        p = RpcProfile(
            id=str(uuid.uuid4()),
            name="Observer Test",
            type=ProfileType.REMOTE_RPC,
            rpc_url="https://obs.animica.org/rpc",
            chain_id_expected=1,
        )
        svc.add_profile(p)
        svc.set_active(p.id)

        assert len(notified) == 1
        assert notified[0].id == p.id


# ---------------------------------------------------------------------------
# util/fs tests
# ---------------------------------------------------------------------------


def test_ensure_dir_creates(tmp_path):
    from animica_studio.util.fs import ensure_dir

    new_dir = tmp_path / "a" / "b" / "c"
    result = ensure_dir(new_dir)
    assert result is True
    assert new_dir.is_dir()


def test_check_writable_dir_ok(tmp_path):
    from animica_studio.util.fs import check_writable_dir

    ok, err = check_writable_dir(tmp_path)
    assert ok is True
    assert err is None


def test_check_writable_dir_not_exist_creates(tmp_path):
    from animica_studio.util.fs import check_writable_dir

    new_dir = tmp_path / "new_sub"
    ok, err = check_writable_dir(new_dir)
    assert ok is True


def test_atomic_write_test_ok(tmp_path):
    from animica_studio.util.fs import atomic_write_test

    ok, err = atomic_write_test(tmp_path)
    assert ok is True
    assert err is None
    # Ensure no temp file left behind
    leftover = list(tmp_path.glob(".animica_write_test_*"))
    assert leftover == []


# ---------------------------------------------------------------------------
# RpcClient.get_chain_id tests
# ---------------------------------------------------------------------------


def _make_mock_response(data: dict, status_code: int = 200):
    from unittest.mock import MagicMock

    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data
    return mock


def test_rpc_client_get_chain_id_decimal():
    from animica_studio.services.rpc_client import RpcClient
    from unittest.mock import patch

    discover_resp = {
        "jsonrpc": "2.0", "id": 1,
        "result": {"methods": [{"name": "chain_getChainId"}]},
    }
    chain_id_resp = {
        "jsonrpc": "2.0", "id": 2,
        "result": 1,
    }
    with patch("requests.Session.post") as mock_post:
        mock_post.side_effect = [
            _make_mock_response(discover_resp),
            _make_mock_response(chain_id_resp),
        ]
        client = RpcClient("http://localhost:8545/rpc", max_retries=1)
        chain_id = client.get_chain_id()
        assert chain_id == 1
        client.close()


def test_rpc_client_get_chain_id_hex():
    from animica_studio.services.rpc_client import RpcClient
    from unittest.mock import patch

    discover_resp = {
        "jsonrpc": "2.0", "id": 1,
        "result": {"methods": [{"name": "eth_chainId"}]},
    }
    chain_id_resp = {
        "jsonrpc": "2.0", "id": 2,
        "result": "0x1",
    }
    with patch("requests.Session.post") as mock_post:
        mock_post.side_effect = [
            _make_mock_response(discover_resp),
            _make_mock_response(chain_id_resp),
        ]
        client = RpcClient("http://localhost:8545/rpc", max_retries=1)
        chain_id = client.get_chain_id()
        assert chain_id == 1
        client.close()
