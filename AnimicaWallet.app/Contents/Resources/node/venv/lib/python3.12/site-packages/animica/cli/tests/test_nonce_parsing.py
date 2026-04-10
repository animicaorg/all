from __future__ import annotations

from animica.cli import tx


def test_coerce_int_parses_wrapped_nonce_values() -> None:
    assert tx._coerce_int(16) == 16
    assert tx._coerce_int("16") == 16
    assert tx._coerce_int("0x10") == 16
    assert tx._coerce_int({"nonce": 16}) == 16
    assert tx._coerce_int({"result": "0x10"}) == 16
    assert tx._coerce_int({"value": "7"}) == 7


def test_get_next_nonce_uses_pending_when_confirmed_missing(monkeypatch, capsys) -> None:
    def fake_rpc(_url: str, method: str, _params):  # noqa: ANN001
        if method == "state.getNextNonce":
            return {"nonce": "0x2"}
        return None

    monkeypatch.setattr(tx, "_rpc", fake_rpc)
    nonce = tx._get_next_nonce("http://node", "0x" + "11" * 32, nonce_source="confirmed")
    assert nonce == 2

    captured = capsys.readouterr()
    assert "Confirmed nonce unavailable" in captured.out
