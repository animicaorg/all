"""
animica.ena.web
===============

The 5.1.1 feature: **SAFE** internet access for ENA.

This module is a single, hardened, reusable primitive — :func:`fetch` — that the
operator-controlled coordinator (and ONLY the coordinator, never untrusted
miners) uses to pull public-web text under one policy. The text it returns is
*corpus source material only*: it is chunked and shipped INLINE as
``synthesize_qa`` jobs, the miners' local models synthesize ``{prompt, response}``
pairs from it, and those pairs must still survive the full curation gate
(:mod:`animica.ena.curation`) before any of it can grow ENA's genome. Web text
therefore NEVER enters the genome directly — see :func:`fetch_and_chunk` and the
"anti-poison" note at the bottom.

The SSRF guard is the feature. Every fetch:

1. **Scheme/host/port/userinfo validation** — http/https only; ``user@host``
   tricks rejected; only allowlisted ports (80/443 + operator extras).
2. **Resolve + validate EVERY IP** — the hostname is resolved with
   ``getaddrinfo`` and *each* returned address is checked against the
   private/reserved/loopback/link-local/CGNAT/multicast/ULA/mapped-IPv4 ranges
   (covers decimal/octal/hex-encoded IP literals because they normalize through
   :mod:`ipaddress`). If *any* resolved IP is blocked the whole fetch is refused.
3. **IP pinning (DNS-rebind defense)** — the TCP connection is made to the
   validated, resolved IP, with the original hostname only used for the ``Host``
   header and TLS SNI. The name is never re-resolved for the socket, so a
   rebind between validation and connect cannot point us at an internal host.
4. **Per-hop redirect re-validation** — redirects are followed manually, capped
   at ``cfg.max_depth`` hops, and EACH ``Location`` is re-validated from scratch
   (scheme + host + every resolved IP). A public URL that ``302``s to
   ``127.0.0.1`` / ``169.254.169.254`` is blocked at the hop.
5. **Streaming size cap, timeout, content-type allowlist** — the body is read in
   bounded chunks and the fetch is *aborted* (never silently truncated) if it
   exceeds ``cfg.size_limit_bytes``; only text/markdown/json content types are
   accepted; ``Accept-Encoding: identity`` so the cap reflects real bytes.
6. **Per-host rate limit, total budget, robots.txt, allow/deny domains** — all
   operator-controlled via :class:`~animica.ena.models.NetworkConfig`.

Everything is standard-library only (``socket``/``ssl``/``http.client``/
``ipaddress``/``urllib``/``html.parser``) so the core install pulls no wheels and
stays GPU-free. The network I/O is isolated behind a single ``opener`` seam so
tests can exercise the entire guard by mocking ``getaddrinfo`` and the opener —
no real outbound calls.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import logging
import re
import socket
import ssl
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Optional
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

from .errors import ENAError
from .models import NetworkConfig

log = logging.getLogger("animica.ena.web")

__all__ = [
    "WebFetchError",
    "FetchResult",
    "RateLimiter",
    "FetchBudget",
    "AcquiredLedger",
    "chunk_sha",
    "validate_url",
    "ip_block_reason",
    "fetch",
    "fetch_and_chunk",
    "extract_text",
]


class WebFetchError(ENAError):
    """A fetch was refused or failed (SSRF block, policy, size, transport)."""

    code = "ENA/WEB"


# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

ALLOWED_SCHEMES = ("http", "https")
_DEFAULT_PORTS = {"http": 80, "https": 443}
DEFAULT_ALLOWED_PORTS = frozenset({80, 443})
# Only safe, text-shaped content may become corpus. No executables/archives/media.
DEFAULT_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "application/json",
    "application/xhtml+xml",
    "application/ld+json",
)
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_READ_BLOCK = 65536

# Explicit blocklists (belt-and-suspenders on top of the ipaddress properties so
# coverage does not depend on the running Python's notion of "private").
_BLOCKED_V4 = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8",          # "this" network / unspecified
    "10.0.0.0/8",         # RFC1918
    "100.64.0.0/10",      # CGNAT (RFC6598)
    "127.0.0.0/8",        # loopback
    "169.254.0.0/16",     # link-local + cloud metadata 169.254.169.254
    "172.16.0.0/12",      # RFC1918
    "192.0.0.0/24",       # IETF protocol assignments
    "192.0.2.0/24",       # TEST-NET-1
    "192.168.0.0/16",     # RFC1918
    "198.18.0.0/15",      # benchmarking
    "198.51.100.0/24",    # TEST-NET-2
    "203.0.113.0/24",     # TEST-NET-3
    "224.0.0.0/4",        # multicast
    "240.0.0.0/4",        # reserved (includes 255.255.255.255)
))
_BLOCKED_V6 = tuple(ipaddress.ip_network(n) for n in (
    "::/128",             # unspecified
    "::1/128",            # loopback
    "::ffff:0:0/96",      # IPv4-mapped (also unwrapped explicitly below)
    "64:ff9b::/96",       # NAT64 (can embed private IPv4)
    "100::/64",           # discard-only
    "2001:db8::/32",      # documentation
    "fc00::/7",           # unique-local (ULA)
    "fe80::/10",          # link-local
    "ff00::/8",           # multicast
))


# ---------------------------------------------------------------------------
# Results + rate limit + budget
# ---------------------------------------------------------------------------

@dataclass
class FetchResult:
    url: str                 # the URL that was requested
    final_url: str           # after redirects
    status: int
    content_type: str
    text: str                # extracted clean text (corpus material)
    raw_bytes: int           # bytes read off the wire (post-cap)
    host: str
    ip: str                  # the pinned IP we actually connected to
    hops: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url, "final_url": self.final_url, "status": self.status,
            "content_type": self.content_type, "chars": len(self.text),
            "raw_bytes": self.raw_bytes, "host": self.host, "ip": self.ip,
            "hops": self.hops, "elapsed_ms": self.elapsed_ms,
        }


class RateLimiter:
    """In-process per-host sliding-window limiter (requests/minute)."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = int(per_minute)
        self._hits: dict[str, list[float]] = {}

    def acquire(self, host: str) -> None:
        if self.per_minute <= 0:
            return
        now = time.monotonic()
        window = now - 60.0
        q = self._hits.setdefault(host, [])
        q[:] = [t for t in q if t > window]
        if len(q) >= self.per_minute:
            raise WebFetchError(
                f"rate limit hit for {host}: {self.per_minute}/min",
                hint="raise network.rate_limit_per_domain_per_minute or slow the feeder")
        q.append(now)


