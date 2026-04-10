import asyncio

from rpc.methods import p2p as p2p_methods


def test_p2p_revalidate_txs_retries_unknown_with_bytes(monkeypatch):
    class Entry:
        def __init__(self, tx_bytes, status):
            self.tx_bytes = tx_bytes
            self.validation_status = status

    txid = bytes.fromhex("11" * 32)

    class Relay:
        def __init__(self):
            self._tx_store = {txid: Entry(b"raw", "unknown")}

        async def _admit_tx(self, raw, origin):
            assert raw == b"raw"
            return True, "accepted"

    relay = Relay()
    monkeypatch.setattr(p2p_methods, "_get_p2p_service", lambda: object())
    monkeypatch.setattr(p2p_methods, "_get_tx_relay_service", lambda _svc: relay)

    out = asyncio.run(p2p_methods.p2p_revalidate_txs("all"))
    assert out["success"] is True
    assert out["requested"] == 1
    assert out["admitted"][0]["txid"] == "0x" + txid.hex()
