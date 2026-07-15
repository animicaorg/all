"""Animica Animal — the autonomous mascot ambassador.

A friendly always-running agent that produces original content for the Animica project and posts it
to social accounts the OPERATOR OWNS (connected via each platform's official OAuth in the console).
It never creates accounts, never scrapes, never targets individuals, and never fabricates metrics.
Live posting is double-gated (dry-run off AND live-post allowed); the default posture renders
previews only.
"""

from __future__ import annotations

__version__ = "8.2.0"

# Non-bypassable behavioural invariants (documented here, enforced in guardrails.py + engine.py).
INVARIANTS = (
    "posts only to accounts connected in the operator console (owned, official-API)",
    "never creates/registers a social account",
    "dry-run by default; live posting needs ANIMAL_DRY_RUN=0 AND ANIMAL_ALLOW_LIVE_POST=1",
    "honors the operator pause kill-switch every cycle",
    "per-platform daily post cap; no spam bursts",
    "financial content carries the risk disclaimer and never pumps price",
    "numbers come from real sources or are omitted — never fabricated",
)
