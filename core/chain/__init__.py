"""
Animica core.chain
==================

A small public façade over the chain orchestration primitives:

- state_root: canonical state-root calculation from key/value views
- fork_choice: deterministic fork-choice helpers
- block_import: decode/sanity-check, link, persist, update head
- head: read/write canonical head pointers

This module re-exports the most commonly used call points lazily, so importing
`core.chain` is cheap and does not pull heavy dependencies until a symbol is
actually referenced.

Typical usage
-------------
    from core.chain import (
        compute_state_root,
        ForkChoiceParams, fork_choice,
        BlockImporter, ImportResult,
        get_head, set_head,
    )
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, List

__all__ = [
    # state_root
    "compute_state_root",
    "compute_state_root_from_items",
    # fork_choice
    "ForkChoiceParams",
    "fork_choice",
    "compare_weight",
    # block_import
    "BlockImporter",
    "ImportResult",
    # head
    "HeadInfo",
    "get_head",
    "set_head",
    "read_head",
    "write_head",
]

# Map exported names → (module, attribute) for lazy loading.
_LAZY_MAP: Dict[str, tuple[str, str]] = {
    # state_root
    "compute_state_root": ("core.chain.state_root", "compute_state_root"),
    "compute_state_root_from_items": ("core.chain.state_root", "compute_state_root_from_items"),
    # fork_choice
    "ForkChoiceParams": ("core.chain.fork_choice", "ForkChoiceParams"),
    "fork_choice": ("core.chain.fork_choice", "fork_choice"),
    "compare_weight": ("core.chain.fork_choice", "compare_weight"),
    # block_import
    "BlockImporter": ("core.chain.block_import", "BlockImporter"),
    "ImportResult": ("core.chain.block_import", "ImportResult"),
    # head
    "HeadInfo": ("core.chain.head", "HeadInfo"),
    "get_head": ("core.chain.head", "get_head"),
    "set_head": ("core.chain.head", "set_head"),
    "read_head": ("core.chain.head", "read_head"),
    "write_head": ("core.chain.head", "write_head"),
}


def __getattr__(name: str) -> Any:  # pragma: no cover - trivial lazy loader
    try:
        mod_name, attr = _LAZY_MAP[name]
    except KeyError as e:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from e
    module = importlib.import_module(mod_name)
    obj = getattr(module, attr)
    globals()[name] = obj  # cache for future lookups
    return obj


def __dir__() -> List[str]:  # pragma: no cover
    return sorted(list(globals().keys()) + list(__all__))
