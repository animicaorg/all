from unittest.mock import Mock, patch

from animica_studio.models.profile_models import RpcProfile, ProfileType, validate_explorer_base_url
from animica_studio.services.explorer_style_rpc import ExplorerStyleRpcClient


def _profile() -> RpcProfile:
    return RpcProfile(
        id="p1",
        name="p",
        type=ProfileType.REMOTE_RPC,
        rpc_url="http://127.0.0.1:8545/rpc",
        chain_id_expected=1,
        explorer_base_url="https://explorer.example.org",
    )


def test_explorer_style_rpc_balance_parity_contract_hex_result():
    svc = ExplorerStyleRpcClient()
    fake_response = Mock()
    fake_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "0x3b9aca00"}  # 1 ANM in nANM
    fake_response.raise_for_status.return_value = None

    with patch("animica_studio.services.explorer_style_rpc.requests.post", return_value=fake_response):
        state = svc.get_balance("anim1abc", "http://127.0.0.1:8545")

    assert state.error is None
    assert state.raw_nanm == 1_000_000_000
    assert state.formatted_anm == "1 ANM"


def test_explorer_style_rpc_balance_parity_contract_object_result():
    svc = ExplorerStyleRpcClient()
    fake_response = Mock()
    fake_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {"balance": "1230000000"}}
    fake_response.raise_for_status.return_value = None

    with patch("animica_studio.services.explorer_style_rpc.requests.post", return_value=fake_response):
        state = svc.get_balance("anim1abc", "http://127.0.0.1:8545/rpc")

    assert state.error is None
    assert state.raw_nanm == 1_230_000_000
    assert state.formatted_anm == "1.23 ANM"


def test_explorer_style_rpc_returns_cached_value_on_rpc_error():
    svc = ExplorerStyleRpcClient()
    ok_response = Mock()
    ok_response.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "0x5"}
    ok_response.raise_for_status.return_value = None

    with patch("animica_studio.services.explorer_style_rpc.requests.post", return_value=ok_response):
        first = svc.get_balance("anim1abc", "http://127.0.0.1:8545/rpc")

    with patch("animica_studio.services.explorer_style_rpc.requests.post", side_effect=RuntimeError("down")):
        second = svc.get_balance("anim1abc", "http://127.0.0.1:8545/rpc", force_refresh=True)

    assert first.error is None
    assert second.error is not None
    assert second.from_cache is True
    assert second.raw_nanm == 5


def test_validate_explorer_base_url():
    assert validate_explorer_base_url("https://x/y/") == "https://x/y"
