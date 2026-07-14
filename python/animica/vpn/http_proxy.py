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
"""

from __future__ import annotations

import base64
import http.client
import ipaddress
import select
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

BLOCKED_PORTS = {25, 465, 587, 137, 138, 139, 445, 3389, 5353}

# Hop-by-hop headers must not be forwarded end-to-end (RFC 7230 §6.1).
_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailer", "transfer-encoding", "upgrade", "proxy-connection"}


def _is_blocked_dest(host: str, port: int) -> Optional[str]:
    if port in BLOCKED_PORTS or 6881 <= port <= 6889:
        return f"port {port} blocked by egress policy"
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return f"cannot resolve {host}"
    for *_, sa in infos:
        ip = ipaddress.ip_address(sa[0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            return f"destination {ip} is a private/reserved address (blocked)"
    return None


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "animica-dvpn-proxy/1.0"
    proxy_token = ""   # set by make_server

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
        return pw == self.proxy_token or user == self.proxy_token

    def _reject(self, code: int, msg: str, *, auth: bool = False) -> None:
        self.send_response(code, msg)
        if auth:
            self.send_header("Proxy-Authenticate", 'Basic realm="animica-dvpn"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_CONNECT(self):  # noqa: N802
        if not self._auth_ok():
            return self._reject(407, "Proxy Authentication Required", auth=True)
        try:
            host, _, port_s = self.path.rpartition(":")
            port = int(port_s)
        except ValueError:
            return self._reject(400, "bad CONNECT target")
        blocked = _is_blocked_dest(host, port)
        if blocked:
            return self._reject(403, blocked)
        try:
            upstream = socket.create_connection((host, port), timeout=15)
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
        blocked = _is_blocked_dest(parts.hostname, port)
        if blocked:
            return self._reject(403, blocked)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        out_headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP_BY_HOP}
        out_headers["Connection"] = "close"
        rel = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
        try:
            conn = http.client.HTTPConnection(parts.hostname, port, timeout=20)
            conn.request(self.command, rel, body=body, headers=out_headers)
            resp = conn.getresponse()
            payload = resp.read()
        except OSError as e:
            return self._reject(502, f"upstream request failed: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
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
