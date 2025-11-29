"""Stratum pool helper CLI for Animica developers."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer

from animica.config import load_network_config
from animica.cli.wallet import WalletEntry, create_wallet, _wallet_file_path
from animica.stratum_pool import cli as pool_cli
from animica.stratum_pool.config import PoolConfig, load_config_from_env

app = typer.Typer(help="Run and inspect the Animica Stratum pool.")

RPC_ENV = "ANIMICA_RPC_URL"
DB_ENV = "ANIMICA_MINING_POOL_DB_URL"
LOG_LEVEL_ENV = "ANIMICA_MINING_POOL_LOG_LEVEL"
STRATUM_BIND_ENV = "ANIMICA_STRATUM_BIND"
API_BIND_ENV = "ANIMICA_POOL_API_BIND"


def _ensure_network_env() -> None:
    cfg = load_network_config()
    os.environ.setdefault("ANIMICA_NETWORK", cfg.name)
    os.environ.setdefault(RPC_ENV, cfg.rpc_url)


@app.command("run-pool")
def run_pool(
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="Animica node RPC URL", envvar=RPC_ENV),
    db_url: Optional[str] = typer.Option(None, "--db-url", help="Database URL", envvar=DB_ENV),
    stratum_bind: Optional[str] = typer.Option(None, "--stratum-bind", help="Stratum bind address", envvar=STRATUM_BIND_ENV),
    api_bind: Optional[str] = typer.Option(None, "--api-bind", help="API bind address", envvar=API_BIND_ENV),
    log_level: Optional[str] = typer.Option(None, "--log-level", help="Log level", envvar=LOG_LEVEL_ENV),
) -> None:
    """Start the Animica Stratum mining pool."""
    _ensure_network_env()
    env_overrides = {
        RPC_ENV: rpc_url,
        DB_ENV: db_url,
        STRATUM_BIND_ENV: stratum_bind,
        API_BIND_ENV: api_bind,
        LOG_LEVEL_ENV: log_level,
    }
    for key, value in env_overrides.items():
        if value is not None:
            os.environ[key] = value
    pool_cli.main([])


@app.command("show-config")
def show_config() -> None:
    """Display the effective pool configuration."""
    _ensure_network_env()
    cfg: PoolConfig = load_config_from_env()
    typer.echo(
        f"RPC URL: {cfg.rpc_url}\n"
        f"DB URL: {cfg.db_url}\n"
        f"Chain ID: {cfg.chain_id}\n"
        f"Pool address: {cfg.pool_address}\n"
        f"Stratum bind: {cfg.host}:{cfg.port}\n"
        f"API bind: {cfg.api_host}:{cfg.api_port}\n"
        f"Log level: {cfg.log_level}"
    )


@app.command("generate-payout-address")
def generate_payout_address(
    wallet_file: Optional[Path] = typer.Option(None, "--wallet-file", help="Wallet store for generated address"),
    label: str = typer.Option("pool-payout", "--label", help="Label for the generated wallet"),
) -> None:
    """Generate a dev wallet for pool payouts using the wallet CLI helpers."""
    entry: WalletEntry = create_wallet(label, _wallet_file_path(wallet_file))
    typer.echo(f"Generated payout address {entry.address} (label: {entry.label})")


if __name__ == "__main__":  # pragma: no cover
    app()
