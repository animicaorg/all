from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def _build_netloc(parsed, host: str) -> str:
    userinfo = ""
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo += f":{parsed.password}"
        userinfo += "@"

    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    netloc = f"{userinfo}{host}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return netloc


def candidate_rpc_urls(url: str) -> list[str]:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return [url]

    host_key = host.lower()
    hosts: list[str]
    if host_key == "localhost":
        hosts = ["localhost", "127.0.0.1", "::1"]
    elif host_key == "127.0.0.1":
        hosts = ["127.0.0.1", "::1"]
    elif host_key == "::1":
        hosts = ["::1", "127.0.0.1"]
    else:
        return [url]

    candidates: list[str] = []
    for candidate_host in hosts:
        netloc = _build_netloc(parsed, candidate_host)
        candidate = urlunparse(parsed._replace(netloc=netloc))
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def is_method_not_found(exc: Exception) -> bool:
    msg = str(exc).lower()
    if "method not found" in msg:
        return True
    if "not found" in msg and "-32601" in msg:
        return True
    return "method" in msg and "not found" in msg
