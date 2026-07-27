"""
.anm name resolution + trustless content fetch.

The registry is centralized at the gateway (Prisma AnmDomain), so resolution is a public
JSON GET; trustlessness comes from CID verification: the CID is anm1c+sha3_256(bytes), so a
tampered gateway/middlebox is caught locally before a single byte is rendered.

  resolve(name)            -> ResolvedName (contentCid, records, kind, owner, status, …)
  fetch_content(cid)       -> bytes, verified sha3_256(bytes) == cid suffix (else raises)
  resolve_and_fetch(name)  -> (ResolvedName, html_bytes | None, endpoint_url | None)

Pure stdlib (urllib + hashlib) so it works identically in the app and in tests.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

from .config import API_BASE, CID_PREFIX, MAX_CONTENT_BYTES

_CID_RE = re.compile(r"^anm1c[0-9a-f]{64}$")
_ENDPOINT_RE = re.compile(r"^https?://", re.IGNORECASE)


class ResolveError(RuntimeError):
    pass


class ContentVerifyError(RuntimeError):
    """Fetched bytes did not hash to their CID — tampering or corruption. Never render these."""


@dataclass
class ResolvedName:
    name: str
    fqdn: str
    kind: str
    owner: str
    content_cid: Optional[str]
    records: dict = field(default_factory=dict)
    node_providers: list = field(default_factory=list)
    status: str = "ACTIVE"
    expires_at: Optional[str] = None
    agent_handle: Optional[str] = None

    @property
    def endpoint(self) -> Optional[str]:
        for k in ("endpoint", "url"):
            v = self.records.get(k)
            if isinstance(v, str) and _ENDPOINT_RE.match(v):
                return v
        return None


def normalize_name(raw: str) -> str:
    s = (raw or "").strip().lower()
    # accept "name", "name.anm", "anm://name", "anm://name/path" — keep only the label
    s = s.replace("anm://", "")
    s = s.split("/")[0]
    if s.endswith(".anm"):
        s = s[:-4]
    return s


def compute_cid(data: bytes) -> str:
    return CID_PREFIX + hashlib.sha3_256(data).hexdigest()


def is_cid(s: str) -> bool:
    return bool(_CID_RE.match(s or ""))


def _get_json(url: str, timeout: int = 12) -> dict:
    from .netcfg import ca_context
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ca_context()) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise ResolveError(f"{url} -> {e.code}") from e
    except urllib.error.URLError as e:
        raise ResolveError(f"{url} unreachable: {e.reason}") from e


def resolve(name: str) -> ResolvedName:
    label = normalize_name(name)
    if not label:
        raise ResolveError("empty name")
    d = _get_json(f"{API_BASE}/names/{urllib.parse.quote(label)}")
    r = d.get("resolved") or {}
    if not r:
        raise ResolveError(f"{label}.anm is not registered")
    records = r.get("records")
    if isinstance(records, str):
        try:
            records = json.loads(records)
        except ValueError:
            records = {}
    if not isinstance(records, dict):
        records = {}
    return ResolvedName(
        name=r.get("name", label),
        fqdn=d.get("fqdn", f"{label}.anm"),
        kind=r.get("kind", "app"),
        owner=r.get("owner", ""),
        content_cid=r.get("contentCid"),
        records=records,
        node_providers=r.get("nodeProviders") or [],
        status=r.get("status", "ACTIVE"),
        expires_at=r.get("expiresAt"),
        agent_handle=r.get("agentHandle"),
    )


def fetch_content(cid: str, timeout: int = 15) -> bytes:
    if not is_cid(cid):
        raise ContentVerifyError(f"not a valid CID: {cid!r}")
    from .netcfg import ca_context
    req = urllib.request.Request(f"{API_BASE}/content/{cid}")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ca_context()) as r:
            data = r.read(MAX_CONTENT_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise ContentVerifyError(f"content {cid} -> {e.code}") from e
    except urllib.error.URLError as e:
        raise ContentVerifyError(f"content {cid} unreachable: {e.reason}") from e
    if len(data) > MAX_CONTENT_BYTES:
        raise ContentVerifyError(f"content {cid} exceeds {MAX_CONTENT_BYTES} bytes")
    got = compute_cid(data)
    if got != cid:
        # The gateway could be lying or a middlebox rewrote the bytes. Refuse to render.
        raise ContentVerifyError(f"CID mismatch: served bytes hash to {got}, expected {cid}")
    return data


def resolve_and_fetch(name: str):
    """Return (ResolvedName, html_bytes|None, endpoint_url|None).

    A CID-hosted site returns verified bytes; an endpoint-record name returns its URL; a name
    with neither returns (rn, None, None) so the caller can show a resolver/placeholder card."""
    rn = resolve(name)
    if rn.status != "ACTIVE":
        raise ResolveError(f"{rn.fqdn} is {rn.status.lower()} (not active)")
    if rn.content_cid:
        return rn, fetch_content(rn.content_cid), None
    if rn.endpoint:
        return rn, None, rn.endpoint
    return rn, None, None
