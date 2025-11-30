#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_ingress_values.py — Generate a Helm values snippet for Kubernetes Ingress
hosts based on an .env file.

It reads domains and service endpoints (name/port) from the .env and prints a
YAML values block to stdout (or to --out). Designed to work with the variables
in ops/.env.example (ROOT/BASE domain, per-service domains, issuer, ingress class).

Usage:
  python ops/scripts/render_ingress_values.py --env ops/.env --out ops/ingress.values.yaml
  # or just print to stdout:
  python ops/scripts/render_ingress_values.py --env ops/.env

Environment keys recognized (case-insensitive for service IDs):
  # Global
  ROOT_DOMAIN / BASE_DOMAIN              # e.g. "dev.animica.local" or "animica.dev"
  INGRESS_CLASS                          # e.g. "nginx"
  CERT_CLUSTER_ISSUER                    # e.g. "letsencrypt-prod"
  TLS_ENABLED                            # "true" | "false" (default: true)
  NAMESPACE                              # for comments (not used in values)

  # Per service (ID ∈ {RPC, WS, EXPLORER, SERVICES, DA, AICF, METRICS} by default)
  DOMAIN_<ID>                            # FQDN. If missing and ROOT/BASE provided, uses <id>.<root>
  SERVICE_<ID>                           # K8s Service name (default: "animica-<id-lower>")
  PORT_<ID>                              # Service port (int). Defaults below.
  PATH_<ID>                              # Ingress path (default: "/")
  TLS_SECRET_<ID>                        # Optional explicit secretName (overrides issuer for that host)

Defaults:
  PORT_RPC=8545, PORT_WS=8546, PORT_EXPLORER=80, PORT_SERVICES=8080,
  PORT_DA=8081, PORT_AICF=8080, PORT_METRICS=9090
  INGRESS_CLASS="nginx", CERT_CLUSTER_ISSUER="letsencrypt-prod", TLS_ENABLED=true

Output shape:
  ingress:
    className: <INGRESS_CLASS>
    tls:
      enabled: <TLS_ENABLED>
      clusterIssuer: <CERT_CLUSTER_ISSUER>
    hosts:
      - name: rpc
        host: rpc.example.com
        path: /
        service:
          name: animica-rpc
          port: 8545
        tlsSecretName: ""          # omitted if empty
      - name: explorer
        host: explorer.example.com
        path: /
        service:
          name: animica-explorer
          port: 80

Notes:
- No external dependencies (no PyYAML). We emit clean YAML by hand.
- Unknown DOMAIN_* entries are also supported; provide matching SERVICE_*/PORT_* to include them.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Built-in/default services (ID -> sensible defaults)
DEFAULT_SERVICES: Dict[str, Tuple[str, int, str]] = {
    "RPC": ("animica-rpc", 8545, "/"),
    "WS": ("animica-ws", 8546, "/"),
    "EXPLORER": ("animica-explorer", 80, "/"),
    "SERVICES": ("animica-services", 8080, "/"),
    "DA": ("animica-da", 8081, "/"),
    "AICF": ("animica-aicf", 8080, "/"),
    "METRICS": ("animica-metrics", 9090, "/"),
}

ENV_BOOL_TRUE = {"1", "true", "yes", "on", "y", "t"}
ENV_BOOL_FALSE = {"0", "false", "no", "off", "n", "f"}


def parse_env_file(path: str) -> Dict[str, str]:
    """
    Minimal .env parser (supports comments, quoted values, export prefix).
    Doesn't expand references; we leave that to the caller if desired.
    """
    env: Dict[str, str] = {}
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            env[key] = val
    return env


def env_get_bool(env: Dict[str, str], key: str, default: bool) -> bool:
    v = env.get(key)
    if v is None:
        return default
    s = v.strip().lower()
    if s in ENV_BOOL_TRUE:
        return True
    if s in ENV_BOOL_FALSE:
        return False
    return default


def discover_ids(env: Dict[str, str]) -> List[str]:
    """
    Discover service IDs from DOMAIN_* keys, merged with defaults.
    """
    ids = set(DEFAULT_SERVICES.keys())
    for k in env.keys():
        m = re.match(r"^DOMAIN_([A-Za-z0-9_]+)$", k)
        if m:
            ids.add(m.group(1).upper())
    return sorted(ids)


def build_host_for_id(
    service_id: str, env: Dict[str, str], root_domain: Optional[str]
) -> Optional[str]:
    # Prefer explicit DOMAIN_<ID>
    explicit = env.get(f"DOMAIN_{service_id}")
    if explicit:
        return explicit.strip()
    # Otherwise derive from ROOT/BASE if available
    if root_domain:
        sub = service_id.lower().replace("_", "-")
        return f"{sub}.{root_domain}"
    return None  # host missing; we will skip this entry


