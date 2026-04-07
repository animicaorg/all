"""Pytest configuration and shared fixtures for animica_studio tests."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_app_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_STUDIO_APP_DATA_DIR", str(tmp_path / "app-data"))
    yield


@pytest.fixture(autouse=True)
def _clear_rpc_discover_cache():
    """Clear global RPC caches before every test.

    The discover cache, resolved-methods cache, and AICF method cache are all
    module-level and shared across test runs, which causes test-ordering failures
    when a test populates the cache and a subsequent test expects a fresh call.
    """

    def _clear():
        try:
            import animica_studio.services.rpc_client as rpc_mod

            with rpc_mod._DISCOVER_CACHE_LOCK:  # noqa: SLF001
                rpc_mod._DISCOVER_CACHE_BY_URL.clear()  # noqa: SLF001
            # Also clear the per-URL resolved-methods cache.
            rpc_mod._RESOLVED_METHODS_BY_URL.clear()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
        try:
            import animica_studio.services.aicf_service as aicf_mod

            with aicf_mod.AicfService._METHOD_CACHE_LOCK:  # noqa: SLF001
                aicf_mod.AicfService._METHOD_CACHE_BY_URL.clear()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass

    _clear()
    yield
    _clear()
