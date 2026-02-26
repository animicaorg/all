"""Tests for the dataset source provider registry.

These tests import from ``animica_studio.ena_ml.dataset.sources``, which
triggers the ``ena_ml`` package initialiser.  That initialiser eagerly
imports ``Trainer``, which requires PyTorch.  Tests that go through the
``ena_ml`` package are therefore skipped when torch is unavailable.

Tests that only need the *services* layer (dataset_bootstrap_service) are
kept separate and always run.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Tests that import directly from services (always run)
# ---------------------------------------------------------------------------


def test_bootstrap_service_providers_exist() -> None:
    """The provider classes must be importable from the services layer."""
    from animica_studio.services.dataset_bootstrap_service import (
        WikipediaAbstractsProvider,
        ArxivApiProvider,
        GutenbergProvider,
        VettedReposProvider,
    )

    for cls in (WikipediaAbstractsProvider, ArxivApiProvider, GutenbergProvider, VettedReposProvider):
        assert hasattr(cls, "source_name")
        assert hasattr(cls, "resolve_latest")
        assert hasattr(cls, "iter_documents")


def test_wikipedia_provider_source_name() -> None:
    from animica_studio.services.dataset_bootstrap_service import WikipediaAbstractsProvider

    assert WikipediaAbstractsProvider.source_name == "wikipedia"


def test_arxiv_provider_source_name() -> None:
    from animica_studio.services.dataset_bootstrap_service import ArxivApiProvider

    assert ArxivApiProvider.source_name == "arxiv"


def test_gutenberg_provider_source_name() -> None:
    from animica_studio.services.dataset_bootstrap_service import GutenbergProvider

    assert GutenbergProvider.source_name == "gutenberg"


def test_vetted_repos_provider_source_name() -> None:
    from animica_studio.services.dataset_bootstrap_service import VettedReposProvider

    assert VettedReposProvider.source_name == "vetted_repos"


def test_providers_resolve_latest_return_tuple() -> None:
    """All provider instances must implement resolve_latest() → (str, list)."""
    from animica_studio.services.dataset_bootstrap_service import (
        WikipediaAbstractsProvider,
        ArxivApiProvider,
        GutenbergProvider,
        VettedReposProvider,
    )

    for cls in (WikipediaAbstractsProvider, ArxivApiProvider, GutenbergProvider, VettedReposProvider):
        provider = cls()
        version, candidates = provider.resolve_latest()
        assert isinstance(version, str), f"{cls.__name__}: version must be str, got {type(version)}"
        assert isinstance(candidates, list), f"{cls.__name__}: candidates must be list"


# ---------------------------------------------------------------------------
# Tests through the ena_ml package path (require torch)
# ---------------------------------------------------------------------------

torch = pytest.importorskip(
    "torch",
    reason="torch not installed; skipping ena_ml package-path tests",
)


def test_provider_registry_is_non_empty() -> None:
    from animica_studio.ena_ml.dataset.sources import PROVIDER_REGISTRY

    assert len(PROVIDER_REGISTRY) >= 4, (
        f"Expected at least 4 providers, got {len(PROVIDER_REGISTRY)}: "
        f"{list(PROVIDER_REGISTRY)}"
    )


def test_provider_registry_has_required_sources() -> None:
    from animica_studio.ena_ml.dataset.sources import PROVIDER_REGISTRY

    required = {"wikipedia", "arxiv", "gutenberg", "vetted_repos"}
    missing = required - set(PROVIDER_REGISTRY)
    assert not missing, f"Missing providers: {missing}"


def test_get_provider_returns_correct_instance() -> None:
    from animica_studio.ena_ml.dataset.sources import get_provider
    from animica_studio.services.dataset_bootstrap_service import WikipediaAbstractsProvider

    provider = get_provider("wikipedia")
    assert isinstance(provider, WikipediaAbstractsProvider)
    assert provider.source_name == "wikipedia"


def test_get_provider_raises_on_unknown() -> None:
    from animica_studio.ena_ml.dataset.sources import get_provider

    with pytest.raises(KeyError, match="unknown_source"):
        get_provider("unknown_source")


def test_get_provider_error_message_lists_available() -> None:
    from animica_studio.ena_ml.dataset.sources import get_provider

    with pytest.raises(KeyError) as exc_info:
        get_provider("nonexistent")
    msg = str(exc_info.value)
    assert "wikipedia" in msg


def test_list_providers_returns_metadata() -> None:
    from animica_studio.ena_ml.dataset.sources import list_providers

    providers = list_providers()
    assert len(providers) >= 4
    for p in providers:
        assert "name" in p
        assert "version" in p
        assert p["enabled"] is True
        assert "allow_auto_download" in p


def test_optional_source_metadata_is_list() -> None:
    from animica_studio.ena_ml.dataset.sources import OPTIONAL_SOURCE_METADATA

    assert isinstance(OPTIONAL_SOURCE_METADATA, list)
    for item in OPTIONAL_SOURCE_METADATA:
        assert "name" in item
        assert "license" in item
        assert item["allow_auto_download"] is False, (
            "Optional sources must require explicit opt-in (allow_auto_download=False)"
        )


def test_all_exports_present() -> None:
    import animica_studio.ena_ml.dataset.sources as sources_mod

    for name in sources_mod.__all__:
        assert hasattr(sources_mod, name), f"__all__ entry {name!r} not found in module"
