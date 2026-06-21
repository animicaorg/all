#!/usr/bin/env python3
"""Animica Studio Qt entrypoint.

Run with:  python main.py
This thin shim defers to :func:`animica_studio.app.main`.
"""
from __future__ import annotations

import sys


def _run() -> int:
    from animica_studio.app import main

    return main(sys.argv)


if __name__ == "__main__":
    raise SystemExit(_run())
