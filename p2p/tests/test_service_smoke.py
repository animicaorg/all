import asyncio

import pytest


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_p2pservice_start_stop():
    from p2p.node.service import P2PService

    port = _free_port()
    svc = P2PService(listen_addrs=[f"/ip4/127.0.0.1/tcp/{port}"], seeds=[], chain_id=1337)

    await svc.start()
    # Allow accept loop to spin once
    await asyncio.sleep(0.1)
    await svc.stop()
