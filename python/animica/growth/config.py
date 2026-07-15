"""Growth Engine configuration — all real endpoints, newsletter/SMTP settings, and safety flags.

Everything is env-driven with safe defaults. The safety-critical defaults are conservative:
dry-run ON, live send OFF, external approval required. Secrets (SMTP password, internal token)
are read from the environment only and never persisted to the store, drafts, or logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass
class GrowthConfig:
    # ---- real data sources (read-only) ----
    rpc_url: str = os.environ.get("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc")
    mkt_url: str = os.environ.get("ANIMICA_MKT_URL", "http://127.0.0.1:4950")
    pool_url: str = os.environ.get("ANIMICA_POOL_URL", "http://127.0.0.1:8550")
    ai_stats_url: str = os.environ.get("ANIMICA_AI_STATS_URL", "https://animica.dev/ai-stats.json")
    nonkyc_ticker: str = os.environ.get("ANIMICA_NONKYC_TICKER", "https://api.nonkyc.io/api/v2/ticker/ANM_USDT")
    nonkyc_market: str = os.environ.get("ANIMICA_NONKYC_MARKET", "https://nonkyc.io/market/ANM_USDT")
    discord_invite: str = os.environ.get("ANIMICA_DISCORD_INVITE", "https://discord.gg/vQHJc2jWUJ")

    # ---- reasoning (free treasury AI gateway; OpenAI-compatible) ----
    gateway_url: str = os.environ.get("ANIMICA_GROWTH_AI_URL", "http://127.0.0.1:4600/v1")
    gateway_model: str = os.environ.get("ANIMICA_GROWTH_MODEL", "animica-chat")

    # ---- newsletter: consent + delivery ----
    internal_token: str = os.environ.get("GROWTH_INTERNAL_TOKEN", "")  # to read confirmed recipients
    smtp_host: str = os.environ.get("GROWTH_SMTP_HOST", "smtp.gmail.com")
    smtp_port: int = _i("GROWTH_SMTP_PORT", 587)
    smtp_user: str = os.environ.get("GROWTH_SMTP_USER", "")
    smtp_pass: str = os.environ.get("GROWTH_SMTP_PASS", "")  # Gmail App Password, never a login pw
    smtp_from: str = os.environ.get("GROWTH_SMTP_FROM", "")
    smtp_from_name: str = os.environ.get("GROWTH_SMTP_FROM_NAME", "Animica")
    smtp_reply_to: str = os.environ.get("GROWTH_SMTP_REPLY_TO", "")
    from_domain: str = os.environ.get("GROWTH_FROM_DOMAIN", "")  # for SPF/DKIM/DMARC alignment
    list_id: str = os.environ.get("GROWTH_LIST_ID", "Animica Newsletter <newsletter.animica.dev>")
    org_postal: str = os.environ.get("GROWTH_ORG_POSTAL_ADDRESS", "")  # CAN-SPAM: required to send

    # ---- volume + pacing (effective cap = min(config, HARD_MAX) in guardrails) ----
    email_per_day: int = _i("GROWTH_EMAIL_DAILY_CAP", 100)
    email_per_hour: int = _i("GROWTH_EMAIL_PER_HOUR", 40)
    email_per_minute: int = _i("GROWTH_EMAIL_PER_MINUTE", 20)
    batch_size: int = _i("GROWTH_EMAIL_BATCH_SIZE", 25)
    min_interval_ms: int = _i("GROWTH_EMAIL_MIN_INTERVAL_MS", 800)
    warmup_schedule: List[int] = field(default_factory=lambda: [
        int(x) for x in os.environ.get("GROWTH_WARMUP_SCHEDULE", "20,40,80,150,300").split(",") if x.strip().isdigit()
    ])
    max_bounce_rate: float = _f("GROWTH_MAX_BOUNCE_RATE", 0.03)
    max_complaint_rate: float = _f("GROWTH_MAX_COMPLAINT_RATE", 0.001)

    # ---- safety flags (conservative defaults) ----
    dry_run: bool = _b("GROWTH_DRY_RUN", True)               # analyze/draft only; nothing leaves
    allow_live_send: bool = _b("GROWTH_ALLOW_LIVE_SEND", False)
    require_approval: bool = True                            # external always needs approval (not disableable)
    financial_strict: bool = True                           # buy/trade content: disclaimer + phrase lint
    owned_channels: List[str] = field(default_factory=lambda:
        os.environ.get("GROWTH_OWNED_CHANNELS", "animica.dev-blog,anm-content,animica.net").split(","))
    publish_daily_cap: int = _i("GROWTH_PUBLISH_DAILY_CAP", 8)

    # ---- paths ----
    state_dir: str = os.environ.get("GROWTH_STATE_DIR", os.path.expanduser("~/.animica/growth"))
    outbox_dir: str = os.environ.get("GROWTH_OUTBOX_DIR", os.path.expanduser("~/.animica/growth/outbox"))

    def db_path(self) -> str:
        os.makedirs(self.state_dir, exist_ok=True)
        return os.path.join(self.state_dir, "growth.db")


def load() -> GrowthConfig:
    return GrowthConfig()
