from unittest.mock import patch

from animica_studio.models.profile_models import RpcProfile, ProfileType, validate_explorer_base_url
from animica_studio.models.wallet_models import BalanceSource
from animica_studio.services.balance_service import BalanceService


def _profile() -> RpcProfile:
    return RpcProfile(
        id="p1",
        name="p",
        type=ProfileType.REMOTE_RPC,
        rpc_url="http://127.0.0.1:8545/rpc",
        chain_id_expected=1,
        explorer_base_url="https://explorer.example.org",
    )


def test_balance_service_uses_rpc_first():
    svc = BalanceService()
    profile = _profile()
    with patch("animica_studio.services.balance_service.RpcClient") as rpc_cls, patch(
        "animica_studio.services.balance_service.ExplorerClient"
    ) as explorer_cls:
        rpc = rpc_cls.return_value.__enter__.return_value
        rpc.get_balance.return_value = 10**18
        state = svc.get_balance("anim1abc", profile)
        assert state.source == BalanceSource.RPC
        assert state.error is None
        explorer_cls.assert_not_called()


def test_balance_service_falls_back_to_explorer():
    svc = BalanceService()
    profile = _profile()
    with patch("animica_studio.services.balance_service.RpcClient") as rpc_cls, patch(
        "animica_studio.services.balance_service.ExplorerClient"
    ) as explorer_cls:
        rpc = rpc_cls.return_value.__enter__.return_value
        rpc.get_balance.side_effect = RuntimeError("rpc down")
        explorer = explorer_cls.return_value
        explorer.get_balance.return_value.balance_wei = 5
        explorer.get_balance.return_value.formatted = "0.000000000000000005 ANM"
        explorer.get_balance.return_value.source = BalanceSource.EXPLORER
        explorer.get_balance.return_value.error = None
        explorer.get_balance.return_value.is_stale = False
        state = svc.get_balance("anim1abc", profile)
        assert state.source == BalanceSource.EXPLORER


def test_validate_explorer_base_url():
    assert validate_explorer_base_url("https://x/y/") == "https://x/y"
