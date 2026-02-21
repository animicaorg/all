from __future__ import annotations

from pathlib import Path

from aicf.protocol.state import ProtocolState
from rpc.methods.state import state_get_aicf_summary


def test_state_get_aicf_summary_empty_db(monkeypatch, tmp_path):
    data_dir = tmp_path / "animica-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(data_dir))

    summary = state_get_aicf_summary()

    assert summary["balance_total"] == "0"
    assert summary["minted_total"] == "0"
    assert summary["spent_total"] == "0"
    assert summary["last_update_height"] is None
    assert summary["last_update_hash"] is None


def test_state_get_aicf_summary_with_totals(monkeypatch, tmp_path):
    data_dir = tmp_path / "animica-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(data_dir))

    db_path = Path(data_dir) / "aicf_protocol.db"
    state = ProtocolState(str(db_path))
    state.update_aicf_totals(
        balance_delta=42,
        minted_delta=50,
        spent_delta=8,
        block_height=123,
        block_hash="0xabc",
    )

    summary = state_get_aicf_summary()

    assert summary["balance_total"] == "42"
    assert summary["minted_total"] == "50"
    assert summary["spent_total"] == "8"
    assert summary["last_update_height"] == 123
    assert summary["last_update_hash"] == "0xabc"
