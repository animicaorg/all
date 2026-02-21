from __future__ import annotations

from pathlib import Path

from animica_studio.services.ena_service import EnaFileEdit, _make_unified_diff, _sha256_text, apply_edit_atomic
from animica_studio.services.template_service import TemplateService


def test_template_service_loads_and_renders() -> None:
    svc = TemplateService()
    svc.load_builtin_templates()
    templates = svc.list_templates(query="hello")
    assert templates
    rendered = svc.render("hello_animica_vm", {"PROJECT_NAME": "ProjA", "AUTHOR": "Ana", "DATE": "2026-01-01"})
    assert "ProjA" in rendered


def test_template_validation_blocks_invalid() -> None:
    svc = TemplateService()
    svc.load_builtin_templates()
    try:
        svc.render("hello_animica_vm", {"PROJECT_NAME": "bad space", "AUTHOR": "Ana", "DATE": "2026-01-01"})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_apply_edit_atomic_hash_guard(tmp_path: Path) -> None:
    ws = tmp_path
    f = ws / "demo.py"
    original = "print('a')\n"
    f.write_text(original, encoding="utf-8")
    new = "print('a')\nprint('b')\n"
    diff = _make_unified_diff("demo.py", original, new)
    edit = EnaFileEdit(path="demo.py", original_hash=_sha256_text(original), unified_diff=diff)
    apply_edit_atomic(ws, edit)
    assert f.read_text(encoding="utf-8") == new
