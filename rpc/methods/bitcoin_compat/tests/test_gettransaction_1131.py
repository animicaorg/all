"""11.3.1: Bitcoin-compat tx views speak anim1 addresses and know direction.

Before 11.3.1 `gettransaction` returned every tx as category "send" with a
negative amount (so an exchange confirming a DEPOSIT saw a withdrawal), put the
node's raw 0x-hex account key where Bitcoin clients expect an address (their own
bech32m validators then rejected it), and reported time 0 (1970).
"""
import os
import tempfile

import pytest

os.environ.setdefault(
    "ANIMICA_BTC_COMPAT_WALLET_FILE",
    os.path.join(tempfile.mkdtemp(prefix="btc1131_"), "wallet.json"),
)

import rpc.methods.bitcoin_compat.formatters as F  # noqa: E402
import rpc.methods.bitcoin_compat.wallet_methods as WM  # noqa: E402
import rpc.methods.bitcoin_compat.wallet_store as WS  # noqa: E402

DEPOSIT_KEY = "9d76976703eb21066f16b8ad1070abde946ef8602391c9f4775fd31b15d36f07"
DEPOSIT = "anim1zqpe6a5hvup7kggxdutt3tgswz4aa9rwlpsz8ywf73m4l5cmzhfk7pcqsu3y9"
HOT_KEY = "a15d728321989d9fb66cdbf9b5ad943db30b65b079eb74a36f7a4ce6d0c20eb1"
TXID = "0x" + "14" * 32
BLOCK_TS = 1790520000


def _stub(monkeypatch, *, frm, to, value=5_000_000_000, fee=0):
    def native(name, *a, **k):
        if name == "tx.getTransactionByHash":
            return {"blockNumber": 100, "blockHash": "0x" + "cd" * 32,
                    "body": {"from": "0x" + frm, "to": "0x" + to,
                             "value": value, "fee": fee}}
        if name == "chain.getHead":
            return {"height": 105}
        if name == "chain.getBlockByNumber":
            return {"timestamp": BLOCK_TS}
        if name == "mempool.getRawTx":
            return {}
        raise KeyError(name)

    monkeypatch.setattr(F, "native", native)
    WS.reset_for_tests()


def test_render_address_hex_to_anim1_round_trips():
    assert F.render_address("0x" + DEPOSIT_KEY) == DEPOSIT
    assert F.render_address(DEPOSIT.upper()) == DEPOSIT
    assert F.account_key(DEPOSIT).hex() == DEPOSIT_KEY
    assert F.render_address("not-an-address") == "not-an-address"


def test_deposit_to_watched_address_is_a_receive(monkeypatch):
    _stub(monkeypatch, frm=HOT_KEY, to=DEPOSIT_KEY)
    WS.watch(DEPOSIT, "deposits", baseline_nanos=0)
    r = WM.gettransaction(TXID)
    assert [d["category"] for d in r["details"]] == ["receive"]
    assert r["details"][0]["address"] == DEPOSIT
    assert r["amount"] == pytest.approx(5.0)
    assert r["time"] == r["blocktime"] == BLOCK_TS


def test_deposit_to_not_yet_imported_address_is_still_a_receive(monkeypatch):
    _stub(monkeypatch, frm=HOT_KEY, to=DEPOSIT_KEY)
    r = WM.gettransaction(TXID)
    assert r["details"] == [
        {"address": DEPOSIT, "category": "receive", "amount": pytest.approx(5.0), "vout": 0}
    ]


def test_withdrawal_from_watched_hot_wallet_is_a_send(monkeypatch):
    _stub(monkeypatch, frm=HOT_KEY, to=DEPOSIT_KEY, fee=1_000_000)
    WS.watch(F.render_address("0x" + HOT_KEY), "hot", baseline_nanos=0)
    r = WM.gettransaction(TXID)
    assert [d["category"] for d in r["details"]] == ["send"]
    assert r["details"][0]["address"] == DEPOSIT  # the DESTINATION, as anim1
    assert r["amount"] == pytest.approx(-5.0)
    assert r["fee"] == pytest.approx(-0.001)


def test_internal_sweep_nets_to_zero(monkeypatch):
    _stub(monkeypatch, frm=HOT_KEY, to=DEPOSIT_KEY)
    WS.watch(DEPOSIT, "deposits", baseline_nanos=0)
    WS.watch(F.render_address("0x" + HOT_KEY), "hot", baseline_nanos=0)
    r = WM.gettransaction(TXID)
    assert sorted(d["category"] for d in r["details"]) == ["receive", "send"]
    assert r["amount"] == pytest.approx(0.0)


def test_raw_tx_view_outputs_anim1_addresses():
    v = F.btc_tx_view({"hash": TXID, "body": {"from": "0x" + HOT_KEY,
                                               "to": "0x" + DEPOSIT_KEY, "value": 1}})
    assert v["vout"][0]["scriptPubKey"]["address"] == DEPOSIT
    assert v["vout"][0]["scriptPubKey"]["addresses"] == [DEPOSIT]
    assert v["vin"][0]["animica:from"].startswith("anim1")
