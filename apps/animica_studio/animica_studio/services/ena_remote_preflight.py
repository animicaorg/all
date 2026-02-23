from __future__ import annotations

import socket
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import requests

PreflightErrorKind = Literal["DNS", "HTTP", "TIMEOUT", "INVALID_URL", ""]


@dataclass
class PreflightResult:
    ok: bool
    endpoint: str
    resolved_ips: list[str] = field(default_factory=list)
    http_status: int | None = None
    error_kind: PreflightErrorKind = ""
    message: str = ""
    checked_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "endpoint": self.endpoint,
            "resolved_ips": list(self.resolved_ips),
            "http_status": self.http_status,
            "error_kind": self.error_kind,
            "message": self.message,
            "checked_url": self.checked_url,
        }


class ServicesPreflight:
    @staticmethod
    def check(url: str, *, connect_timeout_s: float = 3.0, total_timeout_s: float = 5.0) -> PreflightResult:
        endpoint = (url or "").strip()
        parsed = urlparse(endpoint)
        if not endpoint or parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return PreflightResult(
                ok=False,
                endpoint=endpoint,
                error_kind="INVALID_URL",
                message="services_url must be a valid http(s) URL with a hostname.",
            )

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        host = parsed.hostname
        result = PreflightResult(ok=False, endpoint=endpoint)

        default_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(connect_timeout_s)
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            result.resolved_ips = sorted({info[4][0] for info in infos if info and info[4]})
        except socket.timeout:
            result.error_kind = "TIMEOUT"
            result.message = f"DNS resolution timed out for '{host}'."
            return result
        except OSError as exc:
            result.error_kind = "DNS"
            result.message = f"DNS resolution failed for '{host}': {exc}"
            return result
        finally:
            socket.setdefaulttimeout(default_timeout)

        base = endpoint.rstrip("/") + "/"
        for candidate in [urljoin(base, "health"), endpoint]:
            try:
                resp = requests.get(candidate, timeout=(connect_timeout_s, total_timeout_s), allow_redirects=True)
                result.checked_url = candidate
                result.http_status = int(resp.status_code)
                if resp.status_code < 500:
                    result.ok = True
                    result.error_kind = ""
                    result.message = f"Remote services reachable at {candidate} (HTTP {resp.status_code})."
                    return result
                result.error_kind = "HTTP"
                result.message = f"HTTP {resp.status_code} returned by {candidate}."
            except requests.Timeout:
                result.checked_url = candidate
                result.error_kind = "TIMEOUT"
                result.message = f"HTTP timeout while contacting {candidate}."
            except requests.RequestException as exc:
                result.checked_url = candidate
                result.error_kind = "HTTP"
                result.message = f"HTTP request failed for {candidate}: {exc}"

        return result


def run_remote_preflight(services_url: str, *, connect_timeout_s: float = 3.0, total_timeout_s: float = 5.0) -> PreflightResult:
    return ServicesPreflight.check(services_url, connect_timeout_s=connect_timeout_s, total_timeout_s=total_timeout_s)
