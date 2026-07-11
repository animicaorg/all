"""Tests for animica.ai.receipt_mw — receipt attach middleware (7.1.1 P1)."""


def _args():
    return dict(provider_key="deterministic", resolved_model="deterministic",
                prompt="hello", output="[deterministic:abcd] hello", tokens_in=2, tokens_out=3,
                requester="anim1x")


def test_off_mode_is_byte_identical(monkeypatch):
    monkeypatch.setenv("ANIMICA_AI_RECEIPTS", "off")
    from animica.ai import receipt_mw
    base = {"id": "x", "choices": []}
    resp, receipt, hdr = receipt_mw.attach_receipt(dict(base), **_args())
    assert receipt is None and hdr is None
    assert "animica_receipt" not in resp and resp == base


def test_hash_mode_unsigned_receipt(monkeypatch):
    monkeypatch.setenv("ANIMICA_AI_RECEIPTS", "hash")
    from animica.ai import receipt_mw
    resp, receipt, hdr = receipt_mw.attach_receipt({"id": "x"}, **_args())
    assert receipt is not None and receipt.signed is False
    assert resp["animica_receipt"]["receipt_hash"] == hdr
    assert resp["animica_receipt"]["signed"] is False


def test_signed_mode_verifiable(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_AI_RECEIPTS", "signed")
    monkeypatch.setenv("ANIMICA_AI_HOME", str(tmp_path))
    monkeypatch.delenv("ANIMICA_PQ_MODE", raising=False)
    from animica.ai import receipt_mw, nodekey
    nodekey.reset_cache()
    resp, receipt, hdr = receipt_mw.attach_receipt({"id": "x"}, **_args())
    assert receipt.signed is True
    from animica.ena.inference_receipt import validate_inference_receipt
    v = validate_inference_receipt(resp["animica_receipt"])
    assert v["valid"] and v["signature_ok"]
    assert resp["animica_receipt"]["receipt_hash"] == hdr
    nodekey.reset_cache()


def test_signed_mode_degrades_to_hash_when_pq_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_AI_RECEIPTS", "signed")
    monkeypatch.setenv("ANIMICA_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ANIMICA_PQ_MODE", "disabled")
    from animica.ai import receipt_mw, nodekey
    nodekey.reset_cache()
    resp, receipt, hdr = receipt_mw.attach_receipt({"id": "x"}, **_args())
    assert receipt is not None and receipt.signed is False
    from animica.ena.inference_receipt import validate_inference_receipt
    assert validate_inference_receipt(resp["animica_receipt"])["valid"] is True
    nodekey.reset_cache()
