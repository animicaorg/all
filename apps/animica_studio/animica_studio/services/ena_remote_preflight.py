from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urljoin

import requests


@dataclass
class RemotePreflightResult:
    ok: bool
    endpoint: str
    host: str
    port: int
    resolved_ips: list[str] = field(default_factory=list)
    health_url: str = ""
    http_status: int | None = None
    http_excerpt: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "endpoint": self.endpoint,
            "host": self.host,
            "port": self.port,
            "resolved_ips": list(self.resolved_ips),
            "health_url": self.health_url,
            "http_status": self.http_status,
            "http_excerpt": self.http_excerpt,
            "error": self.error,
        }


def _hostname_and_port(parsed: Any) -> tuple[str, int]:
    if not parsed.hostname:
        raise ValueError("services_url must include a hostname")
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.hostname, int(port)


def run_remote_preflight(services_url: str, *, connect_timeout_s: float = 3.0, total_timeout_s: float = 5.0) -> RemotePreflightResult:
    endpoint = (services_url or "").strip()
    if not endpoint:
        raise ValueError("Remote training requires a services_url. Switch to Local mode or set a valid URL.")

    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("services_url must use http:// or https://")
    host, port = _hostname_and_port(parsed)

    result = RemotePreflightResult(ok=False, endpoint=endpoint, host=host, port=port)

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = sorted({info[4][0] for info in infos if info and info[4]})
        result.resolved_ips = ips
    except OSError as exc:
        result.error = f"DNS resolution failed for '{host}': {exc}"
        return result

    health_candidates = [
        urljoin(endpoint.rstrip("/") + "/", "health"),
        urljoin(endpoint.rstrip("/") + "/", "v1/health"),
        endpoint,
    ]

    for url in health_candidates:
        try:
            response = requests.get(url, timeout=(connect_timeout_s, total_timeout_s), allow_redirects=True)
        except requests.RequestException as exc:
            result.health_url = url
            result.error = f"HTTP check failed for {url}: {exc}"
            continue

        result.health_url = url
        result.http_status = int(response.status_code)
        result.http_excerpt = (response.text or "")[:300]
        if 200 <= response.status_code <= 499:
            result.ok = True
            result.error = ""
            return result
        result.error = f"HTTP status {response.status_code} from {url}"

    return result
