"""Tests for expanded template library — basics, DAO, utility, AICF, security categories."""
from __future__ import annotations

from pathlib import Path

import pytest

from animica_studio.services.template_service import TemplateService
from animica_studio.services.token_template_service import TokenTemplateService


# ---------------------------------------------------------------------------
# IDE script templates — template service
# ---------------------------------------------------------------------------


@pytest.fixture()
def template_svc() -> TemplateService:
    svc = TemplateService()
    svc.load_builtin_templates()
    return svc


def test_new_basics_templates_present(template_svc: TemplateService) -> None:
    ids = {t.id for t in template_svc.list_templates()}
    expected = {"counter", "key_value_store", "ownership", "pause_unpause"}
    assert expected.issubset(ids), f"Missing basic templates: {expected - ids}"


def test_dao_templates_present(template_svc: TemplateService) -> None:
    ids = {t.id for t in template_svc.list_templates()}
    expected = {"proposal_registry", "weighted_voting", "treasury"}
    assert expected.issubset(ids), f"Missing DAO templates: {expected - ids}"


def test_utility_templates_present(template_svc: TemplateService) -> None:
    ids = {t.id for t in template_svc.list_templates()}
    expected = {"escrow", "payment_splitter", "multisig_wallet"}
    assert expected.issubset(ids), f"Missing utility templates: {expected - ids}"


def test_aicf_templates_present(template_svc: TemplateService) -> None:
    ids = {t.id for t in template_svc.list_templates()}
    expected = {"contribution_receipt", "da_commitment_registry", "checkpoint_registry"}
    assert expected.issubset(ids), f"Missing AICF templates: {expected - ids}"


def test_security_templates_present(template_svc: TemplateService) -> None:
    ids = {t.id for t in template_svc.list_templates()}
    expected = {"role_based_access", "input_validation", "safe_arithmetic"}
    assert expected.issubset(ids), f"Missing security templates: {expected - ids}"


def test_template_categories(template_svc: TemplateService) -> None:
    cats = set(template_svc.categories())
    assert "Basics" in cats
    assert "DAO / Governance" in cats
    assert "Utility / Infra" in cats
    assert "AICF / ENA" in cats
    assert "Security Patterns" in cats


def test_render_counter_template(template_svc: TemplateService, tmp_path: Path) -> None:
    rendered = template_svc.render(
        "counter",
        {"CONTRACT_NAME": "MyCounter", "AUTHOR": "Test Dev", "DATE": "2026-01-01", "INITIAL_VALUE": "5"},
    )
    assert "MyCounter" in rendered
    assert "increment" in rendered
    assert "decrement" in rendered
    assert "reset" in rendered


def test_render_counter_invalid_name(template_svc: TemplateService) -> None:
    with pytest.raises(ValueError):
        template_svc.render(
            "counter",
            # CONTRACT_NAME with invalid chars (starts with digit) violates validation_regex
            {"CONTRACT_NAME": "1InvalidName", "AUTHOR": "Dev"},
        )


def test_render_escrow_template(template_svc: TemplateService) -> None:
    rendered = template_svc.render(
        "escrow",
        {"CONTRACT_NAME": "SafeEscrow", "AUTHOR": "Dev"},
    )
    assert "SafeEscrow" in rendered
    assert "confirm_receipt" in rendered
    assert "dispute" in rendered


def test_render_proposal_registry(template_svc: TemplateService) -> None:
    rendered = template_svc.render(
        "proposal_registry",
        {"CONTRACT_NAME": "MyDAO", "AUTHOR": "Dev", "QUORUM_PCT": "60"},
    )
    assert "MyDAO" in rendered
    assert "create_proposal" in rendered
    assert "vote" in rendered
    assert "execute" in rendered


def test_render_contribution_receipt(template_svc: TemplateService) -> None:
    rendered = template_svc.render(
        "contribution_receipt",
        {"CONTRACT_NAME": "Receipts", "AUTHOR": "Dev"},
    )
    assert "record_receipt" in rendered
    assert "contributor_total" in rendered


def test_render_role_based_access(template_svc: TemplateService) -> None:
    rendered = template_svc.render(
        "role_based_access",
        {"CONTRACT_NAME": "MyRBAC", "AUTHOR": "Dev"},
    )
    assert "grant_role" in rendered
    assert "revoke_role" in rendered
    assert "has_role" in rendered


def test_render_safe_arithmetic(template_svc: TemplateService) -> None:
    rendered = template_svc.render(
        "safe_arithmetic",
        {"CONTRACT_NAME": "SafeMath", "AUTHOR": "Dev"},
    )
    assert "safe_add" in rendered
    assert "safe_sub" in rendered
    assert "overflow" in rendered.lower()


