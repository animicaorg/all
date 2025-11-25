# -*- coding: utf-8 -*-
"""
{{project_slug}}.contracts
--------------------------

Package marker for on-chain contracts bundled with this project.

Goals:
- Provide a stable import path for tooling (builders, linters, tests).
- Stay side-effect free (no I/O, no network, no VM imports) to keep
  `import {{project_slug}}.contracts` safe in any environment.
- Document layout conventions for additional contracts.

Layout conventions
- Primary contract source: ./contract.py
- Primary manifest/ABI:    ./manifest.json
- You may add more modules (e.g., token.py, escrow.py) beside contract.py.
  Keep each module self-contained and deterministic per VM subset rules.

Tip
- Tools typically discover files relative to this package directory,
  so avoid runtime logic in this file.

"""

from __future__ import annotations

# Public surface kept deliberately tiny.
__all__ = ["__version__"]

# Project-local semantic version for the contracts package (edit as you iterate).
__version__ = "0.1.0"
