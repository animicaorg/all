from pathlib import Path

import pytest

from animica_studio.services.token_template_service import TokenTemplateService


def test_list_templates_contains_required_ids() -> None:
    svc = TokenTemplateService()
    ids = {t.id for t in svc.list_templates()}
    assert {"nft", "ft", "multitoken", "membership", "factory"}.issubset(ids)


def test_render_ft_and_write(tmp_path: Path) -> None:
    svc = TokenTemplateService()
    rendered = svc.render("ft", {"NAME": "Gold", "SYMBOL": "GLD", "DECIMALS": "8", "TOTAL_SUPPLY": "1000"})
    assert "contract.py" in rendered
    assert "manifest.json" in rendered
    assert '"name": "Gold"' in rendered["manifest.json"]

    out = tmp_path / "tokens" / "gold"
    written = svc.write_to_project(rendered, out)
    assert (out / "contract.py") in written
    assert (out / "README.md").exists()


def test_validation_rejects_bad_symbol() -> None:
    svc = TokenTemplateService()
    with pytest.raises(ValueError):
        svc.render("nft", {"NAME": "Bad", "SYMBOL": "bad-symbol"})
