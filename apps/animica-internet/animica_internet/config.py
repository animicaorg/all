"""
Runtime configuration for the Animica Internet desktop app.

Mirrors the marketplace constants (apps/animica-marketplace/lib/config.ts, lib/storage.ts,
lib/ans.ts) so the native client resolves, verifies and pays exactly like the gateway.
"""

from __future__ import annotations

import os

APP_NAME = "Animica Internet"
APP_ID = "org.animica.internet"

# The .anm registry / gateway. Overridable for testnets or a self-hosted registry.
GATEWAY = os.environ.get("ANIMICA_INTERNET_GATEWAY", "https://animica.dev").rstrip("/")
API_BASE = GATEWAY + "/api/mkt/v1"

# Node JSON-RPC for wallet balance/broadcast (matches the wallet extension default).
RPC_URL = os.environ.get("ANIMICA_RPC_URL", "https://rpc.animica.org/rpc")

# Custom URL scheme the browser is addressed in. The app is .anm-ONLY: it never navigates
# arbitrary http(s) URLs typed into the bar — only <name>.anm / anm://<name>/<path>.
ANM_SCHEME = "anm"

# Content addressing (lib/storage.ts): CID = "anm1c" + sha3_256(bytes) hex, 2 MB per object.
CID_PREFIX = "anm1c"
MAX_CONTENT_BYTES = 2 * 1024 * 1024

# Signature domain (lib/config.ts SIGN_MESSAGE_DOMAIN) — load-bearing for every verifier.
SIGN_MESSAGE_DOMAIN = "animica:signMessage:"
ML_DSA_65_ALG_ID = 0x1003
NANM_PER_ANM = 1_000_000_000

# Name rules (lib/ans.ts): 2-63 chars, [a-z0-9] with internal hyphens, no leading/trailing/`--`.
NAME_RE = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
RESERVED_NAMES = {"anm", "www", "root", "admin", "animica", "ans", "registry", "node", "localhost"}

# Registration fee per YEAR by name length (lib/ans.ts registrationFeeNanm), in whole ANM.
def registration_fee_anm(name: str, years: int = 1) -> int:
    years = max(1, min(10, int(years)))
    n = len(name)
    if n <= 3:
        per = 500
    elif n <= 5:
        per = 100
    elif n <= 8:
        per = 25
    else:
        per = 5
    return per * years


# Where in-app name-reservation / renewal ANM is routed. The Animica Foundation address.
FOUNDATION_ADDRESS = os.environ.get(
    "ANIMICA_FOUNDATION_ADDRESS",
    "anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga",
)

# The name this app itself is reachable at inside the Animica Internet (animica.dev mirror).
HOME_NAME = os.environ.get("ANIMICA_INTERNET_HOME", "develop")


def app_state_dir() -> str:
    base = os.environ.get("ANIMICA_INTERNET_STATE")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".animica", "internet")
    os.makedirs(base, mode=0o700, exist_ok=True)
    return base
