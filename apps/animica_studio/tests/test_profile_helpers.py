from __future__ import annotations

from animica_studio.services.profile_helpers import get_active_rpc_url
from animica_studio.storage.config import Config


def test_get_active_rpc_url_prefers_remote_rpc_for_remote_profile() -> None:
    cfg = Config(
        active_profile_id="remote-1",
        rpc_profiles=[
            {
                "id": "remote-1",
                "type": "remote_rpc",
                "rpc_url": "https://mainnet.animica.org/rpc",
                "node_rpc_url": "http://127.0.0.1:8545/rpc",
                "chain_id_expected": 1,
            }
        ],
    )
    assert get_active_rpc_url(cfg) == "https://mainnet.animica.org/rpc"


def test_get_active_rpc_url_uses_node_rpc_for_local_profile() -> None:
    cfg = Config(
        active_profile_id="local-1",
        rpc_profiles=[
            {
                "id": "local-1",
                "type": "local_node",
                "rpc_url": "http://127.0.0.1:8545/rpc",
                "node_rpc_url": "http://127.0.0.1:8545/rpc",
                "chain_id_expected": 1,
            }
        ],
    )
    assert get_active_rpc_url(cfg) == "http://127.0.0.1:8545/rpc"
