"""CLI tests for the ``ena pool`` command group.

Drives ``animica.cli.ena:app`` through ``typer.testing.CliRunner`` with
``--json`` so each command's payload can be parsed back from stdout. Mirrors the
fixtures used by ``animica/ena/tests/test_ena_smoke.py`` and ``test_pool.py``:
``ANIMICA_ENA_HOME`` points at a tmp dir, and the funding test sets
``ENA_TREASURY_ADDRESS`` + monkeypatches ``animica.ena.payments.AnimicaRPC``.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from animica.cli.ena import app

runner = CliRunner()

TREASURY = "anim1zqpfpwctgp7zkfhj8qr77g3d0ucvp52n7fsv3xsjdclyzwzsjryp4gs07vma0"


def _write_dataset(path, n=20) -> str:
    rows = [{"prompt": f"Q{i}", "response": f"A{i}"} for i in range(n)]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


class _FakeRPC:
    def __init__(self, txs):
        self.txs = txs

    def get_transaction(self, txhash):
        from animica.ena.payments import _norm_hex
        return self.txs.get(_norm_hex(txhash))

    def get_status(self, txhash):
        tx = self.get_transaction(txhash) or {}
        return {"status": tx.get("status", "unknown")}


def _tx(to_digest, memo, value_nano, status="confirmed", frm="anim1payer"):
    return {"to_addr": "0x" + to_digest, "memo": memo, "value": str(value_nano),
            "status": status, "from_addr": frm}


def _invoke(*args):
    result = runner.invoke(app, ["--json", "pool", *args])
    assert result.exit_code == 0, result.stdout
    return json.loads(result.stdout)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    return tmp_path


def test_pool_cli_lifecycle(home):
    data = _write_dataset(home / "d.jsonl", n=20)

    # create
    pool = _invoke("create", "--base-model", "tiny", "--dataset", data,
                   "--name", "demo", "--num-shards", "2")
    pid = pool["pool_id"]
    assert pid.startswith("enapool-")
    assert pool["status"] == "open" and pool["round"] == 1

    # list
    listed = _invoke("list")
    assert any(p["pool_id"] == pid for p in listed["pools"])

    # status
    st = _invoke("status", pid)
    assert st["pool_id"] == pid and st["num_shards"] == 2

    # claim + submit both shards
    submitted = []
    for i in range(2):
        s = _invoke("claim", pid, "--worker-id", f"trainer-{i}")["claimed"]
        assert "manifest" in s and s["status"] == "claimed"
        sub = _invoke("submit", pid, s["shard_id"],
                      "--worker-id", f"trainer-{i}",
                      "--miner-address", f"anim1trainer{i}",
                      "--metrics", json.dumps({"samples": 10 * (i + 1)}))
        assert sub["status"] == "submitted"
        assert len(sub["checkpoint_hash"]) == 64
        submitted.append(sub)

    # nothing left to claim
    none_left = _invoke("claim", pid, "--worker-id", "trainer-late")
    assert none_left == {"claimed": None}

    # leaderboard reflects the two trainers
    lb = _invoke("leaderboard", pid)
    assert len(lb["leaderboard"]) == 2

    # aggregate (no eval gate) promotes + advances the round
    agg = _invoke("aggregate", pid)
    assert agg["promoted"] is True and agg["next_round"] == 2
    assert len(agg["served_checkpoint"]["checkpoint_hash"]) == 64

    # payout round 1: no budget (unfunded) so nothing pays out
    out = _invoke("payout", pid, "--round", "1")
    assert out["reason"] == "no_budget"


def test_pool_cli_funded_fund_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena"))
    monkeypatch.setenv("ENA_TREASURY_ADDRESS", TREASURY)
    from animica.ena import payments

    data = _write_dataset(tmp_path / "d.jsonl", n=20)
    pool = _invoke("create", "--base-model", "tiny", "--dataset", data,
                   "--name", "funded", "--num-shards", "2")
    pid = pool["pool_id"]

    quote = _invoke("fund-quote", pid, "--reward-anm", "10.0",
                    "--requester", "anim1alice")
    assert quote["memo"] == pool["pool_hash"]
    assert quote["required_nano"] == 10_000_000_000

    digest = payments.address_to_digest_hex(TREASURY)
    rpc = _FakeRPC({"a1": _tx(digest, pool["pool_hash"], 10_000_000_000,
                              frm="anim1alice")})
    monkeypatch.setattr(payments, "AnimicaRPC", lambda *a, **k: rpc)

    confirm = _invoke("fund-confirm", pid, "--txid", "a1",
                      "--requester", "anim1alice")
    assert confirm["funded"] is True
    assert confirm["budget_nano"] == 10_000_000_000

    st = _invoke("status", pid)
    assert st["budget_nano"] == 10_000_000_000


def test_pool_serve_errors_without_checkpoint(home, tmp_path):
    data = _write_dataset(tmp_path / "d.jsonl", n=8)
    pool = _invoke("create", "--base-model", "tiny", "--dataset", data,
                   "--name", "noserve", "--num-shards", "2")
    pid = pool["pool_id"]
    # serving needs a promoted checkpoint first → clean non-zero exit, no traceback
    res = runner.invoke(app, ["pool", "serve", pid, "--worker-id", "node-A"])
    assert res.exit_code == 1
    # rich may wrap the message across lines; normalise whitespace before matching
    out = " ".join(res.stdout.lower().split())
    assert "promoted checkpoint" in out
