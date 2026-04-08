from __future__ import annotations

import sys
from pathlib import Path

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
