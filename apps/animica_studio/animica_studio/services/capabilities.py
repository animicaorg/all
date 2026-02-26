from __future__ import annotations

from importlib.util import find_spec


def _has_module(name: str) -> bool:
    return find_spec(name) is not None


def has_pillow() -> bool:
    return _has_module("PIL")


def has_torch() -> bool:
    return _has_module("torch")


def has_psutil() -> bool:
    return _has_module("psutil")

