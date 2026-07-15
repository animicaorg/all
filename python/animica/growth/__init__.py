"""Animica Growth Engine (8.1.0).

Autonomous ecosystem intelligence + marketing that stays a *feature*, not a spam cannon.
The engine reads REAL data (chain RPC, marketplace, pool, free-AI usage, NonKYC market),
turns it into honest insights, and DRAFTS marketing content — then:

  * autonomous on OWNED properties (animica.dev, .anm sites),
  * draft-and-human-approve for anything that speaks to an external platform,
  * consent-only email: a double-opt-in newsletter whose sender pulls recipients ONLY from
    the confirmed-subscribers store (no caller-supplied list exists anywhere), always injects
    one-click unsubscribe, honors an append-only suppression list, and is rate/volume-capped.

Load-bearing invariants (enforced as code in guardrails.py, not as toggles):
  NO scraping/harvesting · NO sending to non-consented addresses · NO fabricated metrics ·
  NO astroturfing / fake accounts / mass-DM · NO CAPTCHA/anti-bot evasion · dry-run by default.
"""

from __future__ import annotations

__version__ = "8.1.0"

# Invariants surfaced as constants so they are greppable and self-documenting.
INVARIANTS = (
    "consent-only-email",
    "no-arbitrary-recipient-list",
    "append-only-suppression",
    "owned-autonomy-only",
    "external-needs-human-approval",
    "no-fabricated-metrics",
    "dry-run-default",
)

__all__ = ["__version__", "INVARIANTS"]
