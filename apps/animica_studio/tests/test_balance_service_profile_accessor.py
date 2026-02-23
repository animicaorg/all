from animica_studio.services.balance_service import BalanceService
from animica_studio.storage.config import Profile


def test_balance_service_uses_profile_get_rpc_url_accessor():
    profile = Profile(rpc_url="http://127.0.0.1:8545/rpc")
    assert profile.get_rpc_url() == "http://127.0.0.1:8545/rpc"
    assert BalanceService._resolve_rpc_url(profile) == "http://127.0.0.1:8545/rpc"
