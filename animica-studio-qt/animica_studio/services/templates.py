"""Compatibility shim re-exporting :class:`TemplatesService`.

The canonical implementation lives in :mod:`templates_service` (matching the
CONTRACTS module map). This module exists so ``services.templates`` also
resolves to the same class.
"""
from __future__ import annotations

from .templates_service import TemplatesService

__all__ = ["TemplatesService"]
