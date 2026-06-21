"""Contract alias module for the file-tree panel.

The CONTRACTS module map references ``ui/file_tree_panel.py`` while the concrete
implementation lives in :mod:`animica_studio.ui.file_tree`. This shim re-exports
:class:`FileTreePanel` so either import path resolves to the same class.
"""
from __future__ import annotations

from .file_tree import FileTreePanel

__all__ = ["FileTreePanel"]
