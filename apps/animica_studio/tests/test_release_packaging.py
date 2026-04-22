from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from animica_studio.release_packaging import (  # noqa: E402
    APP_BUNDLE_NAME,
    LINUX_PACKAGE_NAME,
    linux_desktop_entry,
    normalize_release_version,
)


def test_normalize_release_version_strips_leading_v() -> None:
    assert normalize_release_version("v1.2.3", "0.1.0") == "1.2.3"


def test_normalize_release_version_wraps_hash_builds() -> None:
    assert normalize_release_version("c76ab286e-dirty", "0.1.0") == "0.1.0+c76ab286e.dirty"


def test_linux_desktop_entry_points_to_installed_wrapper() -> None:
    entry = linux_desktop_entry()

    assert f"Exec={LINUX_PACKAGE_NAME} %U" in entry
    assert f"TryExec={LINUX_PACKAGE_NAME}" in entry
    assert f"Icon={LINUX_PACKAGE_NAME}" in entry
    assert f"StartupWMClass={APP_BUNDLE_NAME}" in entry


def test_pyinstaller_spec_includes_requests_hiddenimports() -> None:
    spec_path = Path(__file__).resolve().parent.parent / "scripts" / "pyinstaller.spec"
    spec_text = spec_path.read_text(encoding="utf-8")
    assert '"requests"' in spec_text
    assert '"requests.exceptions"' in spec_text


def _load_package_release_module():
    module_path = Path(__file__).resolve().parent.parent / "scripts" / "package_release.py"
    spec = importlib.util.spec_from_file_location("test_package_release", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ensure_monaco_assets_skips_when_loader_exists(monkeypatch, tmp_path) -> None:
    module = _load_package_release_module()
    loader = tmp_path / "monaco" / "vs" / "loader.js"
    loader.parent.mkdir(parents=True)
    loader.write_text("// ready", encoding="utf-8")
    monkeypatch.setattr(module, "MONACO_LOADER_PATH", loader)
    calls: list[list[str]] = []

    def _fake_run(cmd, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))

    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    module.ensure_monaco_assets_installed()

    assert calls == []


def test_ensure_monaco_assets_invokes_setup_when_missing(monkeypatch, tmp_path) -> None:
    module = _load_package_release_module()
    loader = tmp_path / "monaco" / "vs" / "loader.js"
    monkeypatch.setattr(module, "MONACO_LOADER_PATH", loader)
    calls: list[list[str]] = []

    def _fake_run(cmd, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        loader.parent.mkdir(parents=True, exist_ok=True)
        loader.write_text("// installed", encoding="utf-8")

    monkeypatch.setattr(module.subprocess, "run", _fake_run)
    module.ensure_monaco_assets_installed()

    assert calls
    assert "setup_monaco.py" in " ".join(calls[0])
    assert loader.exists()


def test_ensure_monaco_assets_raises_when_setup_fails(monkeypatch, tmp_path) -> None:
    module = _load_package_release_module()
    loader = tmp_path / "monaco" / "vs" / "loader.js"
    monkeypatch.setattr(module, "MONACO_LOADER_PATH", loader)

    def _fake_run(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise module.subprocess.CalledProcessError(1, "setup_monaco.py")

    monkeypatch.setattr(module.subprocess, "run", _fake_run)

    with pytest.raises(SystemExit, match="Failed to prepare Monaco assets"):
        module.ensure_monaco_assets_installed()
