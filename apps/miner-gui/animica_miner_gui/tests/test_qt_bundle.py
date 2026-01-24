"""Tests for bundled Qt plugins."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_qcocoa_plugin_present():
    app_bundle = os.environ.get("ANIMICA_GUI_APP_BUNDLE")
    if not app_bundle:
        pytest.skip("ANIMICA_GUI_APP_BUNDLE not set")
    bundle_path = Path(app_bundle)
    assert bundle_path.exists(), f"App bundle missing: {bundle_path}"
    qcocoa = list(bundle_path.rglob("platforms/libqcocoa.dylib"))
    assert qcocoa, "qcocoa platform plugin missing in app bundle"