def gather_entry(
    service_id: str, env: Dict[str, str], root_domain: Optional[str]
) -> Optional[Dict[str, object]]:
    host = build_host_for_id(service_id, env, root_domain)
    if not host:
        return None

    # Defaults
    def_svc, def_port, def_path = DEFAULT_SERVICES.get(
        service_id, (f"animica-{service_id.lower()}", 80, "/")
    )

    name = env.get(f"SERVICE_{service_id}", def_svc).strip()
    port_raw = env.get(f"PORT_{service_id}")
    path = env.get(f"PATH_{service_id}", def_path).strip() or "/"
    tls_secret = env.get(f"TLS_SECRET_{service_id}", "").strip()

    try:
        port = int(port_raw) if port_raw is not None else def_port
    except ValueError:
        port = def_port

    return {
        "id": service_id.lower(),
        "host": host,
        "path": path if path.startswith("/") else f"/{path}",
        "service_name": name,
        "service_port": port,
        "tls_secret": tls_secret or None,
    }


def render_yaml(block: Dict[str, object]) -> str:
    """
    Hand-roll a small YAML for:
    ingress:
      className: ...
      tls:
        enabled: true
        clusterIssuer: ...
      hosts:
        - name: ...
          host: ...
          path: ...
          service:
            name: ...
            port: ...
          tlsSecretName: ...
    """

    def esc(s: str) -> str:
        # Quote if contains special chars
        if s == "" or any(
            ch in s for ch in [":", "#", "{", "}", "[", "]", ",", "&", "*", " ", "\t"]
        ):
            return f'"{s}"'
        return s

    lines: List[str] = []
    lines.append(
        "# Generated by ops/scripts/render_ingress_values.py on "
        + datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    lines.append("ingress:")
    lines.append(f"  className: {esc(str(block['className']))}")
    lines.append("  tls:")
    lines.append(f"    enabled: {'true' if block['tlsEnabled'] else 'false'}")
    issuer = block.get("clusterIssuer")
    if issuer:
        lines.append(f"    clusterIssuer: {esc(str(issuer))}")
    lines.append("  hosts:")
    for h in block["hosts"]:  # type: ignore[index]
        lines.append("    - name: " + esc(h["name"]))  # type: ignore[index]
        lines.append("      host: " + esc(h["host"]))  # type: ignore[index]
        lines.append("      path: " + esc(h["path"]))  # type: ignore[index]
        lines.append("      service:")
        lines.append("        name: " + esc(h["service"]["name"]))  # type: ignore[index]
        lines.append("        port: " + str(h["service"]["port"]))  # type: ignore[index]
        tls_secret = h.get("tlsSecretName")  # type: ignore[assignment]
        if tls_secret:
            lines.append("      tlsSecretName: " + esc(str(tls_secret)))
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render Helm ingress values from .env")
    ap.add_argument(
        "--env", default="ops/.env", help="Path to .env (default: ops/.env)"
    )
    ap.add_argument(
        "--out", default="", help="Write YAML to this file (default: stdout)"
    )
    args = ap.parse_args()

    try:
        env = parse_env_file(args.env)
    except Exception as exc:
        print(f"ERROR: cannot read env file: {exc}", file=sys.stderr)
        return 2

    # Prefer ROOT_DOMAIN; fall back to BASE_DOMAIN if present
    root = env.get("ROOT_DOMAIN") or env.get("BASE_DOMAIN")
    ingress_class = env.get("INGRESS_CLASS", "nginx")
    issuer = env.get("CERT_CLUSTER_ISSUER", "letsencrypt-prod")
    tls_enabled = env_get_bool(env, "TLS_ENABLED", True)

    ids = discover_ids(env)

    hosts: List[Dict[str, object]] = []
    for sid in ids:
        entry = gather_entry(sid, env, root)
        if not entry:
            continue
        hosts.append(
            {
                "name": entry["id"],  # e.g. "rpc"
                "host": entry["host"],
                "path": entry["path"],
                "service": {
                    "name": entry["service_name"],
                    "port": entry["service_port"],
                },
                **(
                    {"tlsSecretName": entry["tls_secret"]}
                    if entry["tls_secret"]
                    else {}
                ),
            }
        )

    if not hosts:
        print(
            "ERROR: no hosts discovered (set ROOT_DOMAIN/BASE_DOMAIN or DOMAIN_* entries).",
            file=sys.stderr,
        )
        return 3

    values_block: Dict[str, object] = {
        "className": ingress_class,
        "tlsEnabled": tls_enabled,
        "clusterIssuer": issuer if tls_enabled else "",
        "hosts": hosts,
    }

    yaml = render_yaml(values_block)

    if args.out:
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(yaml)
        print(f"Wrote ingress values → {args.out}")
    else:
        sys.stdout.write(yaml)

    return 0


if __name__ == "__main__":
    sys.exit(main())
