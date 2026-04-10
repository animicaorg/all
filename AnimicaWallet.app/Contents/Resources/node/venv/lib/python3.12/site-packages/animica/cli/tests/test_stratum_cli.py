from __future__ import annotations

from pathlib import Path
from typing import Any

from animica.cli import stratum
from typer.testing import CliRunner

runner = CliRunner()


def _header_template() -> dict[str, object]:
    return {
        "v": 1,
        "chainId": 1,
        "height": 1,
        "parentHash": "0x" + "11" * 32,
        "timestamp": 1775784221,
        "stateRoot": "0x" + "22" * 32,
        "txsRoot": "0x" + "33" * 32,
        "receiptsRoot": "0x" + "44" * 32,
        "proofsRoot": "0x" + "55" * 32,
        "daRoot": "0x" + "66" * 32,
        "mixSeed": "0x" + "77" * 32,
        "poiesPolicyRoot": "0x" + "88" * 32,
        "pqAlgPolicyRoot": "0x" + "99" * 32,
        "thetaMicro": 1000,
        "nonce": 0,
        "extra": "0x",
    }


def test_init_writes_pool_env(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc")
    monkeypatch.setenv("ANIMICA_POOL_ADDRESS", "anim1pooltest")

    target = tmp_path / "animica-pool.env"
    result = runner.invoke(stratum.app, ["init", "--path", str(target)])

    assert result.exit_code == 0
    content = target.read_text(encoding="utf-8")
    assert "ANIMICA_RPC_URL=http://127.0.0.1:8545/rpc" in content
    assert "ANIMICA_POOL_ADDRESS=anim1pooltest" in content


def test_show_config_alias(monkeypatch: Any) -> None:
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://rpc.local/rpc")
    monkeypatch.setenv("ANIMICA_POOL_ADDRESS", "anim1pooltest")

    result = runner.invoke(stratum.app, ["show-config"])

    assert result.exit_code == 0
    assert "RPC URL: http://rpc.local/rpc" in result.output
    assert "Pool address: anim1pooltest" in result.output


def test_list_workers_uses_pool_api(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("ANIMICA_SERVICE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(stratum, "read_metadata", lambda _state: {"api_url": "http://pool.local"})
    monkeypatch.setattr(
        stratum,
        "_api_json_get",
        lambda api_url, path, timeout=5.0: {
            "items": [
                {
                    "worker_id": "rig-1",
                    "address": "anim1worker",
                    "shares_accepted": 7,
                    "shares_rejected": 2,
                    "blocks_found": 1,
                    "hashrate_1m": 0.5,
                }
            ]
        },
    )

    result = runner.invoke(stratum.app, ["list-workers"])

    assert result.exit_code == 0
    assert "rig-1" in result.output
    assert "accepted=7" in result.output
    assert "blocks=1" in result.output


def test_doctor_reports_success(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("ANIMICA_SERVICE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ANIMICA_RPC_URL", "http://node.local/rpc")
    monkeypatch.setenv(
        "ANIMICA_POOL_ADDRESS",
        "anim1zqp2rdpnhwvvfe03ts9tf9rnp7p449xhnvh0u0wpvle3wahtce64zwgz8m208",
    )
    monkeypatch.setattr(stratum, "read_metadata", lambda _state: {"api_url": "http://pool.local"})
    monkeypatch.setattr(stratum, "read_pid", lambda _state: 4321)
    monkeypatch.setattr(stratum, "is_running", lambda pid: bool(pid))

    def fake_rpc_json_call(
        rpc_url: str,
        method: str,
        params: Any | None = None,
        *,
        timeout: float = 5.0,
    ) -> Any:
        assert rpc_url == "http://node.local/rpc"
        if method == "chain.getHead":
            return {"height": 7, "hash": "0x" + "aa" * 32}
        if method == "miner.getBlockTemplate":
            return {
                "templateId": "tmpl-1",
                "target": "0x" + "ff" * 32,
                "header": _header_template(),
                "txs": [],
            }
        raise AssertionError(f"unexpected method: {method}")

    def fake_api_json_get(api_url: str, path: str, *, timeout: float = 5.0) -> Any:
        assert api_url == "http://pool.local"
        if path == "/healthz":
            return {"status": "ok"}
        if path == "/summary":
            return {"num_workers": 1, "blocks_found_total": 2, "height": 8}
        raise AssertionError(f"unexpected path: {path}")

    monkeypatch.setattr(stratum, "_rpc_json_call", fake_rpc_json_call)
    monkeypatch.setattr(stratum, "_api_json_get", fake_api_json_get)

    result = runner.invoke(stratum.app, ["doctor"])

    assert result.exit_code == 0
    assert "PASS pool_address" in result.output
    assert "PASS node_rpc" in result.output
    assert "PASS template" in result.output
    assert "PASS pool_api" in result.output
