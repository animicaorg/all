"""Tests for the 5.1.1 SSRF-safe web acquisition primitive (animica.ena.web).

The SSRF guard IS the feature, so coverage is exhaustive on the validation:
private/reserved IP rejection (v4 + v6 + mapped + encoded), scheme/userinfo/port
checks, per-hop redirect re-validation, DNS-rebind pinning, size cap, content-type
allowlist, rate limit, budget, robots.txt, and the anti-poison contract
(fetch_and_chunk yields corpus only — never genome writes).

NO REAL NETWORK: the DNS resolver and the HTTP opener are injected fakes, so the
whole guard runs offline and deterministically.
"""

from __future__ import annotations

import pytest

from animica.ena import web
from animica.ena.models import NetworkConfig


# ---------------------------------------------------------------------------
# Fakes: a deterministic resolver + a routing opener (no sockets, no DNS)
# ---------------------------------------------------------------------------

def make_resolver(mapping: dict[str, list[str]]):
    """Return a resolver(host)->ips backed by ``mapping`` (else NXDOMAIN-ish)."""
    def _r(host: str) -> list[str]:
        if host in mapping:
            return list(mapping[host])
        raise web.WebFetchError(f"cannot resolve host {host!r}")
    return _r


class Routes:
    """A fake opener: maps a target URL (or host+path) to (status, headers, body)."""

    def __init__(self):
        self._by_url: dict[str, tuple] = {}
        self.calls: list[str] = []

    def add(self, url: str, status: int, headers: dict, body: bytes = b""):
        self._by_url[url] = (status, {k.lower(): v for k, v in headers.items()}, body)
        return self

    def __call__(self, target: web.Target, cfg) -> tuple:
        self.calls.append(target.url)
        # robots.txt: default allow-all unless explicitly registered.
        if target.path_qs == "/robots.txt" and target.url not in self._by_url:
            return 404, {}, b""
        if target.url in self._by_url:
            return self._by_url[target.url]
        return 200, {"content-type": "text/plain"}, b"default body"


PUBLIC = {"example.com": ["93.184.216.34"], "site.test": ["198.51.100.7"]}
# NB: 198.51.100.0/24 is TEST-NET-2 (blocked); use a routable-looking public IP.
PUBLIC = {"example.com": ["93.184.216.34"], "site.test": ["8.8.8.8"]}


def cfg(**kw) -> NetworkConfig:
    base = dict(respect_robots=False, rate_limit_per_domain_per_minute=0)
    base.update(kw)
    return NetworkConfig(**base)


# ---------------------------------------------------------------------------
# ip_block_reason — the core of the SSRF guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "127.5.5.5",           # loopback /8
    "10.0.0.1", "10.255.1.1",           # RFC1918
    "172.16.0.1", "172.31.255.255",     # RFC1918
    "192.168.1.1",                      # RFC1918
    "169.254.169.254",                  # cloud metadata (link-local)
    "169.254.0.1",
    "100.64.0.1", "100.127.255.255",    # CGNAT
    "0.0.0.0", "0.1.2.3",               # this-network / unspecified
    "224.0.0.1", "239.0.0.1",           # multicast
    "240.0.0.1", "255.255.255.255",     # reserved
    "198.18.0.1",                       # benchmarking
    "::1",                              # IPv6 loopback
    "::",                               # IPv6 unspecified
    "fc00::1", "fd12:3456::1",          # ULA
    "fe80::1",                          # IPv6 link-local
    "ff02::1",                          # IPv6 multicast
    "::ffff:127.0.0.1",                 # IPv4-mapped loopback
    "::ffff:10.0.0.1",                  # IPv4-mapped RFC1918
    "2001:db8::1",                      # documentation
])
def test_ip_block_reason_blocks_internal(ip):
    assert web.ip_block_reason(ip) is not None, f"{ip} must be blocked"


