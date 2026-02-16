from __future__ import annotations

import pytest

from rpc import errors as rpc_errors
from rpc.methods import tx2


class _Ctx:
    def __init__(self, host: str, headers: dict[str, str] | None = None):
        self.client = (host, 12345)
        self.headers = headers or {}


def test_get_supported_signature_schemes_has_policy_fields(monkeypatch):
    monkeypatch.setattr(
        tx2,
        "list_scheme_descriptors",
        lambda: [
            {
                "schemeId": 1,
                "name": "dilithium3",
                "pubkeyLengths": [1952],
                "signatureLengths": [3293],
                "enabledByCode": True,
                "enabledByPolicy": False,
                "enabledEffective": False,
                "reasonIfDisabled": "disabled_by_policy",
            }
        ],
    )
    monkeypatch.setattr(tx2, "get_signature_policy_status", lambda: {"policyRoots": {"pqAlgPolicy": "root"}})

    out = tx2.get_supported_signature_schemes()
    assert "policyRoots" in out
    assert out["schemes"][0]["enabledByPolicy"] is False
    assert out["schemes"][0]["enabledEffective"] is False


def test_admin_get_policy_status_denies_remote_without_token(monkeypatch):
    monkeypatch.delenv("ANIMICA_RPC_ADMIN_TOKEN", raising=False)
    with pytest.raises(rpc_errors.AccessDenied):
        tx2.get_policy_status(ctx=_Ctx("8.8.8.8"))


def test_admin_get_policy_status_allows_token(monkeypatch):
    monkeypatch.setenv("ANIMICA_RPC_ADMIN_TOKEN", "secret")
    monkeypatch.setattr(tx2, "get_signature_policy_status", lambda: {"policyRoots": {}, "override": {"enabled": True}})
    monkeypatch.setattr(tx2, "list_scheme_descriptors", lambda: [])

    out = tx2.get_policy_status(ctx=_Ctx("8.8.8.8", {"x-animica-admin-token": "secret"}))
    assert out["override"]["enabled"] is True