class FetchBudget:
    """A total cap on requests and bytes for one acquisition session."""

    def __init__(self, max_requests: int = 0, max_bytes: int = 0) -> None:
        self.max_requests = int(max_requests)
        self.max_bytes = int(max_bytes)
        self.requests = 0
        self.bytes = 0

    def charge_request(self) -> None:
        if self.max_requests and self.requests >= self.max_requests:
            raise WebFetchError(
                f"request budget exhausted ({self.max_requests})",
                hint="raise network.max_requests")
        self.requests += 1

    def charge_bytes(self, n: int) -> None:
        self.bytes += int(n)
        if self.max_bytes and self.bytes > self.max_bytes:
            raise WebFetchError(
                f"byte budget exhausted ({self.max_bytes})",
                hint="raise the session byte budget")


# ---------------------------------------------------------------------------
# IP validation
# ---------------------------------------------------------------------------

def ip_block_reason(ip: "ipaddress._BaseAddress | str") -> Optional[str]:
    """Return a reason string if ``ip`` must be blocked, else None.

    Normalizes via :mod:`ipaddress` (so decimal/octal/hex/mapped encodings are
    all caught), unwraps IPv4-mapped IPv6, and checks both the stdlib properties
    and the explicit reserved-range blocklists.
    """
    if isinstance(ip, str):
        try:
            ip = ipaddress.ip_address(ip)
        except ValueError:
            return "unparseable"
    # Unwrap ::ffff:a.b.c.d so a mapped private IPv4 can't sneak past v6 checks.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    # Property checks (fast, version-dependent).
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_reserved:
        return "reserved"
    if ip.is_private:
        return "private"
    # Explicit range checks (version-independent coverage).
    nets = _BLOCKED_V4 if ip.version == 4 else _BLOCKED_V6
    for net in nets:
        if ip in net:
            return f"reserved_range:{net}"
    # Defense-in-depth (kept LAST so the specific reasons above win): refuse any
    # address the stdlib does not consider globally routable. The explicit
    # blocklists already cover every must-block range (incl. CGNAT), so this only
    # tightens odd edge cases a future Python might classify as non-global.
    is_global = getattr(ip, "is_global", None)
    if is_global is False:
        return "not_global"
    return None