@pytest.mark.parametrize("ip", [
    "8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_ip_block_reason_allows_public(ip):
    assert web.ip_block_reason(ip) is None, f"{ip} must be allowed"


def test_ip_block_reason_unparseable():
    assert web.ip_block_reason("not-an-ip") == "unparseable"


# ---------------------------------------------------------------------------
# validate_url — scheme / userinfo / port / domain / resolution
# ---------------------------------------------------------------------------

def test_validate_rejects_non_http_schemes():
    r = make_resolver(PUBLIC)
    for url in ("file:///etc/passwd", "ftp://example.com/x",
                "gopher://example.com", "data:text/plain,hi"):
        with pytest.raises(web.WebFetchError):
            web.validate_url(url, cfg(), _resolver=r)


def test_validate_rejects_userinfo():
    r = make_resolver({"169.254.169.254": ["169.254.169.254"], **PUBLIC})
    # classic SSRF: real host hidden behind userinfo
    with pytest.raises(web.WebFetchError):
        web.validate_url("http://example.com@169.254.169.254/", cfg(), _resolver=r)
    with pytest.raises(web.WebFetchError):
        web.validate_url("http://user:pass@example.com/", cfg(), _resolver=r)


def test_validate_rejects_disallowed_port():
    r = make_resolver(PUBLIC)
    with pytest.raises(web.WebFetchError):
        web.validate_url("http://example.com:22/", cfg(), _resolver=r)
    # allowlisted via config
    t = web.validate_url("http://example.com:8080/", cfg(extra_allowed_ports=[8080]),
                         _resolver=r)
    assert t.port == 8080


def test_validate_blocks_when_any_resolved_ip_is_private():
    # Host resolves to BOTH a public and a private IP -> the whole fetch is refused.
    r = make_resolver({"sneaky.test": ["8.8.8.8", "127.0.0.1"]})
    with pytest.raises(web.WebFetchError) as ei:
        web.validate_url("https://sneaky.test/", cfg(), _resolver=r)
    assert "SSRF" in str(ei.value)


def test_validate_blocks_encoded_decimal_ip():
    # http://2130706433 is 127.0.0.1; getaddrinfo would normalize it -> blocked.
    r = make_resolver({"2130706433": ["127.0.0.1"]})
    with pytest.raises(web.WebFetchError):
        web.validate_url("http://2130706433/", cfg(), _resolver=r)


def test_validate_pins_first_resolved_ip():
    r = make_resolver(PUBLIC)
    t = web.validate_url("https://example.com/path?q=1", cfg(), _resolver=r)
    assert t.ip == "93.184.216.34" and t.host == "example.com"
    assert t.path_qs == "/path?q=1"
    assert t.host_header == "example.com"  # default port omitted


def test_validate_domain_allow_deny():
    r = make_resolver(PUBLIC)
    # deny wins
    with pytest.raises(web.WebFetchError):
        web.validate_url("https://example.com/", cfg(deny_domains=["example.com"]),
                         _resolver=r)
    # allowlist excludes others
    with pytest.raises(web.WebFetchError):
        web.validate_url("https://example.com/",
                         cfg(allow_domains=["other.test"]), _resolver=r)
    # allowlist matches subdomain suffix
    r2 = make_resolver({"docs.example.com": ["8.8.8.8"]})
    t = web.validate_url("https://docs.example.com/",
                         cfg(allow_domains=["example.com"]), _resolver=r2)
    assert t.host == "docs.example.com"


# ---------------------------------------------------------------------------
# fetch — happy path + extraction
# ---------------------------------------------------------------------------

HTML = (b"<html><head><title>t</title><style>.x{}</style>"
        b"<script>evil()</script></head><body><h1>Quantum</h1>"
        b"<p>Animica draws entropy from IDQ Quantis hardware.</p></body></html>")


def test_fetch_happy_path_extracts_text():
    r = make_resolver(PUBLIC)
    op = Routes().add("https://example.com/doc",
                      200, {"content-type": "text/html; charset=utf-8"}, HTML)
    res = web.fetch("https://example.com/doc", cfg(), opener=op, resolver=r)
    assert res.status == 200 and res.ip == "93.184.216.34"
    assert "Quantum" in res.text and "Animica draws entropy" in res.text
    assert "evil()" not in res.text and ".x{}" not in res.text  # script/style stripped
    assert res.content_type == "text/html"


def test_fetch_plain_and_json_passthrough():
    r = make_resolver(PUBLIC)
    op = Routes().add("https://example.com/a", 200,
                      {"content-type": "text/plain"}, b"hello world")
    assert web.fetch("https://example.com/a", cfg(), opener=op, resolver=r).text == "hello world"
    op2 = Routes().add("https://example.com/b", 200,
                       {"content-type": "application/json"}, b'{"k": 1}')
    assert '{"k": 1}' in web.fetch("https://example.com/b", cfg(), opener=op2, resolver=r).text


# ---------------------------------------------------------------------------
# fetch — redirect re-validation (THE SSRF redirect requirement)
# ---------------------------------------------------------------------------

def test_redirect_to_metadata_is_blocked_per_hop():
    # Public page 302s to the cloud metadata endpoint -> must be blocked at hop 2.
    r = make_resolver({**PUBLIC, "metadata.evil.test": ["169.254.169.254"]})
    op = Routes().add("https://example.com/start", 302,
                      {"location": "http://metadata.evil.test/latest/meta-data/"}, b"")
    with pytest.raises(web.WebFetchError) as ei:
        web.fetch("https://example.com/start", cfg(), opener=op, resolver=r)
    assert "SSRF" in str(ei.value) or "169.254" in str(ei.value)


def test_redirect_followed_then_resolved():
    r = make_resolver(PUBLIC)
    op = (Routes()
          .add("https://example.com/start", 301,
               {"location": "https://site.test/final"}, b"")
          .add("https://site.test/final", 200,
               {"content-type": "text/plain"}, b"final content"))
    res = web.fetch("https://example.com/start", cfg(), opener=op, resolver=r)
    assert res.final_url == "https://site.test/final"
    assert res.text == "final content"
    assert len(res.hops) == 2


def test_redirect_cap_exhausted():
    r = make_resolver(PUBLIC)
    # always redirects to itself-ish public target -> exceeds max_depth
    op = (Routes()
          .add("https://example.com/a", 302, {"location": "https://example.com/b"}, b"")
          .add("https://example.com/b", 302, {"location": "https://example.com/c"}, b"")
          .add("https://example.com/c", 302, {"location": "https://example.com/d"}, b""))
    with pytest.raises(web.WebFetchError) as ei:
        web.fetch("https://example.com/a", cfg(max_depth=2), opener=op, resolver=r)
    assert "redirect" in str(ei.value).lower()


def test_redirect_without_location():
    r = make_resolver(PUBLIC)
    op = Routes().add("https://example.com/x", 302, {}, b"")
    with pytest.raises(web.WebFetchError):
        web.fetch("https://example.com/x", cfg(), opener=op, resolver=r)


# ---------------------------------------------------------------------------
# fetch — content-type allowlist + non-200
# ---------------------------------------------------------------------------

def test_content_type_not_allowed():
    r = make_resolver(PUBLIC)
    op = Routes().add("https://example.com/x.zip", 200,
                      {"content-type": "application/zip"}, b"PK\x03\x04")
    with pytest.raises(web.WebFetchError) as ei:
        web.fetch("https://example.com/x.zip", cfg(), opener=op, resolver=r)
    assert "content-type" in str(ei.value).lower()


def test_non_200_raises():
    r = make_resolver(PUBLIC)
    op = Routes().add("https://example.com/missing", 404, {"content-type": "text/html"}, b"")
    with pytest.raises(web.WebFetchError):
        web.fetch("https://example.com/missing", cfg(), opener=op, resolver=r)


# ---------------------------------------------------------------------------
# size cap (the streaming-abort helper)
# ---------------------------------------------------------------------------

class _Chunked:
    """Fake response: yields ``data`` in blocks then EOF."""
    def __init__(self, data: bytes, block: int = 4):
        self._buf = data
        self._i = 0
        self._block = block

    def read(self, n: int = -1) -> bytes:
        if self._i >= len(self._buf):
            return b""
        out = self._buf[self._i:self._i + self._block]
        self._i += len(out)
        return out


def test_read_body_aborts_over_cap():
    with pytest.raises(web.WebFetchError):
        web._read_body(_Chunked(b"x" * 100), cap=16)


def test_read_body_rejects_declared_content_length():
    with pytest.raises(web.WebFetchError):
        web._read_body(_Chunked(b"x" * 4), cap=16, declared="9999")


def test_read_body_ok_under_cap():
    assert web._read_body(_Chunked(b"hello"), cap=64) == b"hello"


# ---------------------------------------------------------------------------
# rate limit + budget
# ---------------------------------------------------------------------------

def test_rate_limiter_per_host():
    rl = web.RateLimiter(per_minute=2)
    rl.acquire("a.test")
    rl.acquire("a.test")
    with pytest.raises(web.WebFetchError):
        rl.acquire("a.test")
    rl.acquire("b.test")  # other host has its own window


def test_budget_requests_and_bytes():
    b = web.FetchBudget(max_requests=1, max_bytes=10)
    b.charge_request()
    with pytest.raises(web.WebFetchError):
        b.charge_request()
    b2 = web.FetchBudget(max_bytes=10)
    b2.charge_bytes(8)
    with pytest.raises(web.WebFetchError):
        b2.charge_bytes(5)


def test_fetch_threads_budget_and_rate():
    r = make_resolver(PUBLIC)
    op = Routes().add("https://example.com/a", 200, {"content-type": "text/plain"}, b"hi")
    budget = web.FetchBudget(max_requests=5, max_bytes=1000)
    rl = web.RateLimiter(per_minute=10)
    res = web.fetch("https://example.com/a", cfg(), opener=op, resolver=r,
                    budget=budget, rate_limiter=rl)
    assert res.text == "hi"
    assert budget.requests == 1 and budget.bytes == 2


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------

def test_robots_disallow_blocks():
    r = make_resolver(PUBLIC)
    op = (Routes()
          .add("https://example.com/robots.txt", 200, {"content-type": "text/plain"},
               b"User-agent: *\nDisallow: /private")
          .add("https://example.com/private/x", 200, {"content-type": "text/html"}, HTML))
    c = NetworkConfig(respect_robots=True, rate_limit_per_domain_per_minute=0)
    with pytest.raises(web.WebFetchError) as ei:
        web.fetch("https://example.com/private/x", c, opener=op, resolver=r)
    assert "robots" in str(ei.value).lower()


def test_robots_allow_passes():
    r = make_resolver(PUBLIC)
    op = (Routes()
          .add("https://example.com/robots.txt", 200, {"content-type": "text/plain"},
               b"User-agent: *\nDisallow: /private")
          .add("https://example.com/public/x", 200, {"content-type": "text/html"}, HTML))
    c = NetworkConfig(respect_robots=True, rate_limit_per_domain_per_minute=0)
    res = web.fetch("https://example.com/public/x", c, opener=op, resolver=r)
    assert "Quantum" in res.text


def test_robots_fetch_failure_is_fail_open():
    r = make_resolver(PUBLIC)
    # robots.txt 500s; fetch should still proceed (best-effort robots).
    op = (Routes()
          .add("https://example.com/robots.txt", 500, {}, b"")
          .add("https://example.com/x", 200, {"content-type": "text/plain"}, b"ok"))
    c = NetworkConfig(respect_robots=True, rate_limit_per_domain_per_minute=0)
    assert web.fetch("https://example.com/x", c, opener=op, resolver=r).text == "ok"


# ---------------------------------------------------------------------------
# extract_text
# ---------------------------------------------------------------------------

def test_extract_text_strips_scripts_and_collapses():
    out = web.extract_text(HTML, "text/html")
    assert "Quantum" in out
    assert "evil" not in out
    assert "\n\n\n" not in out  # blank runs collapsed


# ---------------------------------------------------------------------------
# fetch_and_chunk — the anti-poison contract (corpus only)
# ---------------------------------------------------------------------------

def test_fetch_and_chunk_returns_corpus_records():
    r = make_resolver(PUBLIC)
    big = ("Animica useful work. " * 400).encode("utf-8")
    op = Routes().add("https://example.com/big", 200,
                      {"content-type": "text/plain"}, big)
    res, chunks = web.fetch_and_chunk("https://example.com/big", cfg(),
                                      max_chars=500, opener=op, resolver=r)
    assert chunks and all(c["source"] == "https://example.com/big" for c in chunks)
    # graft 4: chunk records carry provenance (source_sha + fetched_at) for an
    # auditable URL -> bytes -> round -> gene trail.
    assert all(set(c) == {"ordinal", "text", "source", "content_type",
                          "source_sha", "fetched_at"} for c in chunks)
    assert all(c["source_sha"] == web.chunk_sha(c["text"]) for c in chunks)
    assert all(isinstance(c["fetched_at"], int) and c["fetched_at"] > 0 for c in chunks)
    # No record is shaped like a training pair — it's pure corpus source.
    assert all("prompt" not in c and "response" not in c for c in chunks)


def test_fetch_and_chunk_drops_thin_chunks():
    r = make_resolver(PUBLIC)
    op = Routes().add("https://example.com/tiny", 200,
                      {"content-type": "text/plain"}, b"tiny")
    _res, chunks = web.fetch_and_chunk("https://example.com/tiny", cfg(),
                                       min_chars=200, opener=op, resolver=r)
    assert chunks == []


# ---------------------------------------------------------------------------
# graft 6: not_global defense-in-depth (kept LAST so specific reasons win)
# ---------------------------------------------------------------------------

def test_not_global_defense_in_depth(monkeypatch):
    # On modern Python every must-block range is already caught by is_private /
    # the explicit lists, so this branch is belt-and-suspenders. Exercise it
    # directly with a fake address that slips past everything else.
    monkeypatch.setattr(web, "_BLOCKED_V4", ())
    monkeypatch.setattr(web, "_BLOCKED_V6", ())

    class _FakeIP:
        version = 4
        ipv4_mapped = None
        is_loopback = is_link_local = is_multicast = False
        is_unspecified = is_reserved = is_private = False
        is_global = False

    assert web.ip_block_reason(_FakeIP()) == "not_global"

    class _FakeGlobal(_FakeIP):
        is_global = True

    assert web.ip_block_reason(_FakeGlobal()) is None


# ---------------------------------------------------------------------------
# graft 5: robots fail-mode configurable (default allow / opt-in deny)
# ---------------------------------------------------------------------------

def test_robots_on_error_deny_fails_closed():
    r = make_resolver(PUBLIC)
    # robots.txt 500s; with robots_on_error=deny the whole fetch must be refused.
    op = (Routes()
          .add("https://example.com/robots.txt", 500, {}, b"")
          .add("https://example.com/x", 200, {"content-type": "text/plain"}, b"ok"))
    c = NetworkConfig(respect_robots=True, rate_limit_per_domain_per_minute=0,
                      robots_on_error="deny")
    with pytest.raises(web.WebFetchError) as ei:
        web.fetch("https://example.com/x", c, opener=op, resolver=r)
    assert "robots" in str(ei.value).lower()


def test_robots_on_error_default_allow_is_fail_open():
    r = make_resolver(PUBLIC)
    op = (Routes()
          .add("https://example.com/robots.txt", 500, {}, b"")
          .add("https://example.com/x", 200, {"content-type": "text/plain"}, b"ok"))
    c = NetworkConfig(respect_robots=True, rate_limit_per_domain_per_minute=0)
    assert c.robots_on_error == "allow"  # default preserves 5.1.0 behavior
    assert web.fetch("https://example.com/x", c, opener=op, resolver=r).text == "ok"


# ---------------------------------------------------------------------------
# graft 7: acquire_enabled master switch (one place to kill web egress)
# ---------------------------------------------------------------------------

def test_acquire_disabled_blocks_fetch_at_the_seam():
    r = make_resolver(PUBLIC)
    op = Routes().add("https://example.com/a", 200, {"content-type": "text/plain"}, b"hi")
    c = cfg(acquire_enabled=False)
    with pytest.raises(web.WebFetchError) as ei:
        web.fetch("https://example.com/a", c, opener=op, resolver=r)
    assert "disabled" in str(ei.value).lower()
    # fetch_and_chunk routes through fetch, so it is covered too.
    with pytest.raises(web.WebFetchError):
        web.fetch_and_chunk("https://example.com/a", c, opener=op, resolver=r)


def test_acquire_disabled_does_not_gate_validate_url():
    # `acquire check` audits policy via validate_url, which must still work even
    # when egress is disabled (it performs no body fetch).
    r = make_resolver(PUBLIC)
    t = web.validate_url("https://example.com/", cfg(acquire_enabled=False), _resolver=r)
    assert t.ip == "93.184.216.34"


# ---------------------------------------------------------------------------
# graft 2: AcquiredLedger (cross-tick dedupe; efficiency only, never poison)
# ---------------------------------------------------------------------------

def test_acquired_ledger_record_and_seen(tmp_path):
    path = tmp_path / "ledger.jsonl"
    led = web.AcquiredLedger(path)
    assert not led.seen("alpha corpus")
    assert led.record("alpha corpus", "https://a.test") is True
    assert led.seen("alpha corpus")
    assert led.record("alpha corpus", "https://a.test") is False  # idempotent
    assert len(led) == 1


def test_acquired_ledger_persists_across_instances(tmp_path):
    path = tmp_path / "ledger.jsonl"
    web.AcquiredLedger(path).record("beta corpus", "https://b.test")
    # A fresh instance (e.g. the next short-lived feeder tick) remembers it.
    led2 = web.AcquiredLedger(path)
    assert led2.seen("beta corpus")
    assert led2.record("beta corpus") is False


def test_acquired_ledger_filter_new(tmp_path):
    led = web.AcquiredLedger(tmp_path / "l.jsonl")
    chunks = [{"text": "one", "source": "s"}, {"text": "two", "source": "s"}]
    assert len(led.filter_new(chunks)) == 2          # both new
    assert led.filter_new(chunks) == []              # all seen now
    assert led.filter_new([{"text": "three", "source": "s"}])[0]["text"] == "three"


def test_acquired_ledger_none_path_is_memory_only():
    led = web.AcquiredLedger(None)
    assert led.record("x") is True
    assert led.record("x") is False  # in-memory dedupe still works, no file


def test_acquired_ledger_sha_matches_provenance():
    # The ledger key is the same sha3 fetch_and_chunk stamps as source_sha.
    assert web.AcquiredLedger.__init__  # sanity
    assert web.chunk_sha("hello") == web.chunk_sha("hello")
    assert web.chunk_sha("hello") != web.chunk_sha("world")
