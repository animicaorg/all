from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.encoding.cbor import dumps as cbor_dumps
from core.encoding.cbor import loads as cbor_loads
from core.utils.tx import normalize_tx_envelope
from mempool.errors import AdmissionError, ReplacementUnsupported
from mempool.pool import Pool, PoolConfig
from rpc.mempool_service import MempoolService
from rpc.methods.mempool import mempool_drop_transaction


def _build_body(nonce: int = 0) -> dict:
    sender = b"\x11" * 32
    recipient = b"\x22" * 32
    return {
        "chainId": 1,
        "from": "0x" + sender.hex(),
        "to": "0x" + recipient.hex(),
        "nonce": nonce,
        "value": 1,
        "gasLimit": 21_000,
        "maxFee": 1,
        "data": b"",
    }


def _new_service(tmp_path) -> MempoolService:
    pool = Pool(cfg=PoolConfig())
    return MempoolService(
        pool=pool,
        chain_id=1,
        min_gas_price_wei=0,
        state_db=None,
        tx_index=None,
        data_dir=str(tmp_path),
        persist_enabled=True,
        persist_ttl_s=600,
    )


def test_normalize_tx_envelope_shapes() -> None:
    body = _build_body()
    canonical = normalize_tx_envelope({"body": body, "sigs": []})
    raw_body = cbor_dumps({"body": body, "sigs": []})

    shapes = [
        {"body": body, "sigs": []},
        {"tx": canonical["tx"], "sigs": []},
        {"tx": canonical["tx"], "body": body, "sigs": []},
        {"raw": "0x" + raw_body.hex()},
    ]

    for shape in shapes:
        normalized = normalize_tx_envelope(shape)
        assert "tx" in normalized
        assert "sigs" in normalized
        assert normalized["nonce"] == 0
        decoded = cbor_loads(normalized["raw"])
        assert "tx" in decoded
        assert "body" not in decoded


def test_mempool_persists_canonical_envelope(tmp_path) -> None:
    service = _new_service(tmp_path)
    body = _build_body()
    raw = cbor_dumps({"body": body, "sigs": []})
    tx_hash = service.submit(tx={"body": body, "sigs": []}, raw=raw, local=True)

    persist_path = tmp_path / "mempool" / "pending.jsonl"
    entries = [json.loads(line) for line in persist_path.read_text().splitlines() if line.strip()]
    assert any(entry.get("hash") == tx_hash for entry in entries)

    raw_hex = next(entry["raw"] for entry in entries if entry.get("hash") == tx_hash)
    decoded = cbor_loads(bytes.fromhex(raw_hex))
    assert "tx" in decoded
    assert "body" not in decoded


def test_mempool_rejects_malformed_tx(tmp_path) -> None:
    service = _new_service(tmp_path)
    raw = cbor_dumps({"foo": "bar"})
    with pytest.raises(AdmissionError):
        service.submit(tx={"foo": "bar"}, raw=raw, local=True)
    persist_path = tmp_path / "mempool" / "pending.jsonl"
    if persist_path.exists():
        assert not persist_path.read_text().strip()


def test_idempotent_submit_and_drop(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = _new_service(tmp_path)
    body = _build_body(nonce=0)
    raw = cbor_dumps({"body": body, "sigs": []})
    tx_hash = service.submit(tx={"body": body, "sigs": []}, raw=raw, local=True)
    assert service.submit(tx={"body": body, "sigs": []}, raw=raw, local=True) == tx_hash

    replacement_body = _build_body(nonce=0)
    replacement_body["value"] = 2
    replacement_raw = cbor_dumps({"body": replacement_body, "sigs": []})
    with pytest.raises(ReplacementUnsupported):
        service.submit(tx={"body": replacement_body, "sigs": []}, raw=replacement_raw, local=True)

    monkeypatch.setattr("rpc.methods.tx.deps.get_ctx", lambda: SimpleNamespace(mempool=service))
    drop_res = mempool_drop_transaction(tx_hash)
    assert drop_res["dropped"] is True
    assert service.has_hash(tx_hash) is False


def test_load_eviction_writes_quarantine(tmp_path) -> None:
    service = _new_service(tmp_path)
    body = _build_body()
    raw = cbor_dumps({"body": body, "sigs": []})
    tx_hash = service.submit(tx={"body": body, "sigs": []}, raw=raw, local=True)

    persist_path = tmp_path / "mempool" / "pending.jsonl"
    with persist_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"hash": "0xdead", "raw": "zz"}) + "\n")

    reloaded = _new_service(tmp_path)
    assert reloaded.has_hash(tx_hash) is True
    quarantine = persist_path.with_suffix(".bad.jsonl")
    assert quarantine.exists()
