from __future__ import annotations

from contextlib import nullcontext
from typer.testing import CliRunner

from animica.cli import tx


runner = CliRunner()


def test_send_waits_through_reorg(monkeypatch) -> None:
    send_calls = 0
    status_calls = 0
    time_now = {"t": 0.0}

    def fake_time() -> float:
        time_now["t"] += 0.1
        return time_now["t"]

    def fake_rpc(_url: str, method: str, params):  # noqa: ANN001
        nonlocal send_calls, status_calls
        if method == "sync.getStatus":
            return {"synchronized": True}
        if method == "chain.getChainIdentity":
            return {"chainId": 1337, "forkId": None}
        if method in {"state.getNextNonce", "state_getNextNonce"}:
            return 1
        if method in {"tx.gasPrice", "gasPrice", "fee.getGasPrice"}:
            return 1
        if method == "tx.simulateRawTransaction":
            return {"ok": True, "result": {"status": "success"}}
        if method == "tx.sendRawTransaction":
            send_calls += 1
            return {"hash": "0xtxhash", "accepted_to_mempool": True, "persisted_to_chain": False}
        if method == "mempool.getStatus":
            return {"hash": params[0], "known": True, "state": "pending", "reason": None}
        if method == "chain.getFinalityDepth":
            return 1
        if method == "tx.getStatus":
            status_calls += 1
            if status_calls == 1:
                return {"hash": "0xtxhash", "status": "reorged_out", "safe_confirmations": 0}
            return {"hash": "0xtxhash", "status": "confirmed", "safe_confirmations": 1}
        return None

    class DummySig:
        alg_id = 1
        sig = b"\x01" * 64

    monkeypatch.setattr(tx, "_rpc", fake_rpc)
    monkeypatch.setattr(tx, "_load_wallet_entry", lambda _addr: {"public_key_hex": "11" * 32, "secret_key_hex": "22" * 32})
    monkeypatch.setattr(tx, "build_sign_bytes", lambda *_args, **_kwargs: b"signbytes")
    monkeypatch.setattr(tx, "pq_sign_detached", lambda *_args, **_kwargs: DummySig())
    monkeypatch.setattr(tx, "verify_detached", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(tx, "_nonce_lock", lambda _addr: nullcontext())
    monkeypatch.setattr(tx.time, "sleep", lambda _s: None)
    monkeypatch.setattr(tx.time, "time", fake_time)

    result = runner.invoke(
        tx.app,
        [
            "send",
            "--from",
            "0x" + "11" * 32,
            "--to",
            "0x" + "22" * 32,
            "--value-nanm",
            "1",
            "--rpc-url",
            "http://node",
            "--wait",
            "--confirmations",
            "1",
            "--confirm-timeout",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Transaction confirmed" in result.output
