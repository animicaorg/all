import socket

from mining.pool import PoolConfig, StratumPool


def test_pool_start_handles_p2p_port_conflict():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    cfg = PoolConfig(
        rpc_url="http://127.0.0.1:8547",
        listen_host="127.0.0.1",
        listen_port=0,
        p2p_port=port,
        no_p2p=False,
    )
    pool = StratumPool(cfg)
    pool._check_p2p_port()
    sock.close()
