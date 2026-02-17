from __future__ import annotations

import json

from coretx import crypto


def _with_env(monkeypatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_override_disabled_by_default_does_not_enable(monkeypatch, tmp_path):
    override_file = tmp_path / "policy_override.json"
    override_file.write_text(
        json.dumps({"allowSigSchemes": [2], "denySigSchemes": [], "mode": "override_allow", "comment": "test"}),
        encoding="utf-8",
    )

    _with_env(
        monkeypatch,
        ANIMICA_DISABLED_SIGNATURE_SCHEMES="2",
        ANIMICA_POLICY_OVERRIDE_FILE=str(override_file),
        ANIMICA_ENABLE_POLICY_OVERRIDE="0",
    )
    crypto._bootstrap_schemes()

    status = crypto.get_signature_policy_status()
    sphincs = next(s for s in status["schemes"] if s["schemeId"] == 2)
    assert sphincs["enabledByPolicy"] is False
    assert sphincs["enabledEffective"] is False


def test_override_allow_enables_previously_disabled_scheme(monkeypatch, tmp_path):
    override_file = tmp_path / "policy_override.json"
    override_file.write_text(
        json.dumps({"allowSigSchemes": [2], "denySigSchemes": [], "mode": "override_allow", "comment": "break glass"}),
        encoding="utf-8",
    )

    _with_env(
        monkeypatch,
        ANIMICA_DISABLED_SIGNATURE_SCHEMES="2",
        ANIMICA_POLICY_OVERRIDE_FILE=str(override_file),
        ANIMICA_ENABLE_POLICY_OVERRIDE="1",
    )
    crypto._bootstrap_schemes()

    status = crypto.get_signature_policy_status()
    sphincs = next(s for s in status["schemes"] if s["schemeId"] == 2)
    assert sphincs["enabledByPolicy"] is True


def test_scheme_policy_reject_contains_supported_matrix(monkeypatch):
    monkeypatch.setenv("ANIMICA_DISABLED_SIGNATURE_SCHEMES", "2")
    monkeypatch.delenv("ANIMICA_ENABLE_POLICY_OVERRIDE", raising=False)
    monkeypatch.delenv("ANIMICA_POLICY_OVERRIDE_FILE", raising=False)
    crypto._bootstrap_schemes()

    result = crypto.verify_signature(2, b"m", b"s" * 7856, b"p" * 32)
    assert result.ok is False
    assert result.reason == "scheme_disabled_by_policy"
    assert result.diagnostics["kind"] == "scheme_disabled_by_policy"
    assert isinstance(result.diagnostics.get("supported"), list)


def test_pq_allowlist_fallback_only_applies_on_policy_load_failure(monkeypatch):
    monkeypatch.setenv("ANIMICA_ENABLE_PQ_ALLOWLIST_FALLBACK", "1")
    monkeypatch.setenv("ANIMICA_PQ_ALLOWLIST", "dilithium3,sphincs128s")
    monkeypatch.delenv("ANIMICA_ALLOWED_SIG_SCHEMES", raising=False)
    monkeypatch.delenv("ANIMICA_DISABLED_SIGNATURE_SCHEMES", raising=False)
    monkeypatch.delenv("ANIMICA_ENABLE_POLICY_OVERRIDE", raising=False)
    monkeypatch.delenv("ANIMICA_POLICY_OVERRIDE_FILE", raising=False)

    crypto._bootstrap_schemes()
    status = crypto.get_signature_policy_status()
    policy_enabled = {s["schemeId"] for s in status["schemes"] if s["enabledByPolicy"]}
    assert 1 in policy_enabled
    assert 2 in policy_enabled


def test_pq_allowlist_fallback_activates_for_missing_policy_file(monkeypatch):
    monkeypatch.setenv("ANIMICA_ENABLE_PQ_ALLOWLIST_FALLBACK", "1")
    monkeypatch.setenv("ANIMICA_PQ_ALLOWLIST", "dilithium3,sphincs128s")
    monkeypatch.setenv("ANIMICA_ENABLE_POLICY_OVERRIDE", "1")
    monkeypatch.setenv("ANIMICA_POLICY_OVERRIDE_FILE", "/tmp/does-not-exist-policy.json")
    monkeypatch.delenv("ANIMICA_ALLOWED_SIG_SCHEMES", raising=False)
    monkeypatch.delenv("ANIMICA_DISABLED_SIGNATURE_SCHEMES", raising=False)

    crypto._bootstrap_schemes()
    status = crypto.get_signature_policy_status()
    policy_enabled = {s["schemeId"] for s in status["schemes"] if s["enabledByPolicy"]}
    assert 1 in policy_enabled
    assert 2 in policy_enabled