def _normalize_ip_literal(host: str) -> Optional[str]:
    """If ``host`` is an IP literal in ANY encoding, return its canonical text
    form; else None (it is a real hostname to be resolved).

    Catches the classic SSRF encodings — whole-decimal (``2130706433``), hex
    (``0x7f000001``), bracketed/plain IPv6, and dotted octal/hex octets
    (``0177.0.0.1``) — by normalizing through :mod:`ipaddress`, so the guard never
    has to trust the resolver to canonicalize a numeric host.
    """
    h = host.strip()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    # plain dotted-quad / standard IPv6
    try:
        return str(ipaddress.ip_address(h))
    except ValueError:
        pass
    # whole-host hex (0x7f000001) or decimal (2130706433)
    try:
        if h.lower().startswith("0x"):
            return str(ipaddress.ip_address(int(h, 16)))
        if h.isdigit():
            return str(ipaddress.ip_address(int(h)))
    except (ValueError, OverflowError, ipaddress.AddressValueError):
        pass
    # dotted IPv4 with octal/hex octets (0177.0.0.1 / 0x7f.0.0.1)
    if "." in h:
        octs = h.split(".")
        if 2 <= len(octs) <= 4 and all(octs):
            try:
                vals: list[int] = []
                for o in octs:
                    ol = o.lower()
                    if ol.startswith("0x"):
                        vals.append(int(ol, 16))
                    elif ol.startswith("0") and len(ol) > 1:
                        vals.append(int(ol, 8))
                    else:
                        vals.append(int(ol))
                if len(vals) == 4 and all(0 <= v <= 255 for v in vals):
                    return str(ipaddress.ip_address(".".join(str(v) for v in vals)))
            except ValueError:
                pass
    return None


