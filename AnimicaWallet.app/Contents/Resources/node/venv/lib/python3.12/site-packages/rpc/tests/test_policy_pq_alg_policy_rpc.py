from __future__ import annotations

from rpc.methods import tx2


def test_policy_get_pq_alg_policy_returns_chain_allowlist(monkeypatch):
    monkeypatch.setattr(tx2.deps, "get_chain_id", lambda: 1337)
    monkeypatch.setattr(
        tx2,
        "list_scheme_descriptors",
        lambda: [
            {"schemeId": 1, "name": "dilithium3", "enabledEffective": False},
            {"schemeId": 2, "name": "sphincs_shake_128s", "enabledEffective": True},
        ],
    )
    monkeypatch.setattr(tx2, "get_signature_policy_status", lambda: {"policyRoots": {"pqAlgPolicy": "ANIMICA_ALLOWED_SIG_SCHEMES"}})

    result = tx2.get_pq_alg_policy()

    assert result["chainId"] == 1337
    assert result["defaultSchemeId"] == 2
    assert result["allowedSchemes"] == [
        {"id": 1, "name": "dilithium3", "enabled": False},
        {"id": 2, "name": "sphincs_shake_128s", "enabled": True},
    ]
    assert result["policyRoot"] == "ANIMICA_ALLOWED_SIG_SCHEMES"


def test_policy_get_effective_returns_policy_hash_and_enabled(monkeypatch):
    monkeypatch.setattr(tx2.deps, "get_chain_id", lambda: 1)
    monkeypatch.setattr(
        tx2,
        "list_scheme_descriptors",
        lambda: [
            {"schemeId": 1, "name": "dilithium3", "enabledEffective": True},
            {"schemeId": 2, "name": "sphincs_shake_128s", "enabledEffective": True},
        ],
    )
    monkeypatch.setattr(
        tx2,
        "get_signature_policy_status",
        lambda: {
            "policyRoots": {"pqAlgPolicy": "ANIMICA_DISABLED_SIGNATURE_SCHEMES"},
            "override": {"enabled": False},
        },
    )

    result = tx2.get_effective_policy()

    assert result["chainId"] == 1
    assert result["enabledSignatureSchemes"] == ["dilithium3", "sphincs_shake_128s"]
    assert result["policyRoots"]["pqAlgPolicy"] == "ANIMICA_DISABLED_SIGNATURE_SCHEMES"
    assert isinstance(result.get("policyHash"), str)
    assert result["policyHash"].startswith("0x")
