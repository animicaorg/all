"""Configuration + credentials for the Animica Python Cloud SDK/CLI.

Resolution order for every value: explicit argument > environment > credentials file > default.
The credentials file is ``~/.animica/cloud.json`` (mode 0600 — it holds a bearer API key),
sitting next to the wallet store the rest of the CLI already uses (``~/.animica/wallets.json``).

Money: integer nANM, always. Python ints are arbitrary precision so there is no BigInt dance,
but the same law as the platform applies — NEVER floats. ANM strings are parsed exactly with
Decimal, mirroring lib/nanm.ts anmToNanm/nanmToAnm digit-for-digit.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Tuple

from .errors import ConfigError

DEFAULT_BASE_URL = "https://animica.dev"
ENV_BASE_URL = "ANIMICA_CLOUD_URL"
ENV_API_KEY = "ANIMICA_CLOUD_API_KEY"
ENV_VALIDATOR = "ANIMICA_CLOUD_VALIDATOR"

# API keys are marketplace keys — the platform only ever mints this prefix (lib/apikey.ts).
API_KEY_PREFIX = "anm_mkt_"

NANM_PER_ANM = 1_000_000_000  # 1 ANM = 1e9 nANM

# ---------------------------------------------------------------------------
# Platform mirrors. These are the SERVER DEFAULTS from lib/cloud/config.ts, mirrored so the
# SDK can refuse obviously-invalid configs before an HTTP round trip. The server re-validates
# and is ALWAYS authoritative (its values are env-overridable per deployment); nothing here
# grants anything — it only fails faster.
# ---------------------------------------------------------------------------

MAX_SOURCE_BYTES = 256 * 1024
MIN_TIMEOUT_MS = 1_000
MAX_TIMEOUT_MS = 300_000
DEFAULT_TIMEOUT_MS = 30_000
MIN_MEMORY_MB = 64
MAX_MEMORY_MB = 1024
DEFAULT_MEMORY_MB = 256

# The full capability vocabulary (lib/cloud/config.ts CAPABILITIES). A function may only
# request what it declared at deploy time; the sandbox host broker enforces the grant.
CAPABILITIES = (
    "AI_INFERENCE",
    "CALL_FUNCTION",
    "CALL_APP",
    "READ_CHAIN",
    "SPEND_ANM",
    "PERSIST_STATE",
    "SCHEDULE",
    "HTTP_FETCH",
)

# Capabilities that always require an explicit, revocable user grant before first run.
SENSITIVE_CAPABILITIES = ("SPEND_ANM", "CALL_APP", "CALL_FUNCTION", "HTTP_FETCH")


# ---------------------------------------------------------------------------
# Money helpers (exact, no floats)
# ---------------------------------------------------------------------------


def anm_to_nanm(anm: "str | int | Decimal") -> int:
    """Parse a human ANM amount ("1.5") into integer nANM. Exact; rejects >9 decimals."""
    if isinstance(anm, float):  # refuse silently-lossy input rather than round it
        raise ValueError("pass ANM amounts as str/int/Decimal, never float")
    try:
        dec = Decimal(str(anm).strip().replace("_", ""))
    except InvalidOperation as exc:
        raise ValueError(f"invalid ANM amount: {anm!r}") from exc
    if dec < 0:
        raise ValueError(f"ANM amount must be non-negative: {anm!r}")
    base = dec * NANM_PER_ANM
    if base != base.to_integral_value():
        raise ValueError(f"more than 9 decimal places in {anm!r}")
    return int(base)


def nanm_to_anm(nanm: "int | str") -> str:
    """Integer nANM -> exact decimal ANM string (mirrors lib/nanm.ts nanmToAnm)."""
    n = int(nanm)
    neg = n < 0
    n = -n if neg else n
    whole, frac = divmod(n, NANM_PER_ANM)
    frac_str = f"{frac:09d}".rstrip("0")
    body = f"{whole}.{frac_str}" if frac_str else str(whole)
    return f"-{body}" if neg else body


def format_anm(nanm: "int | str") -> str:
    """Display form: thousands separators, up to 4 decimals (mirrors lib/nanm.ts formatAnm)."""
    s = nanm_to_anm(nanm)
    neg = s.startswith("-")
    if neg:
        s = s[1:]
    whole, _, frac = s.partition(".")
    grouped = f"{int(whole):,}"
    short = frac[:4]
    out = f"{grouped}.{short}" if short else grouped
    return f"-{out}" if neg else out


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def credentials_path() -> Path:
    """~/.animica/cloud.json (ANIMICA_HOME honored like the rest of the CLI's dotdir users)."""
    home = os.environ.get("ANIMICA_HOME")
    root = Path(home) if home else Path.home() / ".animica"
    return root / "cloud.json"


def load_credentials() -> Tuple[Optional[str], Optional[str]]:
    """(api_key, base_url) from the credentials file, or (None, None). Never raises on a
    missing/corrupt file — an unreadable store must degrade to 'not logged in', not a crash."""
    path = credentials_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    key = data.get("api_key")
    url = data.get("base_url")
    return (key if isinstance(key, str) and key else None, url if isinstance(url, str) and url else None)


def save_credentials(api_key: str, base_url: Optional[str] = None) -> Path:
    """Write ~/.animica/cloud.json with mode 0600 (and 0700 on the directory).

    Written atomically via rename so a crash mid-write can't leave a truncated key file, and
    opened O_EXCL with 0600 from the first byte — the key is never world-readable, even briefly.
    """
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(stat.S_IRWXU)
    except OSError:
        pass  # e.g. the dir is a mount we don't own; the file mode below still protects the key
    doc: dict = {"api_key": api_key}
    if base_url and base_url != DEFAULT_BASE_URL:
        doc["base_url"] = base_url
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    os.chmod(path, 0o600)
    return path


def delete_credentials() -> bool:
    """Remove the stored key. True if a file was deleted."""
    try:
        credentials_path().unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Resolved config
# ---------------------------------------------------------------------------


@dataclass
class CloudConfig:
    """Resolved SDK settings for one client."""

    base_url: str = DEFAULT_BASE_URL
    api_key: Optional[str] = None
    #: Default HTTP timeout for control-plane calls (list/deploy/logs). Invocations use their
    #: own, longer deadline derived from the function timeout — see CloudClient.invoke().
    timeout_s: int = 30

    @classmethod
    def resolve(
        cls,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_s: Optional[int] = None,
    ) -> "CloudConfig":
        stored_key, stored_url = load_credentials()
        url = base_url or os.environ.get(ENV_BASE_URL) or stored_url or DEFAULT_BASE_URL
        key = api_key or os.environ.get(ENV_API_KEY) or stored_key
        return cls(
            base_url=url.rstrip("/"),
            api_key=key,
            timeout_s=timeout_s if timeout_s is not None else 30,
        )

    def require_key(self) -> str:
        if not self.api_key:
            raise ConfigError(
                "no API key configured — run `animica cloud login`, or set "
                f"{ENV_API_KEY}, or pass api_key= explicitly"
            )
        return self.api_key


# ---------------------------------------------------------------------------
# The real validator (sandbox/validate.py)
# ---------------------------------------------------------------------------


def find_validator() -> Optional[Path]:
    """Locate the platform's pre-deploy validator so `animica cloud validate` runs the EXACT
    artifact the server runs — not a reimplementation that could drift.

    Order: ANIMICA_CLOUD_VALIDATOR env > the marketplace checkout relative to this repo
    (editable installs from /root/animica/python land inside the monorepo) > the production
    absolute path. Returns None when unavailable (e.g. plain pip installs off-box); callers
    fall back to the server-side POST /validate, which runs the same file.
    """
    env = os.environ.get(ENV_VALIDATOR)
    if env:
        p = Path(env)
        if p.is_file():
            return p
    here = Path(__file__).resolve()
    candidates = []
    # <repo>/python/animica/cloud/config.py -> <repo>/apps/animica-marketplace/sandbox/validate.py
    if len(here.parents) >= 4:
        candidates.append(here.parents[3] / "apps" / "animica-marketplace" / "sandbox" / "validate.py")
    candidates.append(Path("/root/animica/apps/animica-marketplace/sandbox/validate.py"))
    for c in candidates:
        if c.is_file():
            return c
    return None
