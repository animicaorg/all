import pytest

from p2p.node.p2p_service import P2PService


@pytest.mark.asyncio
async def test_import_peers_triggers_dial(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_P2P_PRIVATE_NETWORK", "1")
    service = P2PService(
        chain_id=1,
        listen_addrs=["/ip4/127.0.0.1/tcp/0"],
        seeds=[],
        peerstore_path=str(tmp_path),
    )
    dialed: list[str] = []

    async def _fake_dial(addr: str, *args, **kwargs) -> bool:
        dialed.append(addr)
        return True

    monkeypatch.setattr(service, "_dial", _fake_dial)

    addr = "/ip4/127.0.0.1/tcp/30333"
    expected = service._sanitize_peer_addr(addr, fallback_port=service._local_listen_port())
    result = await service.import_peers([addr])

    assert result["dial_attempted"] == 1
    assert result["dial_success"] == 1
    assert dialed == [expected]
