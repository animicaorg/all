"""Tests for animica.ai.replay — offline receipt replay (7.1.1 P2)."""

from animica.ai import replay
from animica.ena import inference_receipt as IR
from animica.ena.models import ModelProviderConfig, sha3_hex
from animica.ena.providers import build_model_adapter


def _deterministic_receipt(prompt="What is 2+2?", seed=4242, max_tokens=64):
    cfg = ModelProviderConfig(name="d", provider="deterministic", model="deterministic")
    out = build_model_adapter(cfg).generate(prompt, seed=seed, max_tokens=max_tokens)
    r = IR.build_inference_receipt(model_id="deterministic", provider_key="deterministic",
                                   prompt=prompt, output=out, tokens_in=4, tokens_out=8,
                                   metadata={"seed_int": seed, "max_tokens": max_tokens})
    return r.to_dict(), prompt


def test_deterministic_replay_verified_match():
    d, prompt = _deterministic_receipt()
    res = replay.replay_receipt(d, prompt)
    assert res["reproducible"] == "verified" and res["match"] is True and res["prompt_ok"] is True


def test_tampered_output_hash_fails_match():
    d, prompt = _deterministic_receipt()
    d = dict(d)
    d["output_hash"] = "00" * 32
    res = replay.replay_receipt(d, prompt)
    assert res["match"] is False


def test_wrong_prompt_flagged():
    d, prompt = _deterministic_receipt()
    res = replay.replay_receipt(d, "an entirely different prompt")
    assert res["prompt_ok"] is False and res["match"] is False


def test_remote_backend_is_best_effort():
    d, prompt = _deterministic_receipt()
    d = dict(d)
    d["provider_key"] = "openai"
    res = replay.replay_receipt(d, prompt)
    assert res["reproducible"] == "best_effort" and res["match"] is None


def test_unknown_backend_unsupported():
    d, prompt = _deterministic_receipt()
    d = dict(d)
    d["provider_key"] = "mystery"
    assert replay.replay_receipt(d, prompt)["reproducible"] == "unsupported"


def test_audit_pick_is_deterministic():
    receipts = [{"receipt_hash": sha3_hex(str(i))} for i in range(20)]
    a = replay.audit_pick(receipts, k=4)
    b = replay.audit_pick(receipts, k=4)
    assert len(a) == 4 and a == b
    assert replay.audit_pick(receipts, k=0) == []
    assert len(replay.audit_pick(receipts, k=100)) == 20
