"""The consent-only newsletter sender — the ONE outbound-email path.

Non-bypassable by construction:
  * recipients come ONLY from confirmed_recipients() (the marketplace CONFIRMED-minus-suppressed
    store); send_campaign() takes NO to=/recipients= parameter — an arbitrary list is unrepresentable.
  * every message is built with a per-recipient one-click unsubscribe + List-Unsubscribe(-Post) +
    postal identity, and assert_can_spam() aborts the build if any is missing.
  * dry-run is the default (writes .eml to the outbox, never touches SMTP); a live send requires
    GROWTH_DRY_RUN=0 AND GROWTH_ALLOW_LIVE_SEND=1 AND a recorded human approver AND a passing doctor.
  * per-(campaign,recipient) dedup + durable hourly/daily caps + warm-up ceiling.
"""

from __future__ import annotations

import json
import os
import smtplib
import sqlite3
import time
import urllib.request
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import List, Optional

from .config import GrowthConfig
from . import guardrails as G
from . import store

PUBLIC_BASE = os.environ.get("GROWTH_PUBLIC_BASE", "https://animica.dev")


# ---- recipient source (internal, consent-gated) ----------------------------------------------
def confirmed_recipients(cfg: GrowthConfig, *, timeout: float = 10.0) -> List[dict]:
    """The ONLY recipient source. Reads CONFIRMED-minus-suppressed from the marketplace internal
    endpoint (Bearer GROWTH_INTERNAL_TOKEN). Fail-closed: no token => no recipients."""
    if not cfg.internal_token:
        raise G.GuardrailError("GROWTH_INTERNAL_TOKEN unset — cannot read the confirmed-subscriber list")
    req = urllib.request.Request(
        f"{cfg.mkt_url}/api/mkt/v1/newsletter/confirmed",
        headers={"Authorization": f"Bearer {cfg.internal_token}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
        d = json.loads(r.read().decode())
    return d.get("recipients", [])


# ---- per-(campaign, recipient) dedup (durable) -----------------------------------------------
def _dedup_conn(cfg: GrowthConfig) -> sqlite3.Connection:
    c = sqlite3.connect(cfg.db_path(), timeout=15)
    c.execute("CREATE TABLE IF NOT EXISTS campaign_sends(campaign TEXT, email TEXT, ts INTEGER, "
              "PRIMARY KEY(campaign,email))")
    return c


def _already_sent(cfg: GrowthConfig, campaign: str, email: str) -> bool:
    with _dedup_conn(cfg) as c:
        return c.execute("SELECT 1 FROM campaign_sends WHERE campaign=? AND email=?",
                         (campaign, email.lower())).fetchone() is not None


def _mark_sent(cfg: GrowthConfig, campaign: str, email: str) -> None:
    with _dedup_conn(cfg) as c:
        c.execute("INSERT OR IGNORE INTO campaign_sends(campaign,email,ts) VALUES(?,?,?)",
                  (campaign, email.lower(), int(time.time())))


# ---- message construction --------------------------------------------------------------------
def _unsub_url(rcpt: dict) -> str:
    import urllib.parse as up
    q = up.urlencode({"u": rcpt["id"], "e": rcpt["email"], "t": rcpt["unsubToken"]})
    return f"{PUBLIC_BASE}/api/mkt/v1/newsletter/unsubscribe?{q}"


def build_message(cfg: GrowthConfig, campaign: dict, rcpt: dict) -> EmailMessage:
    email_addr = G.validate_email(rcpt["email"])
    unsub = _unsub_url(rcpt)
    postal = cfg.org_postal.strip()
    subject = campaign["title"]
    text = campaign["body_text"].replace("{{unsubscribe_url}}", unsub).replace("{{org_postal}}", postal)
    html = campaign["body"].replace("{{unsubscribe_url}}", unsub).replace("{{org_postal}}", postal)

    from_addr = cfg.smtp_from or cfg.smtp_user
    headers = {
        "List-Unsubscribe": f"<{unsub}>, <mailto:{cfg.smtp_reply_to or from_addr}?subject=unsubscribe>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        "List-Id": cfg.list_id,
    }
    # Last gate before a message can exist: CAN-SPAM required elements.
    if campaign.get("meta", {}).get("financial"):
        G.financial_lint(text, strict=cfg.financial_strict)
    G.assert_can_spam(headers, text, postal, from_addr)

    msg = EmailMessage()
    msg["From"] = f"{cfg.smtp_from_name} <{from_addr}>" if cfg.smtp_from_name else from_addr
    msg["To"] = email_addr
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(cfg.from_domain or "animica.dev"))
    if cfg.smtp_reply_to:
        msg["Reply-To"] = cfg.smtp_reply_to
    msg["Feedback-ID"] = f"{campaign.get('meta',{}).get('campaign_slug','campaign')}:animica:growth"
    for k, v in headers.items():
        msg[k] = v
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


# ---- transports ------------------------------------------------------------------------------
class OutboxTransport:
    """Default (dry-run) transport: writes each message as an .eml file — never touches the network."""
    def __init__(self, cfg: GrowthConfig):
        self.dir = os.path.join(cfg.outbox_dir, "sent-dryrun")
        os.makedirs(self.dir, exist_ok=True)

    def send(self, msg: EmailMessage) -> None:
        name = f"{int(time.time()*1000)}-{abs(hash(msg['To']))%10000}.eml"
        with open(os.path.join(self.dir, name), "wb") as f:
            f.write(bytes(msg))


class SmtpTransport:
    """Live SMTP (STARTTLS). Only constructed on an authorized live send."""
    def __init__(self, cfg: GrowthConfig):
        if not (cfg.smtp_host and cfg.smtp_user and cfg.smtp_pass):
            raise G.GuardrailError("SMTP host/user/pass not configured")
        self.cfg = cfg
        self._smtp = smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30)
        self._smtp.starttls()
        self._smtp.login(cfg.smtp_user, cfg.smtp_pass)

    def send(self, msg: EmailMessage) -> None:
        self._smtp.send_message(msg)

    def close(self) -> None:
        try:
            self._smtp.quit()
        except Exception:
            pass