def _resolve(host: str) -> list[str]:
    """Resolve ``host`` to the de-duplicated list of all A/AAAA addresses.

    A bare IP literal (in any encoding) is normalized through ``getaddrinfo`` and
    returned as-is; this is the single place DNS happens, so the pinned IP we
    later connect to is exactly one of the addresses validated here.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebFetchError(f"cannot resolve host {host!r}: {exc}") from exc
    ips: list[str] = []
    for info in infos:
        addr = info[4][0]
        # Strip any IPv6 scope id (fe80::1%eth0).
        addr = addr.split("%", 1)[0]
        if addr not in ips:
            ips.append(addr)
    return ips


# ---------------------------------------------------------------------------
# URL validation -> pinned target
# ---------------------------------------------------------------------------

@dataclass
class Target:
    url: str
    scheme: str
    host: str          # hostname (TLS SNI + Host header)
    port: int
    ip: str            # validated, pinned IP we connect to
    path_qs: str
    host_header: str
    resolved: list[str] = field(default_factory=list)


def _domain_allowed(host: str, cfg: NetworkConfig) -> Optional[str]:
    """Return a reason if the operator allow/deny domain policy blocks ``host``."""
    def _match(host: str, pat: str) -> bool:
        pat = pat.strip().lower().lstrip(".")
        if not pat:
            return False
        return host == pat or host.endswith("." + pat)

    for d in (cfg.deny_domains or []):
        if _match(host, d):
            return f"deny_domains:{d}"
    allow = [d for d in (cfg.allow_domains or []) if d.strip()]
    if allow and not any(_match(host, d) for d in allow):
        return "not in allow_domains"
    return None


def validate_url(url: str, cfg: NetworkConfig, *,
                 _resolver: Callable[[str], list[str]] = _resolve) -> Target:
    """Validate a URL and return a pinned :class:`Target`, or raise.

    This performs ALL of: scheme/userinfo/port checks, domain allow/deny policy,
    DNS resolution, and per-IP private/reserved rejection. The returned target's
    ``ip`` is one of the validated resolved addresses; callers MUST connect to
    that IP (not re-resolve the host) to keep the DNS-rebind defense intact.
    """
    if not url or not isinstance(url, str):
        raise WebFetchError("empty URL")
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise WebFetchError(f"scheme not allowed: {scheme!r}",
                            hint="only http/https are permitted")
    # userinfo (user:pass@host) is an SSRF/credential trick — refuse outright.
    if parts.username is not None or parts.password is not None or "@" in (parts.netloc or ""):
        raise WebFetchError("userinfo (user@host) not allowed in URL")
    host = parts.hostname
    if not host:
        raise WebFetchError("URL has no host")
    host = host.strip().strip(".").lower()
    if not host:
        raise WebFetchError("URL has no host")
    try:
        port = parts.port or _DEFAULT_PORTS[scheme]
    except ValueError as exc:
        raise WebFetchError(f"invalid port in URL: {exc}") from exc
    allowed_ports = set(DEFAULT_ALLOWED_PORTS) | {int(p) for p in (cfg.extra_allowed_ports or [])}
    if port not in allowed_ports:
        raise WebFetchError(f"port not allowed: {port}",
                            hint="add it to network.extra_allowed_ports if intended")
    reason = _domain_allowed(host, cfg)
    if reason:
        raise WebFetchError(f"domain policy blocked {host}: {reason}")

    # Normalize IP literals (decimal/octal/hex/mapped) via ipaddress BEFORE any
    # DNS, so encodings like http://2130706433 or http://0x7f000001 cannot slip a
    # private/reserved target past the guard by depending on resolver quirks.
    literal = _normalize_ip_literal(host)
    if literal is not None:
        br = ip_block_reason(literal)
        if br is not None:
            raise WebFetchError(f"SSRF blocked: {host} -> {literal} is {br}",
                                hint="IP literals are normalized + validated")
        ips = [literal]
    else:
        ips = _resolver(host)
        if not ips:
            raise WebFetchError(f"no addresses resolved for {host}")
        for ip in ips:
            br = ip_block_reason(ip)
            if br is not None:
                raise WebFetchError(
                    f"SSRF blocked: {host} -> {ip} is {br}",
                    hint="the coordinator only fetches the public internet")
    pinned = ips[0]

    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    default_port = _DEFAULT_PORTS[scheme]
    host_header = host if port == default_port else f"{host}:{port}"
    return Target(url=url, scheme=scheme, host=host, port=port, ip=pinned,
                  path_qs=path, host_header=host_header, resolved=ips)


# ---------------------------------------------------------------------------
# Pinned transport (the only place real sockets are opened)
# ---------------------------------------------------------------------------

def _read_body(resp: Any, cap: int, *, declared: Optional[str] = None) -> bytes:
    """Stream a response body in bounded blocks, ABORTING over ``cap`` bytes.

    Rejects an over-cap declared Content-Length up front, then verifies the
    actual stream too (a lying/absent header can't smuggle a huge body past us).
    Never truncates silently — over-cap is a loud :class:`WebFetchError`.
    """
    cap = int(cap)
    if declared and str(declared).isdigit() and int(declared) > cap:
        raise WebFetchError(
            f"declared Content-Length {declared} exceeds size cap {cap} bytes")
    body = bytearray()
    while True:
        block = resp.read(_READ_BLOCK)
        if not block:
            break
        body += block
        if len(body) > cap:
            raise WebFetchError(
                f"response exceeded size cap {cap} bytes (aborted)",
                hint="raise network.size_limit_bytes if the source is trusted")
    return bytes(body)


def _pinned_opener(target: Target, cfg: NetworkConfig) -> tuple[int, dict[str, str], bytes]:
    """Open a connection to the PINNED IP and return (status, headers, body).

    TLS uses ``server_hostname=target.host`` so the certificate is validated
    against the real name while the TCP connection goes to the already-validated
    IP. The body is read in bounded blocks and the read is aborted if it exceeds
    ``cfg.size_limit_bytes`` (no silent truncation).
    """
    timeout = float(cfg.request_timeout_seconds)
    cap = int(cfg.size_limit_bytes)
    raw = socket.create_connection((target.ip, target.port), timeout=timeout)
    try:
        if target.scheme == "https":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(raw, server_hostname=target.host)
        else:
            sock = raw
    except Exception as exc:  # noqa: BLE001
        raw.close()
        raise WebFetchError(f"TLS/connect failed for {target.host}: {exc}") from exc

    conn = http.client.HTTPConnection(target.host, target.port, timeout=timeout)
    conn.sock = sock  # pinned socket; http.client will not re-connect/re-resolve
    try:
        accept = ", ".join(DEFAULT_CONTENT_TYPES) + ", */*;q=0.1"
        conn.request("GET", target.path_qs, headers={
            "Host": target.host_header,
            "User-Agent": cfg.user_agent,
            "Accept": accept,
            # Refuse compression so the streamed byte count == the size cap unit.
            "Accept-Encoding": "identity",
            "Connection": "close",
        })
        resp = conn.getresponse()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        status = int(resp.status)
        body = _read_body(resp, cap, declared=headers.get("content-length"))
        return status, headers, body
    except WebFetchError:
        raise
    except (http.client.HTTPException, OSError) as exc:
        raise WebFetchError(f"transport error for {target.host}: {exc}") from exc
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# robots.txt (best-effort, fail-open, same SSRF guard)
# ---------------------------------------------------------------------------

def _robots_allows(target: Target, cfg: NetworkConfig,
                   opener: Callable, resolver: Callable,
                   cache: Optional[dict]) -> bool:
    key = (target.scheme, target.host, target.port)
    rp: Optional[robotparser.RobotFileParser] = None
    if cache is not None:
        rp = cache.get(key)
    if rp is None:
        rp = robotparser.RobotFileParser()
        try:
            rt = validate_url(f"{target.scheme}://{target.host_header}/robots.txt",
                              cfg, _resolver=resolver)
            status, _headers, body = opener(rt, cfg)
            if status == 200:
                # empty 200 == no rules -> allow all
                rp.parse(body.decode("utf-8", "replace").splitlines() if body else [])
            elif status in (404, 410):
                rp.parse([])  # no robots present -> allow all (correct semantics)
            else:
                # 5xx / 401 / 403 / etc are a robots FETCH ERROR (not "no robots").
                raise WebFetchError(f"robots.txt fetch returned HTTP {status}")
        except Exception as exc:  # noqa: BLE001 - robots fetch is best-effort
            # Configurable fail mode (graft 5): default fail-OPEN (allow) preserves
            # 5.1.0 behavior; strict operators set robots_on_error="deny" to
            # fail-CLOSED on a robots fetch error (SSRF/transport/5xx). We do NOT
            # cache a deny so a transient error is re-checked on the next attempt.
            mode = str(getattr(cfg, "robots_on_error", "allow")).strip().lower()
            if mode == "deny":
                log.info("web: robots.txt fetch for %s failed (%s); DENY (fail-closed)",
                         target.host, exc)
                return False
            log.info("web: robots.txt fetch for %s failed (%s); allowing (fail-open)",
                     target.host, exc)
            rp.parse([])  # fail-open on robots fetch error
        if cache is not None:
            cache[key] = rp
    try:
        return rp.can_fetch(cfg.user_agent, target.url)
    except Exception:  # noqa: BLE001
        return True


# ---------------------------------------------------------------------------
# HTML -> clean text extraction (stdlib only)
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "head", "iframe"}
_BLOCK_TAGS = {"p", "br", "div", "li", "ul", "ol", "tr", "table", "section",
               "article", "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6",
               "blockquote", "pre", "hr"}
_WS_LINE = re.compile(r"[ \t\f\v]+")


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs):  # noqa: D401
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs):
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str):
        if tag in _SKIP_TAGS and self._skip > 0:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str):
        if self._skip == 0 and data:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        lines = [_WS_LINE.sub(" ", ln).strip() for ln in raw.splitlines()]
        out: list[str] = []
        blank = False
        for ln in lines:
            if ln:
                out.append(ln)
                blank = False
            elif not blank:
                out.append("")  # collapse runs of blank lines to one
                blank = True
        return "\n".join(out).strip()


def extract_text(body: bytes, content_type: str) -> str:
    """Turn a fetched body into clean corpus text per its content type."""
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    # Decode with the declared charset if any, else utf-8 with replacement.
    charset = "utf-8"
    if "charset=" in (content_type or "").lower():
        try:
            charset = content_type.lower().split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
        except Exception:  # noqa: BLE001
            charset = "utf-8"
    try:
        text = body.decode(charset, "replace")
    except (LookupError, UnicodeDecodeError):
        text = body.decode("utf-8", "replace")
    if ctype in ("text/html", "application/xhtml+xml"):
        p = _HTMLTextExtractor()
        try:
            p.feed(text)
            p.close()
        except Exception:  # noqa: BLE001 - never let a malformed page raise
            return text
        return p.get_text()
    # text/plain, text/markdown, application/json, ld+json -> use as-is.
    return text


# ---------------------------------------------------------------------------
# fetch (the hardened primitive)
# ---------------------------------------------------------------------------

def fetch(url: str, cfg: Optional[NetworkConfig] = None, *,
          budget: Optional[FetchBudget] = None,
          rate_limiter: Optional[RateLimiter] = None,
          robots_cache: Optional[dict] = None,
          opener: Optional[Callable] = None,
          resolver: Optional[Callable] = None) -> FetchResult:
    """Fetch ``url`` safely and return extracted corpus text.

    Raises :class:`WebFetchError` for any policy/SSRF/transport failure (a refusal
    is loud, never a silent skip). ``opener`` / ``resolver`` are injectable seams
    used by tests to avoid real network/DNS; production uses the pinned opener and
    ``getaddrinfo``.
    """
    cfg = cfg or NetworkConfig()
    # Master switch (graft 7): one place to hard-disable ALL coordinator web
    # egress. Enforced HERE — the single network seam — so the scrape job, the
    # feeder web pass, and the acquire CLI are all covered and none can bypass it.
    # (validate_url is intentionally NOT gated, so `acquire check` can still audit
    # policy without egress.)
    if not getattr(cfg, "acquire_enabled", True):
        raise WebFetchError(
            "web acquisition disabled (network.acquire_enabled=false)",
            hint="set network.acquire_enabled=true / ENA_ACQUIRE_ENABLED=1 to allow "
                 "coordinator web egress")
    opener = opener or _pinned_opener
    resolver = resolver or _resolve
    allowed_ct = {c.lower() for c in (cfg.content_type_allowlist or DEFAULT_CONTENT_TYPES)}

    started = time.monotonic()
    hops: list[dict[str, Any]] = []
    current = url
    max_hops = max(0, int(cfg.max_depth))
    for hop in range(max_hops + 1):
        target = validate_url(current, cfg, _resolver=resolver)
        if cfg.respect_robots and not _robots_allows(target, cfg, opener, resolver, robots_cache):
            raise WebFetchError(f"blocked by robots.txt: {target.url}")
        if rate_limiter is not None:
            rate_limiter.acquire(target.host)
        if budget is not None:
            budget.charge_request()

        status, headers, body = opener(target, cfg)
        hop_rec = {"url": target.url, "ip": target.ip, "status": status,
                   "resolved": target.resolved}
        hops.append(hop_rec)
        log.info("web: %s -> %s [%s] %s", target.url, target.ip, status,
                 headers.get("content-type", ""))

        if status in _REDIRECT_STATUSES:
            loc = headers.get("location")
            if not loc:
                raise WebFetchError(f"redirect {status} without Location from {target.url}")
            nxt = urljoin(target.url, loc)
            if hop >= max_hops:
                raise WebFetchError(
                    f"too many redirects (> {max_hops}); last hop -> {nxt}")
            current = nxt
            continue

        if status != 200:
            raise WebFetchError(f"HTTP {status} for {target.url}")

        ctype_full = headers.get("content-type", "")
        ctype = ctype_full.split(";", 1)[0].strip().lower()
        # Default-deny: a missing/empty Content-Type does NOT get a pass (an
        # untyped 200 must not slip past the text allowlist into synthesis).
        if ctype not in allowed_ct:
            raise WebFetchError(
                f"content-type not allowed: {ctype or '(missing)'!r} for {target.url}",
                hint=f"allowed: {sorted(allowed_ct)}")
        if budget is not None:
            budget.charge_bytes(len(body))

        text = extract_text(body, ctype_full)
        return FetchResult(
            url=url, final_url=target.url, status=status,
            content_type=ctype or "application/octet-stream", text=text,
            raw_bytes=len(body), host=target.host, ip=target.ip, hops=hops,
            elapsed_ms=int((time.monotonic() - started) * 1000))

    raise WebFetchError(f"redirect limit ({max_hops}) exhausted for {url}")


# ---------------------------------------------------------------------------
# fetch -> corpus chunks  (coordinator-side; feeds INLINE synthesize_qa)
# ---------------------------------------------------------------------------

def fetch_and_chunk(url: str, cfg: Optional[NetworkConfig] = None, *,
                    max_chars: int = 1200, overlap: int = 150,
                    min_chars: int = 200,
                    **fetch_kwargs: Any) -> tuple[FetchResult, list[dict[str, Any]]]:
    """Fetch ``url`` safely and split its clean text into corpus chunk records.

    Returns ``(FetchResult, chunks)`` where each chunk is
    ``{ordinal, text, source, content_type, source_sha, fetched_at}``. These
    chunks are corpus SOURCE
    material only: the operator-controlled feeder ships each chunk's ``text``
    INLINE as a ``synthesize_qa`` job's ``params['corpus']``. The miner's local
    model synthesizes ``{prompt, response}`` pairs from it and those pairs must
    still pass :func:`animica.ena.curation.quality_gate` (schema + length +
    groundedness vs this very chunk + dedupe) before they can grow the genome.
    Web text NEVER bypasses that path.
    """
    from .retrieval import chunk_text  # local import: keep web.py import-light
    res = fetch(url, cfg, **fetch_kwargs)
    fetched_at = int(time.time())
    chunks: list[dict[str, Any]] = []
    for i, piece in enumerate(chunk_text(res.text, max_chars=max_chars, overlap=overlap)):
        if len(piece.strip()) < int(min_chars):
            continue
        # Provenance (graft 4): source_sha (sha3 of THIS chunk's text) + fetched_at
        # make a promoted gene auditable URL -> bytes -> round -> gene. Pure
        # metadata; it does not affect groundedness (gate recomputes vs corpus).
        chunks.append({"ordinal": len(chunks), "text": piece,
                       "source": res.final_url, "content_type": res.content_type,
                       "source_sha": chunk_sha(piece), "fetched_at": fetched_at})
    return res, chunks


# ---------------------------------------------------------------------------
# Cross-tick acquisition ledger  (graft 2: skip re-emitting identical corpus)
# ---------------------------------------------------------------------------

def chunk_sha(text: str) -> str:
    """SHA3-256 hex of a corpus chunk's text (the ledger + provenance key)."""
    return hashlib.sha3_256((text or "").encode("utf-8")).hexdigest()


class AcquiredLedger:
    """A tiny persistent JSONL set of corpus-chunk SHA3s already shipped as
    ``synthesize_qa`` jobs, so the feeder / ``acquire feed`` don't re-synthesize
    identical corpus on every tick.

    This is **purely an efficiency / budget win**: the curation gate already
    dedupes synthesized *pairs* (so re-emitting could never poison or double-grow
    the genome), but re-synthesizing the same chunk every tick wastes miner
    compute and the web byte budget. Best-effort + opt-in: a missing/corrupt
    ledger simply means "nothing seen yet", and persistence failures never raise
    (the caller still emits — it just won't remember). The ledger lives under the
    ENA home so it survives the short-lived feeder timer invocations.
    """

    def __init__(self, path: "str | Path | None") -> None:
        self.path = Path(path) if path else None
        self._seen: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    sha = rec.get("sha")
                    if sha:
                        self._seen[sha] = rec
        except OSError:
            pass

    def __len__(self) -> int:
        return len(self._seen)

    def seen(self, text: str) -> bool:
        return chunk_sha(text) in self._seen

    def record(self, text: str, source: str = "") -> bool:
        """Mark ``text`` as acquired. Returns True if it was NEW (and persisted),
        False if it was already in the ledger."""
        sha = chunk_sha(text)
        if sha in self._seen:
            return False
        rec = {"sha": sha, "source": source, "first_seen": int(time.time())}
        self._seen[sha] = rec
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError as exc:
                log.info("web: acquired-ledger persist failed (%s); continuing", exc)
        return True

    def filter_new(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return only the chunks whose text has not been acquired before,
        recording each kept chunk so the NEXT tick skips it."""
        out: list[dict[str, Any]] = []
        for c in chunks:
            if self.record(c.get("text", ""), c.get("source", "")):
                out.append(c)
        return out
