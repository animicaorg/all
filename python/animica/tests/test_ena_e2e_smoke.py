from __future__ import annotations

import json
from pathlib import Path

from animica.ena.smoke import run_ena_smoke_test


def test_ena_e2e_smoke(tmp_path: Path) -> None:
    report = run_ena_smoke_test(work_dir=tmp_path / "ena-smoke")

    assert report["ok"] is True
    assert report["timings"]["total_seconds"] < 120
    assert report["da_mode"] in {"rpc", "dev_stub"}

    hashes = report["hashes"]
    assert len(hashes["full_snapshot_hash"]) == 64
    assert len(hashes["weights_hash"]) == 64
    assert len(hashes["tokenizer_hash"]) == 64

    trace = json.loads((Path(report["work_dir"]) / "rpc_trace.json").read_text())
    tx_calls = [call for call in trace if call.get("method") == "tx_sendRawTransaction"]
    assert tx_calls, "expected tx_sendRawTransaction path"
    assert isinstance(tx_calls[0]["params"], list)
    assert len(tx_calls[0]["params"]) == 1
    assert isinstance(tx_calls[0]["params"][0], str)

    assert report["aicf"]["after_reward_slice"] >= report["aicf"]["before_reward_slice"]
