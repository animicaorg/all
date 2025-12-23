from __future__ import annotations

import ipaddress
import os
import socket
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

    if _running_in_container():
        docker_hosts = _docker_host_candidates()
        for candidate_host in docker_hosts:
            if candidate_host not in hosts:
                hosts.append(candidate_host)

    candidates: list[str] = []
    for candidate_host in hosts:
        netloc = _build_netloc(parsed, candidate_host)
        candidate = urlunparse(parsed._replace(netloc=netloc))
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _running_in_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return False
    return any(token in content for token in ("docker", "containerd", "kubepods"))


def _default_gateway_ip() -> str | None:
    try:
        with open("/proc/net/route", "r", encoding="utf-8") as handle:
            lines = handle.readlines()[1:]
    except OSError:
        return None

    for line in lines:
        fields = line.strip().split()
        if len(fields) < 3:
            continue
        destination, gateway = fields[1], fields[2]
        if destination != "00000000":
            continue
        try:
            gateway_bytes = bytes.fromhex(gateway)
            ip = socket.inet_ntoa(gateway_bytes[::-1])
        except (ValueError, OSError):
            continue
        if ip and ip != "0.0.0.0":
            return ip
    return None


def _docker_host_candidates() -> list[str]:
    candidates: list[str] = []
    explicit = os.getenv("ANIMICA_DOCKER_HOST")
    if explicit:
        cleaned = explicit.strip()
        if cleaned:
            candidates.append(cleaned)

    candidates.extend(["host.docker.internal", "gateway.docker.internal"])

    gateway = _default_gateway_ip()
    if gateway:
        candidates.append(gateway)
    return candidates


def is_local_rpc_url(url: str) -> bool:
    if not url:
        return False

    normalized = url
    if "://" not in normalized:
        normalized = f"http://{normalized}"

    parsed = urlparse(normalized)
    host = parsed.hostname
    if not host:
        return False

    host_key = host.lower()
    if host_key == "localhost":
        return True

    try:
        ip_obj = ipaddress.ip_address(host_key)
    except ValueError:
        return False

    return bool(ip_obj.is_loopback or ip_obj.is_unspecified)


def is_method_not_found(exc: Exception) -> bool:
    msg = str(exc).lower()
    if "method not found" in msg:
        return True
    if "not found" in msg and "-32601" in msg:
        return True
    return "method" in msg and "not found" in msg
