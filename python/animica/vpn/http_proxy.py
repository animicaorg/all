"""
Authenticated HTTP CONNECT proxy for the dVPN browser-proxy companion.

The MV3 extension's location picker points ``chrome.proxy`` at an exit's HTTP proxy; this
is that proxy. It is deliberately minimal and enforces the SAME egress policy as the
WireGuard exit ACL: it refuses to connect to private / loopback / link-local / cloud-metadata
addresses or abuse ports, so browser-proxy traffic can't reach a LAN or 169.254.169.254.

It is a BROWSER PROXY, not a system VPN — it only carries traffic from a browser configured
to use it. Auth is HTTP Basic with a per-exit token so it is never an open proxy.

Stdlib only. TLS to the origin is end-to-end (CONNECT tunnels raw bytes); the proxy never
terminates TLS or inspects HTTPS payloads.

Security invariants (each has a regression test in tests/test_vpn_unit.py):
  * DNS is resolved EXACTLY ONCE per request and the connection is pinned to a validated
    IP literal — never re-resolved. This closes the DNS-rebind TOCTOU where a TTL=0 record
    passes the ACL as a public IP and then rebinds to 127.0.0.1 for the actual connect.
  * The destination ACL unwraps IPv4-mapped / 6to4 / Teredo IPv6 so an embedded loopback
    (e.g. ::ffff:127.0.0.1) cannot slip past on Python versions where is_private misses it.
  * The proxy token is compared in constant time.
  * Any rejection closes the connection, so an undrained request body can never be
    re-parsed as a pipelined request (request-smuggling defence).
  * Request and upstream-response bodies are capped to bound memory.
"""

from __future__ import annotations

import base64
import hmac
import http.client
import ipaddress
import select
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional, Tuple

BLOCKED_PORTS = {25, 465, 587, 137, 138, 139, 445, 3389, 5353}

# Ranges the stdlib ip.is_private / is_reserved MISS on some Python versions but that must
# never be reachable through an exit relay (they can front internal infra or be used for
# SSRF). Keep this in lock-step with the nftables exit ACL so the two controls agree.
_EXTRA_BLOCKED = [
    ipaddress.ip_network("100.64.0.0/10"),    # RFC 6598 CGNAT (is_private=False on 3.12)
    ipaddress.ip_network("192.88.99.0/24"),   # 6to4 relay anycast (is_private=False)
    ipaddress.ip_network("198.18.0.0/15"),    # benchmarking
    ipaddress.ip_network("::ffff:0:0/96"),    # IPv4-mapped space (unwrapped separately too)
    ipaddress.ip_network("64:ff9b::/96"),     # NAT64 well-known prefix
]

# Hop-by-hop headers must not be forwarded end-to-end (RFC 7230 §6.1).
_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "proxy-connection"}

# Bound memory for the absolute-form plain-HTTP path (CONNECT tunnels are streamed, uncapped).
MAX_REQUEST_BODY = 8 * 1024 * 1024
MAX_RESPONSE_BODY = 16 * 1024 * 1024


def _canonical_ip(sa0: str) -> ipaddress._BaseAddress:
    """Normalise a sockaddr address to the tightest form for ACL checks.

    getaddrinfo can hand back an IPv6 wrapper around an IPv4 address (::ffff:a.b.c.d,
    2002::/16 6to4, 2001::/32 Teredo). We must classify the *embedded* IPv4 so an
    attacker cannot smuggle a loopback/private target through the IPv6 wrapper on a
    Python whose is_private/is_loopback does not unwrap it.
    """
    ip = ipaddress.ip_address(sa0)
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip.teredo is not None:
            embedded = ip.teredo[1]   # the tunnelled client's IPv4
        if embedded is not None:
            return embedded
    return ip


def _ip_is_blocked(ip: ipaddress._BaseAddress) -> bool:
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        return True
    for net in _EXTRA_BLOCKED:
        if ip.version == net.version and ip in net:
            return True
    return False


def _resolve_safe(host: str, port: int) -> Tuple[Optional[List[tuple]], Optional[str]]:
    """Resolve ONCE and validate. Returns (candidate connect-targets, error).

    Each candidate is a (family, sockaddr) whose address is an already-validated IP
    literal, so the subsequent connect never re-resolves the hostname. If ANY resolved
    address is blocked we refuse the whole host — a split public/loopback record set is
    exactly how a rebind attack is staged.
    """
    if port in BLOCKED_PORTS or 6881 <= port <= 6889:
        return None, f"port {port} blocked by egress policy"
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return None, f"cannot resolve {host}"
    candidates: List[tuple] = []
    for family, _stype, _proto, _canon, sa in infos:
        ip = _canonical_ip(sa[0])
        if _ip_is_blocked(ip):
            return None, f"destination {ip} is a private/reserved address (blocked)"
        candidates.append((family, sa))
    if not candidates:
        return None, f"cannot resolve {host}"
    return candidates, None


