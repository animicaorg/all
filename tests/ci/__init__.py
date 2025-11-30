# -*- coding: utf-8 -*-
"""
tests.ci
--------
Lightweight package marker and tiny helpers for CI-aware tests and diagnostics.

Exports:
- CI_ENABLED: bool flag set when running in common CI providers.
- env_snapshot(): minimal environment snapshot useful to attach to logs/reports.
"""
from __future__ import annotations

import os
import platform
import sys
from typing import Dict

# Detect popular CI environments
CI_ENABLED: bool = any(
    os.getenv(var)
    for var in (
        "CI",
        "GITHUB_ACTIONS",
        "BUILDKITE",
        "GITLAB_CI",
        "CIRCLECI",
        "TEAMCITY_VERSION",
    )
)


def env_snapshot() -> Dict[str, str]:
    """
    Return a minimal, non-sensitive environment snapshot for debugging in CI logs.
    Safe to call locally. Intentionally small and generic.
    """
    return {
        "python": sys.version.split()[0],
        "implementation": sys.implementation.name,  # e.g., 'cpython'
        "platform": platform.platform(),
        "ci": "true" if CI_ENABLED else "false",
    }


__all__ = ["CI_ENABLED", "env_snapshot"]