# ---- the single send path --------------------------------------------------------------------
def send_campaign(cfg: GrowthConfig, campaign: dict, *, approver: Optional[str] = None,
                  limit: Optional[int] = None, log=print) -> dict:
    slug = campaign.get("meta", {}).get("campaign_slug") or campaign["content_hash"]
    live = (not cfg.dry_run) and cfg.allow_live_send

    if live:
        G.assert_can_live_send(cfg)
        appr = store.get_approval(cfg, campaign["content_hash"])
        if not appr:
            raise G.GuardrailError(
                "live send needs an approved campaign — run `animica growth newsletter approve` first")
        G.assert_external_approved(appr, campaign["content_hash"])  # human-recorded, hash-bound

    recipients = confirmed_recipients(cfg)
    if not recipients:
        return {"ok": True, "sent": 0, "note": "no confirmed subscribers yet", "dry_run": not live}

    # Volume ceilings (config can only lower; HARD_MAX caps it).
    cap = G.effective_daily_cap(cfg.email_per_day)
    warmup_ceiling = cfg.warmup_schedule[0] if cfg.warmup_schedule else cap
    remaining_day = max(0, min(cap, warmup_ceiling) - store.sent_today(cfg))
    remaining_hour = max(0, cfg.email_per_hour - store.sent_this_hour(cfg))
    budget = min(len(recipients), remaining_day, remaining_hour, limit or len(recipients))

    transport = OutboxTransport(cfg) if not live else SmtpTransport(cfg)
    sent, skipped = 0, 0
    try:
        for rcpt in recipients:
            if sent >= budget:
                break
            email_addr = rcpt.get("email", "")
            try:
                G.validate_email(email_addr)
            except G.GuardrailError:
                skipped += 1
                continue
            if _already_sent(cfg, slug, email_addr):
                skipped += 1
                continue
            msg = build_message(cfg, campaign, rcpt)
            transport.send(msg)
            _mark_sent(cfg, slug, email_addr)
            store.bump_send_counter(cfg, 1)
            sent += 1
            if live and cfg.min_interval_ms:
                time.sleep(cfg.min_interval_ms / 1000.0)
    finally:
        if isinstance(transport, SmtpTransport):
            transport.close()

    mode = "LIVE" if live else "DRY-RUN (outbox)"
    log(f"[{mode}] campaign {slug}: sent {sent}, skipped {skipped}, "
        f"of {len(recipients)} confirmed (budget {budget}, day-remaining {remaining_day})")
    return {"ok": True, "sent": sent, "skipped": skipped, "recipients": len(recipients),
            "dry_run": not live, "campaign": slug}


# ---- transactional confirm-email flush (from the web outbox) ----------------------------------
def flush_outbox(cfg: GrowthConfig, *, log=print) -> dict:
    """Deliver the double-opt-in confirm emails the web app enqueued. Transactional (exempt from the
    marketing linter) but still gated: in dry-run nothing is sent, just reported."""
    src = cfg.outbox_dir
    if not os.path.isdir(src):
        return {"ok": True, "pending": 0, "sent": 0, "note": "no outbox"}
    files = [f for f in sorted(os.listdir(src)) if f.endswith(".json")]
    if not files:
        return {"ok": True, "pending": 0, "sent": 0}
    live = (not cfg.dry_run) and cfg.allow_live_send
    if not live:
        return {"ok": True, "pending": len(files), "sent": 0, "note": "dry-run — set GROWTH_DRY_RUN=0 + GROWTH_ALLOW_LIVE_SEND=1 to deliver"}
    G.assert_can_live_send(cfg)
    transport = SmtpTransport(cfg)
    sent = 0
    done_dir = os.path.join(src, "sent")
    os.makedirs(done_dir, exist_ok=True)
    try:
        for fn in files:
            p = os.path.join(src, fn)
            try:
                env = json.loads(open(p).read())
                to = G.validate_email(env["to"])
                msg = EmailMessage()
                msg["From"] = f"{cfg.smtp_from_name} <{cfg.smtp_from or cfg.smtp_user}>"
                msg["To"] = to
                msg["Subject"] = env["subject"]
                msg["Date"] = formatdate(localtime=True)
                msg["Message-ID"] = make_msgid(domain=(cfg.from_domain or "animica.dev"))
                for k, v in (env.get("headers") or {}).items():
                    msg[k] = v
                msg.set_content(env.get("text", ""))
                if env.get("html"):
                    msg.add_alternative(env["html"], subtype="html")
                transport.send(msg)
                os.replace(p, os.path.join(done_dir, fn))
                sent += 1
            except Exception as e:
                log(f"  confirm-email {fn} failed: {e}")
    finally:
        transport.close()
    log(f"flushed {sent} confirm email(s)")
    return {"ok": True, "pending": len(files), "sent": sent}
