"""Curated, license-safe dataset source provider registry.

This module exposes a :data:`PROVIDER_REGISTRY` mapping source names to
:class:`~animica_studio.services.dataset_bootstrap_service.SourceProvider` instances.

Only sources that are:
- clearly redistributable / usable for training (license-safe)
- obtainable via stable endpoints (dumps / APIs)
- versioned or snapshot-able
- not personal data

are included by default.  Optional (off-by-default) sources must be
explicitly enabled via the ``source_settings`` config dict.

Usage::

    from animica_studio.ena_ml.dataset.sources import PROVIDER_REGISTRY, get_provider

    provider = get_provider("wikipedia")
    version, candidates = provider.resolve_latest()
"""

from __future__ import annotations

# Import directly to avoid triggering ena_ml/__init__.py which imports torch.
from animica_studio.services.dataset_bootstrap_service import (
    ArxivApiProvider,
    GutenbergProvider,
    SourceProvider,
    VettedReposProvider,
    WikipediaAbstractsProvider,
)

# ---------------------------------------------------------------------------
# Default-enabled (text, license-safe, public-domain / CC / open)
# ---------------------------------------------------------------------------

_DEFAULT_PROVIDERS: list[SourceProvider] = [
    WikipediaAbstractsProvider(),   # CC BY-SA; Wikipedia dump abstracts
    ArxivApiProvider(),             # arXiv metadata/abstracts (open access)
    GutenbergProvider(),            # Project Gutenberg - public-domain texts only
    VettedReposProvider(),          # Curated open-licensed documentation sets
]

# ---------------------------------------------------------------------------
# Optional / off-by-default providers
# Activate by setting  source_settings["optional_sources"] = ["wikisource", ...]
# ---------------------------------------------------------------------------
#
# Wikisource public-domain texts and Common Crawl derived datasets are
# intentionally left as opt-in:
#   "wikisource" - requires custom pipeline; enable via source_settings
#   "cc_news"    - requires compliance pipeline; enable via source_settings
#
# Each entry must include:
#   name, license, retrieval_method, version, allow_auto_download
#
OPTIONAL_SOURCE_METADATA: list[dict[str, object]] = [
    {
        "name": "wikisource",
        "license": "CC BY-SA / public domain (varies by text)",
        "retrieval_method": "Wikisource XML dumps",
        "version": "snapshot",
        "allow_auto_download": False,
        "note": "Requires custom pipeline; enable manually after reviewing license.",
    },
    {
        "name": "cc_news",
        "license": "Common Crawl Terms of Use (check per-document)",
        "retrieval_method": "Common Crawl WARC / WET files",
        "version": "snapshot",
        "allow_auto_download": False,
        "note": "Only use if you have a compliant pipeline and clear licensing policy.",
    },
]

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: Maps ``source_name`` to provider instance for all default-enabled sources.
PROVIDER_REGISTRY: dict[str, SourceProvider] = {
    p.source_name: p for p in _DEFAULT_PROVIDERS
}


def get_provider(name: str) -> SourceProvider:
    """Return the provider for *name*, raising :class:`KeyError` if unknown.

    Parameters
    ----------
    name:
        One of the keys in :data:`PROVIDER_REGISTRY`
        (``"wikipedia"``, ``"arxiv"``, ``"gutenberg"``, ``"vetted_repos"``).
    """
    try:
        return PROVIDER_REGISTRY[name]
    except KeyError:
        available = ", ".join(sorted(PROVIDER_REGISTRY))
        raise KeyError(
            f"Unknown source provider {name!r}. Available: {available}"
        ) from None


def list_providers() -> list[dict[str, object]]:
    """Return a list of metadata dicts for all default-enabled providers."""
    return [
        {
            "name": p.source_name,
            "version": p.source_version,
            "enabled": True,
            "allow_auto_download": True,
        }
        for p in _DEFAULT_PROVIDERS
    ]


__all__ = [
    "PROVIDER_REGISTRY",
    "OPTIONAL_SOURCE_METADATA",
    "get_provider",
    "list_providers",
]