def test_render_da_commitment_registry(template_svc: TemplateService) -> None:
    rendered = template_svc.render(
        "da_commitment_registry",
        {"CONTRACT_NAME": "DAReg", "AUTHOR": "Dev", "NAMESPACE": "my-ns"},
    )
    assert "register" in rendered
    assert "exists" in rendered
    assert "update_uri" in rendered


def test_filter_by_category(template_svc: TemplateService) -> None:
    dao_templates = template_svc.list_templates(category="DAO / Governance")
    assert all(t.category == "DAO / Governance" for t in dao_templates)
    assert len(dao_templates) >= 3


def test_search_by_query(template_svc: TemplateService) -> None:
    results = template_svc.list_templates(query="counter")
    assert any(t.id == "counter" for t in results)


def test_template_has_required_metadata(template_svc: TemplateService) -> None:
    for tpl in template_svc.list_templates():
        assert tpl.id, f"Template missing id: {tpl}"
        assert tpl.name, f"Template missing name: {tpl}"
        assert tpl.category, f"Template missing category: {tpl}"
        assert tpl.description, f"Template missing description: {tpl}"
        assert tpl.content_path is not None, f"Template missing content_path: {tpl.id}"
        assert tpl.content_path.exists(), f"Template content file missing: {tpl.content_path}"


# ---------------------------------------------------------------------------
# Token templates — vesting and faucet
# ---------------------------------------------------------------------------


@pytest.fixture()
def token_svc() -> TokenTemplateService:
    return TokenTemplateService()


def test_token_templates_include_vesting(token_svc: TokenTemplateService) -> None:
    ids = {t.id for t in token_svc.list_templates()}
    assert "vesting" in ids


def test_token_templates_include_faucet(token_svc: TokenTemplateService) -> None:
    ids = {t.id for t in token_svc.list_templates()}
    assert "faucet" in ids


def test_render_vesting_template(token_svc: TokenTemplateService, tmp_path: Path) -> None:
    rendered = token_svc.render(
        "vesting",
        {
            "NAME": "MyVest",
            "SYMBOL": "VEST",
            "TOTAL_SUPPLY": "1000000",
            "BENEFICIARY": "beneficiary_addr",
            "CLIFF_BLOCKS": "100",
            "DURATION_BLOCKS": "1000",
        },
    )
    assert "contract.py" in rendered
    assert "manifest.json" in rendered
    assert "releasable" in rendered["contract.py"]
    assert "release" in rendered["contract.py"]


def test_render_faucet_template(token_svc: TokenTemplateService, tmp_path: Path) -> None:
    rendered = token_svc.render(
        "faucet",
        {
            "NAME": "DevFaucet",
            "SYMBOL": "FAUCET",
            "TOTAL_SUPPLY": "10000000",
            "DRIP_AMOUNT": "100",
            "COOLDOWN_BLOCKS": "10",
        },
    )
    assert "contract.py" in rendered
    assert "claim" in rendered["contract.py"]
    assert "cooldown" in rendered["contract.py"]


def test_vesting_invalid_cliff_blocks(token_svc: TokenTemplateService) -> None:
    with pytest.raises(ValueError):
        token_svc.render(
            "vesting",
            {
                "NAME": "X",
                "SYMBOL": "XX",
                "TOTAL_SUPPLY": "1000",
                "BENEFICIARY": "addr",
                "CLIFF_BLOCKS": "-5",
                "DURATION_BLOCKS": "1000",
            },
        )


def test_faucet_invalid_drip_amount(token_svc: TokenTemplateService) -> None:
    with pytest.raises(ValueError):
        token_svc.render(
            "faucet",
            {
                "NAME": "Faucet",
                "SYMBOL": "FC",
                "TOTAL_SUPPLY": "1000",
                "DRIP_AMOUNT": "-1",
                "COOLDOWN_BLOCKS": "10",
            },
        )


def test_vesting_write_to_project(token_svc: TokenTemplateService, tmp_path: Path) -> None:
    rendered = token_svc.render(
        "vesting",
        {
            "NAME": "TestVest",
            "SYMBOL": "TVEST",
            "TOTAL_SUPPLY": "500000",
            "BENEFICIARY": "addr",
            "CLIFF_BLOCKS": "50",
            "DURATION_BLOCKS": "500",
        },
    )
    out = tmp_path / "vesting_project"
    written = token_svc.write_to_project(rendered, out)
    assert (out / "contract.py") in written
    assert (out / "README.md").exists()
