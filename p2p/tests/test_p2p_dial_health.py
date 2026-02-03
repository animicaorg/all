from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from p2p.deps import P2PDeps
from p2p.node.p2p_service import P2PService, _NullTxRelay
from p2p.transport.base import ConnInfo
from p2p.tests import tcp_multiaddr

GENESIS_PATH = Path(__file__).resolve().parents[2] / "core" / "genesis" / "genesis.json"


def _make_devnet_genesis(tmp_path: Path) -> Path:
    genesis_path = tmp_path / "genesis.devnet.json"
    if genesis_path.exists():
        return genesis_path
    base_genesis = json.loads(GENESIS_PATH.read_text(encoding="utf-8"))
    base_genesis["chainId"] = 1337
    base_genesis["network"] = "animica-devnet"
    consensus = base_genesis.get("consensus") or {}
    consensus["initialThetaMicro"] = 1
    base_genesis["consensus"] = consensus
    params_ref = base_genesis.get("paramsRef") or {}
    params_ref["path"] = str(
        Path(__file__).resolve().parents[2] / "spec" / "params.yaml"
    )
    base_genesis["paramsRef"] = params_ref
    genesis_path.write_text(json.dumps(base_genesis, indent=2), encoding="utf-8")
    return genesis_path


def _make_service(tmp_path: Path, name: str) -> P2PService:
    db_path = tmp_path / f"{name}.db"
    genesis_path = _make_devnet_genesis(tmp_path)
    deps = P2PDeps.open(f"sqlite:///{db_path}", str(genesis_path))
    return P2PService(
        listen_addrs=[tcp_multiaddr(0)],
        seeds=[],
        chain_id=deps.chain_id,
        deps=deps,
        peerstore_path=str(tmp_path / name / "p2p"),
    )


class _FakeStream:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._payload = b""

    async def send(self, data: bytes) -> None:
        return None

    async def recv(self) -> bytes:
        await self._event.wait()
        return self._payload

    def close(self) -> None:
        self._payload = b""
        self._event.set()


class _FakeConn:
    def __init__(self, remote: str) -> None:
        self.info = ConnInfo(remote_addr=remote, local_addr="127.0.0.1:0")
        self._stream = _FakeStream()

    async def open_stream(self) -> _FakeStream:
        return self._stream

    async def close(self) -> None:
        self._stream.close()


class _FakeTransport:
    def __init__(self, remote: str) -> None:
        self._remote = remote

    async def dial(self, addr: str, timeout: float | None = None) -> _FakeConn:
        _ = timeout
        return _FakeConn(self._remote)

    async def accept(self) -> _FakeConn:
        raise RuntimeError("not used in test")

    def addresses(self) -> list[str]:
        return ["tcp://127.0.0.1:0"]


@pytest.mark.asyncio
async def test_tx_relay_failure_does_not_break_p2p(monkeypatch, tmp_path: Path) -> None:
    def _boom(*_args, **_kwargs):
        raise RuntimeError("tx relay init failed")

    monkeypatch.setattr("p2p.node.p2p_service.TxRelayService", _boom)
    svc = _make_service(tmp_path, "txrelay-fail")
    assert not svc._tx_relay_enabled
    assert isinstance(svc._txrelay, _NullTxRelay)

    svc._transport = _FakeTransport("tcp://203.0.113.10:30333")
    ok = await svc._dial("tcp://203.0.113.10:30333")
    assert ok
    assert len(svc._peers) == 1

    for task in list(svc._child_tasks):
        task.cancel()
    await asyncio.gather(*svc._child_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_dial_records_success_and_peer_added(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, "dial-success")
    svc._transport = _FakeTransport("tcp://203.0.113.11:30333")
    ok = await svc._dial("tcp://203.0.113.11:30333")
    assert ok
    assert len(svc._peers) == 1
    assert svc._dial_attempt_log
    assert svc._dial_attempt_log[-1]["stage"] == "success"

    for task in list(svc._child_tasks):
        task.cancel()
    await asyncio.gather(*svc._child_tasks, return_exceptions=True)
