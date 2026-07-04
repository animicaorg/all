from __future__ import annotations

import pytest

from coretx import crypto


def test_assert_required_pq_for_chain_passes_when_required_enabled(monkeypatch):
    monkeypatch.setattr(
        crypto,
        "get_signature_policy_status",
        lambda: {
            "schemes": [
                # ANM-C01: mainnet now requires the real FIPS-204 scheme
                # ml_dsa_65 (scheme id 11), not the forgeable stubs 1 & 2.
                {"schemeId": 1, "enabledEffective": True},
                {"schemeId": 2, "enabledEffective": True},
                {"schemeId": crypto.SCHEME_ML_DSA_65, "enabledEffective": True},
            ]
        },
    )

    crypto.assert_required_pq_for_chain(chain_id=1, is_mainnet_rpc=True)


def test_assert_required_pq_for_chain_fails_when_missing(monkeypatch):
    monkeypatch.setattr(
        crypto,
        "get_signature_policy_status",
        lambda: {
            "schemes": [
                {"schemeId": 1, "enabledEffective": True},
                {"schemeId": 2, "enabledEffective": False, "reasonIfDisabled": "disabled_by_policy"},
            ]
        },
    )

    with pytest.raises(RuntimeError, match="Required PQ signature schemes"):
        crypto.assert_required_pq_for_chain(chain_id=1, is_mainnet_rpc=True)
