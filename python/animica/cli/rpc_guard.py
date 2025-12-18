from __future__ import annotations

import os
from urllib.parse import urlparse

import typer

from animica.config import load_network_config

_RISK_ENV = "ANIMICA_I_UNDERSTAND_REMOTE_RISK"
_BOOTSTRAP_SAFE_METHODS = {
    "bootstrap.getManifest",
    "bootstrap.getSeeds",
    "bootstrap.getPeers",
    "bootstrap.getSnapshotManifest",
    "net.getBootstrapSeeds",
    "chain.getHead",
    "chain.getChainId",
}


def guard_bootstrap_rpc(
    rpc_url: str,
    *,
    allow_remote: bool = False,
    allow_bootstrap_methods: bool = False,
    method: str | None = None,
    bootstrap_url: str | None = None,
) -> None:
    if allow_bootstrap_methods and method and (
        method.startswith("bootstrap.") or method in _BOOTSTRAP_SAFE_METHODS
    ):
        # Caller explicitly allowed bootstrap-only methods; only guard if host is different.
        pass

    cfg = load_network_config()
    bootstrap = bootstrap_url or cfg.bootstrap_url
    target_host = urlparse(rpc_url).hostname
    bootstrap_host = urlparse(bootstrap).hostname if bootstrap else None

    if not target_host or not bootstrap_host:
        return False

    is_bootstrap = target_host == bootstrap_host
    if not is_bootstrap:
        return False

    if allow_bootstrap_methods and (method is None or method in _BOOTSTRAP_SAFE_METHODS or method.startswith("bootstrap.")):
        typer.secho(
            f"Using bootstrap RPC endpoint {target_host} for discovery/sync only.",
            fg=typer.colors.YELLOW,
        )
        return True

    if allow_remote:
        if os.getenv(_RISK_ENV) != "1":
            typer.secho(
                "Refusing to use bootstrap RPC without confirmation. "
                f"Set {_RISK_ENV}=1 to proceed with --allow-remote-rpc.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)

        typer.secho(
            "Warning: using bootstrap RPC for this command; prefer your local node.",
                fg=typer.colors.YELLOW,
            )
        return True

    host_hint = bootstrap_host or target_host
    typer.secho(
        f"{host_hint} is bootstrap-only. Run `animica node up` and use localhost RPC, "
        "or run `animica node bootstrap` first.",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)
