"""The guardrail spine — imported by every subsystem so the safety invariants cannot be trivially
removed by editing one call site. These are code, not configuration: no env flag disables consent
enforcement, suppression, unsubscribe presence, the financial linter, or the metric-honesty check.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional


class GuardrailError(RuntimeError):
    """A safety invariant was violated. Callers MUST fail closed (never send/publish)."""


# Hard ceilings on outbound email volume. Effective cap = min(config, HARD_MAX). Config can only
# LOWER volume, never raise it above these. (Gmail free ~500/day, Workspace ~2000/day — stay under.)
HARD_MAX_PER_DAY = 450
HARD_MAX_PER_DAY_WORKSPACE = 1800


def effective_daily_cap(config_cap: int, workspace: bool = False) -> int:
    ceiling = HARD_MAX_PER_DAY_WORKSPACE if workspace else HARD_MAX_PER_DAY
    return max(0, min(int(config_cap), ceiling))


# ---- email address hygiene (header-injection + validity) ------------------------------------
_EMAIL_RE = re.compile(r"^[^\s@\"'<>()\[\]\\,;:]+@[^\s@\"'<>()\[\]\\,;:]+\.[^\s@\"'<>()\[\]\\,;:]+$")


def validate_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not (3 <= len(e) <= 254):
        raise GuardrailError("invalid email length")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in e):  # control chars incl. CR/LF
        raise GuardrailError("control characters in email (header-injection guard)")
    if not _EMAIL_RE.match(e):
        raise GuardrailError("email failed RFC5322 validation")
    return e


# ---- CAN-SPAM / bulk-sender required elements ------------------------------------------------
REQUIRED_HEADERS = ("List-Unsubscribe", "List-Unsubscribe-Post", "List-Id")


def assert_can_spam(headers: dict, body_text: str, org_postal: str, from_addr: str) -> None:
    """Every marketing message must be identifiable + one-click unsubscribable + carry a postal
    identity. The renderer cannot emit a body without these — this is the last gate before SMTP."""
    if not from_addr or "@" not in from_addr:
        raise GuardrailError("missing/invalid From identity")
    if not org_postal.strip():
        raise GuardrailError("GROWTH_ORG_POSTAL_ADDRESS is required (CAN-SPAM physical address)")
    for h in REQUIRED_HEADERS:
        if not headers.get(h):
            raise GuardrailError(f"missing required header {h}")
    lu = headers.get("List-Unsubscribe", "")
    if "http" not in lu:
        raise GuardrailError("List-Unsubscribe must include a one-click https URL")
    if headers.get("List-Unsubscribe-Post", "").replace(" ", "") != "List-Unsubscribe=One-Click":
        raise GuardrailError("List-Unsubscribe-Post must be 'List-Unsubscribe=One-Click' (RFC 8058)")
    if "unsubscribe" not in (body_text or "").lower():
        raise GuardrailError("body must contain a visible unsubscribe link/notice")
    if org_postal.strip() not in (body_text or ""):
        raise GuardrailError("body must contain the postal/org identity line")


# ---- financial-promotion overlay -------------------------------------------------------------
FINANCIAL_DISCLAIMER = (
    "Not financial advice. ANM is a volatile digital asset — its value can go to zero and past "
    "performance does not predict future results. Do your own research and only risk what you can "
    "afford to lose. Animica is the project behind ANM and therefore has a material interest in it. "
    "This message is not an offer or solicitation in any jurisdiction where that would be unlawful."
)

# Language that turns a promotion into a pump-and-dump / illegal financial promotion. Hard-fail.
_PROHIBITED = re.compile(
    r"\b(guaranteed|guarantee[ds]?|risk[- ]?free|can'?t lose|sure thing|to the moon|100x|1000x|"
    r"\d{2,}x returns?|get rich|will (?:moon|explode|pump|skyrocket)|"
    r"buy now before|last chance|act fast|limited time only)\b",
    re.I,
)

# "financial/investment advice" is prohibited when *offered* as a claim, but is REQUIRED (negated)
# in our own disclaimer ("Not financial advice"). Only flag it when it is NOT preceded by a negator,
# so the mandatory disclaimer never trips its own linter.
_ADVICE_CLAIM = re.compile(r"(?<!not )(?<!n't )(?<!no )\b(?:financial|investment) advice\b", re.I)


def financial_lint(text: str, *, strict: bool = True) -> None:
    """Reject pump-and-dump phrasing and require the disclaimer in any buy/trade content."""
    hit = _PROHIBITED.search(text or "") or _ADVICE_CLAIM.search(text or "")
    if hit:
        raise GuardrailError(f"prohibited financial-promotion phrase: {hit.group(0)!r}")
    if strict and "Not financial advice" not in (text or ""):
        raise GuardrailError("financial content must include the risk disclaimer + material-interest disclosure")


# ---- owned vs external boundary --------------------------------------------------------------
def assert_owned_channel(channel: str, allowlist: Iterable[str]) -> None:
    if channel not in set(allowlist):
        raise GuardrailError(
            f"channel {channel!r} is not an owned/allowlisted channel — external channels can only "
            f"produce draft artifacts for human approval, never autonomous publish/send")


def assert_external_approved(approval: Optional[dict], content_hash: str) -> None:
    """Anything targeting an external audience needs a human-recorded, content-hash-bound approval."""
    if not approval:
        raise GuardrailError("external artifact requires a human approval record (none found)")
    if approval.get("content_hash") != content_hash:
        raise GuardrailError("approval does not match the current content hash (content changed after approval)")
    if not approval.get("approver"):
        raise GuardrailError("approval has no recorded human approver")


# ---- metric honesty --------------------------------------------------------------------------
def assert_no_fabricated_metrics(claims: Iterable[dict]) -> None:
    """Each rendered numeric claim must carry provenance from a real source with status OK;
    UNAVAILABLE data must render as 'unavailable', never a fabricated number."""
    for c in claims:
        if c.get("status") == "OK" and not c.get("source"):
            raise GuardrailError(f"numeric claim {c.get('key')!r} has no data source (possible fabrication)")


# ---- live-send authorization -----------------------------------------------------------------
def assert_can_live_send(cfg) -> None:
    """A live send needs BOTH the dry-run switch off AND explicit live authorization. No single
    boolean skips both — the default posture (dry_run=True, allow_live_send=False) sends nothing."""
    if cfg.dry_run:
        raise GuardrailError("GROWTH_DRY_RUN is on — refusing to send; set GROWTH_DRY_RUN=0 to go live")
    if not cfg.allow_live_send:
        raise GuardrailError("GROWTH_ALLOW_LIVE_SEND is off — refusing to send")
    if not cfg.org_postal.strip():
        raise GuardrailError("GROWTH_ORG_POSTAL_ADDRESS unset — required before any live send")
