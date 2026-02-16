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
