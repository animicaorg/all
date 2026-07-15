"""Configuration for Animica Animal. All secrets + posture come from the environment; the defaults
are safe (dry-run on, live-post off, conservative cadence)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _b(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class AnimalConfig:
    mkt_url: str
    internal_token: str
    gateway_url: str
    dry_run: bool
    allow_live_post: bool
    interval_secs: int
    per_platform_daily_cap: int
    global_daily_cap: int
    media_timeout_secs: int
    state_dir: str
    persona_name: str = "Animica Animal"
    discord_invite: str = "https://discord.gg/vQHJc2jWUJ"
    site_url: str = "https://animica.dev"
    nonkyc_url: str = "https://nonkyc.io/market/ANM_USDT"

    @property
    def media_jobs_url(self) -> str:
        return f"{self.mkt_url}/api/mkt/v1/media/jobs"

    @property
    def internal_base(self) -> str:
        return f"{self.mkt_url}/api/mkt/v1/animal/internal"

    def db_path(self) -> str:
        return os.path.join(self.state_dir, "animal.db")

    def preview_dir(self) -> str:
        d = os.path.join(self.state_dir, "previews")
        os.makedirs(d, exist_ok=True)
        return d


def load() -> AnimalConfig:
    state_dir = os.environ.get("ANIMAL_STATE_DIR", os.path.expanduser("~/.animica/animal"))
    os.makedirs(state_dir, exist_ok=True)
    return AnimalConfig(
        mkt_url=os.environ.get("ANIMAL_MKT_URL", "http://127.0.0.1:4950").rstrip("/"),
        internal_token=os.environ.get("ANIMAL_INTERNAL_TOKEN", ""),
        gateway_url=os.environ.get(
            "ANIMAL_GATEWAY_URL", os.environ.get("ANIMICA_FREE_V1", "http://127.0.0.1:4600/v1")
        ).rstrip("/"),
        dry_run=_b("ANIMAL_DRY_RUN", True),
        allow_live_post=_b("ANIMAL_ALLOW_LIVE_POST", False),
        interval_secs=_i("ANIMAL_INTERVAL_SECS", 1800),
        per_platform_daily_cap=_i("ANIMAL_PLATFORM_DAILY_CAP", 6),
        global_daily_cap=_i("ANIMAL_GLOBAL_DAILY_CAP", 24),
        media_timeout_secs=_i("ANIMAL_MEDIA_TIMEOUT_SECS", 900),
        state_dir=state_dir,
    )
