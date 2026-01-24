from __future__ import annotations

import json
from pathlib import Path

from p2p.deps import P2PDeps
from p2p.node.p2p_service_legacy import P2PService
from p2p.tests import tcp_multiaddr


GENESIS_TEMPLATE = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _make_devnet_genesis(tmp_path: Path) -> Path:
    genesis_path = tmp_path / "genesis.devnet.json"
    if genesis_path.exists():
        return genesis_path
    base_genesis = json.loads(GENESIS_TEMPLATE.read_text(encoding="utf-8"))
    base_genesis["chainId"] = 1337
    base_genesis["network"] = "animica-devnet"
    genesis_path.write_text(json.dumps(base_genesis, indent=2), encoding="utf-8")
    return genesis_path


def _make_deps(tmp_path: Path, name: str) -> P2PDeps:
    db_path = tmp_path / f"{name}.db"
    genesis_path = _make_devnet_genesis(tmp_path)
    return P2PDeps.open(f"sqlite:///{db_path}", str(genesis_path))


def test_dial_history_requires_handshake_ok(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_P2P_DISABLE_DEFAULT_SEEDS", "1")
    deps = _make_deps(tmp_path, "dial-history")
    svc = P2PService(
        listen_addrs=[tcp_multiaddr(0)],
        seeds=[],
        chain_id=deps.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / "dial-history" / "p2p"),
    )
    addr = "tcp://127.0.0.1:30333"
    addr_key = svc._addr_key(addr)
    svc._dial_attempts[addr_key] = 1
    svc._mark_dial_tcp_connected(addr, is_seed=False)

    last = svc._dial_history[-1]
    assert last["status"] == "tcp_connected"
    assert last["handshake_ok"] is False
    assert all(entry["status"] != "success" for entry in svc._dial_history)

    svc._dial_attempts[addr_key] = 1
    svc._mark_dial_success(addr, is_seed=False)
    last = svc._dial_history[-1]
    assert last["status"] == "handshake_ok"
    assert last["handshake_ok"] is True
