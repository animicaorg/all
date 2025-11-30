import json
import socket
import threading
from types import SimpleNamespace

import pytest

from p2p.cli import peer as peer_cli
from p2p.tests import free_port


class _Listener(threading.Thread):
    def __init__(self, host: str, port: int):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self._stop_evt = threading.Event()
        self.ready = threading.Event()

    def run(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self.host, self.port))
            s.listen(1)
            self.ready.set()
            s.settimeout(0.5)
            while not self._stop_evt.is_set():
                try:
                    conn, _ = s.accept()
                except socket.timeout:
                    continue
                with conn:
                    pass

    def stop(self) -> None:
        self._stop_evt.set()


@pytest.fixture

def temp_store(tmp_path):
    path = tmp_path / "peers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _make_args(store, peer_id, addr, probe=True, timeout=1.0):
    return SimpleNamespace(store=store, peer_id=peer_id, addr=addr, probe=probe, timeout=timeout)


def test_probe_success_updates_peer(temp_store, capsys):
    port = free_port()
    listener = _Listener("127.0.0.1", port)
    listener.start()
    listener.ready.wait(timeout=2)

    addr = f"/ip4/127.0.0.1/tcp/{port}"
    rc = peer_cli.cmd_add(_make_args(temp_store, "peer-ok", addr))
    listener.stop()
    listener.join(timeout=2)

    assert rc == 0
    captured = capsys.readouterr().out
    assert "[probe] TCP connect OK" in captured
    data = json.loads(temp_store.read_text())
    peers = {p["peer_id"]: p for p in data.get("peers", [])}
    assert peers["peer-ok"]["last_seen"] is not None


def test_probe_failure_is_non_fatal(temp_store, capsys):
    port = free_port()
    addr = f"/ip4/127.0.0.1/tcp/{port}"
    rc = peer_cli.cmd_add(_make_args(temp_store, "peer-fail", addr, probe=True, timeout=0.25))
    assert rc == 0
    captured = capsys.readouterr().out
    assert "[probe] FAILED" in captured
    data = json.loads(temp_store.read_text())
    peers = {p["peer_id"]: p for p in data.get("peers", [])}
    assert peers["peer-fail"]["addrs"] == [addr]
    assert peers["peer-fail"].get("last_seen") is None


def test_store_facade_writes_json(temp_store):
    store = peer_cli.StoreFacade(temp_store)
    store.ensure_addr("peer-123", "/ip4/1.2.3.4/tcp/3333")

    data = json.loads(temp_store.read_text())
    peers = {p["peer_id"]: p for p in data.get("peers", [])}
    assert peers["peer-123"]["addrs"] == ["/ip4/1.2.3.4/tcp/3333"]


def test_parse_addr_handles_multiaddr_object(monkeypatch):
    class FakeMultiaddr:
        def __init__(self, raw: str):
            self._raw = raw

        def __str__(self) -> str:  # pragma: no cover - trivial
            return self._raw

    monkeypatch.setattr(peer_cli, "_parse_multiaddr", lambda raw: FakeMultiaddr(raw))
    host, port = peer_cli.parse_addr("/ip4/10.1.2.3/tcp/4100")

    assert host == "10.1.2.3"
    assert port == 4100
