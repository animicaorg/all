"""TemplatesService enumeration + materialization tests (no network/display)."""
from __future__ import annotations

from pathlib import Path

from animica_studio.services.templates_service import TemplatesService

EXPECTED_IDS = {
    "python_cli",
    "fastapi",
    "qt_app",
    "react",
    "node_express",
    "static_site",
    "animica_agent",
    "miner_dashboard",
    "wallet_app",
}


def test_all_templates_listed_and_available() -> None:
    svc = TemplatesService()
    listed = svc.list_templates()
    ids = {t["id"] for t in listed}
    assert EXPECTED_IDS.issubset(ids)
    # Every catalog template must have real files on disk (no fallback needed).
    for tpl in listed:
        if tpl["id"] in EXPECTED_IDS:
            assert tpl["available"] is True, f"{tpl['id']} template missing files"


def test_create_python_cli(tmp_path: Path) -> None:
    svc = TemplatesService()
    root = Path(svc.create("python_cli", str(tmp_path), "My Tool"))
    assert root.is_dir()
    main = root / "main.py"
    assert main.is_file()
    text = main.read_text(encoding="utf-8")
    # {{name}} substitution applied; no raw placeholders left.
    assert "{{name}}" not in text
    assert "My Tool" in text
    assert (root / "pyproject.toml").is_file()


def test_create_static_site(tmp_path: Path) -> None:
    svc = TemplatesService()
    root = Path(svc.create("static_site", str(tmp_path), "Landing"))
    for rel in ("index.html", "style.css", "app.js"):
        assert (root / rel).is_file()
    assert "{{name}}" not in (root / "index.html").read_text(encoding="utf-8")


def test_create_miner_dashboard_targets_pool(tmp_path: Path) -> None:
    svc = TemplatesService()
    root = Path(svc.create("miner_dashboard", str(tmp_path), "Pool Stats"))
    app_js = (root / "app.js").read_text(encoding="utf-8")
    assert "pool.animica.org/api/mining/status" in app_js
