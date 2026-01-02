from __future__ import annotations

from contextlib import nullcontext

import cbor2
from typer.testing import CliRunner

from animica.cli import tx


runner = CliRunner()


def test_send_retries_on_nonce_too_low(monkeypatch) -> None:
    nonces: list[int] = []
    send_calls = 0
    nonce_calls = 0

    def fake_rpc(_url: str, method: str, params):  # noqa: ANN001
        nonlocal send_calls, nonce_calls
        if method == "sync.getStatus":
            return {"synchronized": True}
        if method == "chain.getChainIdentity":
            return {"chainId": 1337, "forkId": None}
        if method in {"state.getNextNonce", "state_getNextNonce"}:
            nonce_calls += 1
            return 18 if nonce_calls == 1 else 19
        if method in {"tx.gasPrice", "gasPrice", "fee.getGasPrice"}:
            return 1
        if method == "tx.sendRawTransaction":
            send_calls += 1
            raw_hex = params[0]
            raw_bytes = bytes.fromhex(raw_hex[2:] if raw_hex.startswith("0x") else raw_hex)
            decoded = cbor2.loads(raw_bytes)
            nonces.append(int(decoded["body"]["nonce"]))
            return f"0xhash{send_calls}"
        if method == "mempool.getStatus":
            if send_calls == 1:
                return {
                    "hash": params[0],
                    "known": True,
                    "state": "evicted",
                    "reason": "nonce_too_low",
                    "details": {"expected": 19, "got": 18},
                }
            return {"hash": params[0], "known": True, "state": "pending", "reason": None}
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
        ],
    )

    assert result.exit_code == 0, result.output
    assert "nonce mismatch, retrying with nonce=19" in result.output
    assert nonces == [18, 19]