def _connect_pinned(candidates: List[tuple], timeout: float) -> socket.socket:
    """Connect to one of the pre-validated sockaddrs. No hostname is passed anywhere here,
    so there is no second DNS lookup to race."""
    last: Optional[OSError] = None
    for family, sa in candidates:
        try:
            s = socket.socket(family, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect(sa)
            return s
        except OSError as e:
            last = e
            try:
                s.close()
            except Exception:
                pass
    raise last or OSError("no route to validated destination")


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "animica-dvpn-proxy/1.1"
    proxy_token = ""   # set by make_server
    # Bound how long a single unauthenticated client can hold a worker thread. Without this
    # a slowloris client (open socket, dribble headers) pins a thread forever; ThreadingHTTP
    # gives each connection a thread, so a handful of idle sockets would exhaust the exit host.
    timeout = 30

    def _auth_ok(self) -> bool:
        if not self.proxy_token:
            return True
        hdr = self.headers.get("Proxy-Authorization", "")
        if not hdr.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(hdr[6:]).decode().partition(":")
        except Exception:
            return False
        tok = self.proxy_token
        # Constant-time; OR of two compares still short-circuits in Python but each
        # compare_digest is itself constant-time over its inputs.
        return hmac.compare_digest(pw, tok) or hmac.compare_digest(user, tok)

    def _reject(self, code: int, msg: str, *, auth: bool = False) -> None:
        # Kill the connection on every rejection: a rejected request may have an
        # undrained body, and leaving the socket open would let those bytes be
        # re-parsed as a pipelined request (request-smuggling).
        self.close_connection = True
        self.send_response(code, msg)
        if auth:
            self.send_header("Proxy-Authenticate", 'Basic realm="animica-dvpn"')
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    def do_CONNECT(self):  # noqa: N802
        if not self._auth_ok():
            return self._reject(407, "Proxy Authentication Required", auth=True)
        try:
            host, _, port_s = self.path.rpartition(":")
            port = int(port_s)
        except ValueError:
            return self._reject(400, "bad CONNECT target")
        candidates, blocked = _resolve_safe(host, port)
        if blocked:
            return self._reject(403, blocked)
        try:
            upstream = _connect_pinned(candidates, timeout=15)
        except OSError as e:
            return self._reject(502, f"upstream connect failed: {e}")
        self.send_response(200, "Connection Established")
        self.end_headers()
        self._pump(self.connection, upstream)

    def _forward_plain(self) -> None:
        """Forward an absolute-form plain-HTTP request (browsers send these when proxying http://)."""
        if not self._auth_ok():
            return self._reject(407, "Proxy Authentication Required", auth=True)
        self.close_connection = True  # one request per connection keeps framing simple + correct
        parts = urllib.parse.urlsplit(self.path)
        if parts.scheme != "http" or not parts.hostname:
            return self._reject(400, "only absolute http:// requests are proxied (use CONNECT for https)")
        port = parts.port or 80
        candidates, blocked = _resolve_safe(parts.hostname, port)
        if blocked:
            return self._reject(403, blocked)
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_REQUEST_BODY:
            return self._reject(413, "request body too large")
        body = self.rfile.read(length) if length else None
        out_headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP_BY_HOP}
        out_headers["Connection"] = "close"
        rel = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        conn = None
        try:
            # Pin to the validated IP; carry the original Host header so name-based vhosts still work.
            family, sa = candidates[0]
            conn = http.client.HTTPConnection(sa[0], port, timeout=20)
            out_headers.setdefault("Host", parts.netloc)
            conn.request(self.command, rel, body=body, headers=out_headers)
            resp = conn.getresponse()
            payload = resp.read(MAX_RESPONSE_BODY + 1)
        except OSError as e:
            return self._reject(502, f"upstream request failed: {e}")
        finally:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
        if len(payload) > MAX_RESPONSE_BODY:
            return self._reject(502, "upstream response too large")
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() in _HOP_BY_HOP or k.lower() == "content-length":
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    do_GET = _forward_plain
    do_POST = _forward_plain
    do_HEAD = _forward_plain
    do_PUT = _forward_plain
    do_DELETE = _forward_plain
    do_OPTIONS = _forward_plain
    do_PATCH = _forward_plain

    def _pump(self, a: socket.socket, b: socket.socket) -> None:
        a.setblocking(False)
        b.setblocking(False)
        try:
            while True:
                r, _, x = select.select([a, b], [], [a, b], 60)
                if x or not r:
                    break
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        except OSError:
            pass
        finally:
            for s in (a, b):
                try:
                    s.close()
                except OSError:
                    pass

    def log_message(self, *_args):  # silence; never log destinations (privacy)
        pass


def make_server(host: str, port: int, token: str) -> ThreadingHTTPServer:
    handler = type("_H", (_Handler,), {"proxy_token": token})
    return ThreadingHTTPServer((host, port), handler)


def serve_in_thread(host: str, port: int, token: str) -> tuple[ThreadingHTTPServer, threading.Thread]:
    srv = make_server(host, port, token)
    t = threading.Thread(target=srv.serve_forever, name="anmvpn-httpproxy", daemon=True)
    t.start()
    return srv, t
