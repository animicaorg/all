"""
CLI commands for managing the PPLNS mining pool.

Commands:
- pool up: Start the pool
- pool down: Stop the pool
- pool status: Show pool status
- pool miners list: List miners
- pool miner stats: Show miner statistics
- pool blocks list: List found blocks
- pool payouts run: Execute payouts
- pool payouts pause/resume: Control payout engine
- pool payouts history: Show payout history
- pool db migrate: Run database migrations
- pool bans list: List banned IPs
- pool bans add: Ban an IP
- pool bans remove: Unban an IP
- pool bans clear-expired: Clear expired bans
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from .config import load_pool_config_from_env
from .db import PoolDatabase
from .payout_engine import PayoutEngine

app = typer.Typer(help="Manage PPLNS mining pool")


def _resolve_pool_pid_file() -> Path:
    """Resolve the PID file path for the pool."""
    home = Path.home()
    animica_home = home / ".animica"
    animica_home.mkdir(parents=True, exist_ok=True)
    return animica_home / "pool.pid"


def _parse_pid_file(pid_file: Path) -> dict:
    """Parse PID file content."""
    if not pid_file.exists():
        return {}
    
    content = pid_file.read_text(encoding="utf-8").strip()
    if not content:
        return {}
    
    data = {}
    for line in content.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "pid" and value.isdigit():
            data["pid"] = int(value)
        elif key == "port" and value.isdigit():
            data["port"] = int(value)
        elif key in ("bind", "db"):
            data[key] = value
    
    return data


def _is_process_running(pid: int) -> bool:
    """Check if a process is running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _write_pid_file(pid_file: Path, pid: int, bind: str, port: int, db: str) -> None:
    """Write PID file."""
    content = f"pid={pid}\nbind={bind}\nport={port}\ndb={db}\n"
    pid_file.write_text(content, encoding="utf-8")


def _remove_pid_file(pid_file: Path) -> None:
    """Remove PID file."""
    if pid_file.exists():
        pid_file.unlink(missing_ok=True)


def _get_pool_status(pid_file: Path) -> tuple[bool, Optional[dict]]:
    """Get pool status."""
    data = _parse_pid_file(pid_file)
    if not data or "pid" not in data:
        return False, None
    
    pid = data["pid"]
    if not _is_process_running(pid):
        _remove_pid_file(pid_file)
        return False, None
    
    return True, data


@app.command("up")
def pool_up(
    mode: str = typer.Option(
        "pplns",
        "--mode",
        help="Pool mode (pplns, pps, solo)",
    ),
    address: str = typer.Option(
        ...,
        "--address",
        help="Pool fee address (coinbase recipient)",
    ),
    bind: str = typer.Option(
        "127.0.0.1",
        "--bind",
        help="Bind address (default: localhost only)",
    ),
    port: int = typer.Option(
        3333,
        "--port",
        help="Stratum server port",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Node RPC URL",
    ),
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="Database path",
    ),
    pool_fee: float = typer.Option(
        1.0,
        "--pool-fee",
        help="Pool fee percentage (default: 1.0)",
    ),
    maturity_blocks: int = typer.Option(
        20,
        "--maturity-blocks",
        help="Block maturity confirmations",
    ),
    min_payout: float = typer.Option(
        1.0,
        "--min-payout",
        help="Minimum payout in ANM (default: 1.0)",
    ),
    payout_interval: int = typer.Option(
        600,
        "--payout-interval-sec",
        help="Payout interval in seconds (default: 600)",
    ),
    vardiff: bool = typer.Option(
        True,
        "--vardiff/--no-vardiff",
        help="Enable variable difficulty",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon",
        help="Run in background as daemon",
    ),
    log_file: Optional[str] = typer.Option(
        None,
        "--log-file",
        help="Log file path (daemon mode only)",
    ),
) -> None:
    """
    Start the PPLNS mining pool.
    
    Example:
        animica pool up --address anim1... --bind 0.0.0.0 --pool-fee 1.0
    """
    # Check if already running
    pid_file = _resolve_pool_pid_file()
    is_running, info = _get_pool_status(pid_file)
    
    if is_running:
        typer.echo(
            f"Pool is already running (PID {info['pid']}) on {info['bind']}:{info['port']}"
        )
        raise typer.Exit(1)
    
    # Convert min_payout to base units (1 ANM = 1e6 base units)
    min_payout_units = int(min_payout * 1_000_000)
    
    # Build command
    python_exe = sys.executable
    cmd = [
        python_exe,
        "-m",
        "animica.pool.server",
        "--mode", mode,
        "--address", address,
        "--bind", bind,
        "--port", str(port),
        "--pool-fee", str(pool_fee),
        "--maturity-blocks", str(maturity_blocks),
        "--min-payout", str(min_payout_units),
        "--payout-interval", str(payout_interval),
    ]
    
    if rpc_url:
        cmd.extend(["--rpc-url", rpc_url])
    
    if db_path:
        cmd.extend(["--db", db_path])
    else:
        db_path = "~/.animica/pool.db"
    
    if not vardiff:
        cmd.append("--no-vardiff")
    
    typer.echo(f"Starting pool on {bind}:{port}")
    typer.echo(f"Pool address: {address}")
    typer.echo(f"Mode: {mode}, Fee: {pool_fee}%, Min payout: {min_payout} ANM")
    
    if daemon:
        # Run in background
        home = Path.home()
        animica_home = home / ".animica"
        animica_home.mkdir(parents=True, exist_ok=True)
        
        if log_file is None:
            log_file = str(animica_home / "pool.log")
        
        typer.echo(f"Running in daemon mode, logs at: {log_file}")
        
        with open(log_file, "a") as f:
            process = subprocess.Popen(
                cmd,
                stdout=f,
                stderr=f,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        
        _write_pid_file(pid_file, process.pid, bind, port, db_path)
        
        time.sleep(1)
        
        if not _is_process_running(process.pid):
            typer.echo("Error: Pool failed to start. Check logs.", err=True)
            _remove_pid_file(pid_file)
            raise typer.Exit(1)
        
        typer.echo(f"Pool started (PID {process.pid})")
    else:
        # Run in foreground
        try:
            subprocess.run(cmd, check=True)
        except KeyboardInterrupt:
            typer.echo("\nPool stopped.")
        except subprocess.CalledProcessError as e:
            typer.echo(f"Error: Pool exited with code {e.returncode}", err=True)
            raise typer.Exit(e.returncode)


@app.command("down")
def pool_down() -> None:
    """Stop the mining pool."""
    pid_file = _resolve_pool_pid_file()
    is_running, info = _get_pool_status(pid_file)
    
    if not is_running:
        typer.echo("Pool is not running.")
        _remove_pid_file(pid_file)
        return
    
    pid = info["pid"]
    typer.echo(f"Stopping pool (PID {pid})...")
    
    try:
        os.kill(pid, signal.SIGTERM)
        
        for _ in range(100):
            if not _is_process_running(pid):
                break
            time.sleep(0.1)
        else:
            typer.echo("Process did not stop gracefully, forcing...")
            try:
                os.kill(pid, signal.SIGKILL)
                time.sleep(0.5)
            except (OSError, ProcessLookupError):
                pass
        
        _remove_pid_file(pid_file)
        
        if not _is_process_running(pid):
            typer.echo("Pool stopped successfully.")
        else:
            typer.echo("Warning: Process may still be running.", err=True)
    
    except (OSError, ProcessLookupError) as e:
        typer.echo(f"Error stopping process: {e}", err=True)
        _remove_pid_file(pid_file)
        raise typer.Exit(1)


@app.command("status")
def pool_status(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output status in JSON format",
    ),
) -> None:
    """Show pool status and statistics."""
    pid_file = _resolve_pool_pid_file()
    is_running, info = _get_pool_status(pid_file)
    
    if not is_running:
        if json_output:
            typer.echo(json.dumps({"running": False}))
        else:
            typer.echo("Pool is not running.")
        return
    
    # Try to get stats from database
    db_path = info.get("db", "~/.animica/pool.db")
    db_path = os.path.expanduser(db_path)
    
    status_data = {
        "running": True,
        "pid": info["pid"],
        "bind": info.get("bind", "unknown"),
        "port": info.get("port", "unknown"),
    }
    
    try:
        db = PoolDatabase(db_path)
        db.connect()
        
        # Get basic stats
        miners_count = db.fetchone("SELECT COUNT(*) as count FROM miners")["count"] or 0
        shares_count = db.fetchone("SELECT COUNT(*) as count FROM shares")["count"] or 0
        blocks_count = db.fetchone("SELECT COUNT(*) as count FROM blocks")["count"] or 0
        
        status_data.update({
            "miners": miners_count,
            "shares": shares_count,
            "blocks": blocks_count,
        })
        
        db.close()
    except Exception as e:  # noqa: BLE001
        status_data["db_error"] = str(e)
    
    if json_output:
        typer.echo(json.dumps(status_data, indent=2))
    else:
        typer.echo("Pool Status:")
        typer.echo(f"  Running: Yes")
        typer.echo(f"  PID: {info['pid']}")
        typer.echo(f"  Bind: {info.get('bind', 'unknown')}:{info.get('port', 'unknown')}")
        if "miners" in status_data:
            typer.echo(f"  Miners: {status_data['miners']}")
            typer.echo(f"  Shares: {status_data['shares']}")
            typer.echo(f"  Blocks: {status_data['blocks']}")


@app.command("miners")
def miners_list(
    limit: int = typer.Option(
        50,
        "--limit",
        help="Maximum number of miners to show",
    ),
) -> None:
    """List active miners."""
    typer.echo("Not yet implemented")


@app.command("miner")
def miner_stats(
    address: Optional[str] = typer.Option(
        None,
        "--address",
        help="Miner payout address",
    ),
    miner_id: Optional[str] = typer.Option(
        None,
        "--id",
        help="Miner ID",
    ),
) -> None:
    """Show miner statistics."""
    if not address and not miner_id:
        typer.echo("Error: Must specify --address or --id", err=True)
        raise typer.Exit(1)
    
    typer.echo("Not yet implemented")


@app.command("blocks")
def blocks_list(
    limit: int = typer.Option(
        50,
        "--limit",
        help="Maximum number of blocks to show",
    ),
) -> None:
    """List found blocks."""
    typer.echo("Not yet implemented")


# Payouts subcommands
payouts_app = typer.Typer(help="Manage payouts")
app.add_typer(payouts_app, name="payouts")


@payouts_app.command("run")
def payouts_run(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be paid without executing",
    ),
) -> None:
    """Execute payouts for mature balances."""
    typer.echo("Not yet implemented")


@payouts_app.command("pause")
def payouts_pause() -> None:
    """Pause automatic payouts."""
    typer.echo("Not yet implemented")


@payouts_app.command("resume")
def payouts_resume() -> None:
    """Resume automatic payouts."""
    typer.echo("Not yet implemented")


@payouts_app.command("history")
def payouts_history(
    limit: int = typer.Option(
        50,
        "--limit",
        help="Number of recent payouts to show",
    ),
) -> None:
    """Show payout history."""
    typer.echo("Not yet implemented")


# Database subcommands
db_app = typer.Typer(help="Database management")
app.add_typer(db_app, name="db")


@db_app.command("migrate")
def db_migrate(
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="Database path",
    ),
) -> None:
    """Run database migrations."""
    if not db_path:
        db_path = os.path.expanduser("~/.animica/pool.db")
    
    typer.echo(f"Running migrations on {db_path}")
    
    try:
        db = PoolDatabase(db_path)
        db.connect()
        
        version = db.get_schema_version()
        typer.echo(f"Current schema version: {version}")
        
        # Migrations are auto-applied on connect
        typer.echo("Migrations complete!")
        
        db.close()
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


# Bans subcommands
bans_app = typer.Typer(help="Manage IP bans")
app.add_typer(bans_app, name="bans")


@bans_app.command("list")
def bans_list(
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="Database path",
    ),
    active_only: bool = typer.Option(
        True,
        "--active-only/--all",
        help="Show only active bans",
    ),
) -> None:
    """List banned IPs."""
    if not db_path:
        db_path = os.path.expanduser("~/.animica/pool.db")
    
    try:
        from animica.pool.abuse_manager import AbuseConfig, AbuseManager
        
        db = PoolDatabase(db_path)
        db.connect()
        
        config = AbuseConfig()
        manager = AbuseManager(db, config)
        
        bans = manager.list_bans(active_only=active_only)
        
        if not bans:
            typer.echo("No bans found.")
            db.close()
            return
        
        typer.echo(f"\nFound {len(bans)} ban(s):\n")
        for ban in bans:
            status = "Active" if ban.expires_at > datetime.utcnow() else "Expired"
            typer.echo(f"IP: {ban.ip}")
            typer.echo(f"  Status: {status}")
            typer.echo(f"  Reason: {ban.reason}")
            typer.echo(f"  Created: {ban.created_at}")
            typer.echo(f"  Expires: {ban.expires_at}")
            typer.echo(f"  Strikes: {ban.strike_count}")
            typer.echo()
        
        db.close()
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@bans_app.command("add")
def bans_add(
    ip: str = typer.Option(
        ...,
        "--ip",
        help="IP address to ban",
    ),
    minutes: int = typer.Option(
        60,
        "--minutes",
        help="Ban duration in minutes",
    ),
    reason: str = typer.Option(
        "Manual ban",
        "--reason",
        help="Ban reason",
    ),
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="Database path",
    ),
) -> None:
    """Manually ban an IP address."""
    if not db_path:
        db_path = os.path.expanduser("~/.animica/pool.db")
    
    try:
        from animica.pool.abuse_manager import AbuseConfig, AbuseManager
        
        db = PoolDatabase(db_path)
        db.connect()
        
        config = AbuseConfig()
        manager = AbuseManager(db, config)
        
        ban = manager.add_manual_ban(ip, minutes, reason)
        
        typer.echo(f"Successfully banned {ip} for {minutes} minutes")
        typer.echo(f"Reason: {reason}")
        typer.echo(f"Expires at: {ban.expires_at}")
        
        db.close()
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@bans_app.command("remove")
def bans_remove(
    ip: str = typer.Option(
        ...,
        "--ip",
        help="IP address to unban",
    ),
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="Database path",
    ),
) -> None:
    """Remove a ban for an IP address."""
    if not db_path:
        db_path = os.path.expanduser("~/.animica/pool.db")
    
    try:
        from animica.pool.abuse_manager import AbuseConfig, AbuseManager
        
        db = PoolDatabase(db_path)
        db.connect()
        
        config = AbuseConfig()
        manager = AbuseManager(db, config)
        
        removed = manager.remove_ban(ip)
        
        if removed:
            typer.echo(f"Successfully removed ban for {ip}")
        else:
            typer.echo(f"No active ban found for {ip}")
        
        db.close()
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@bans_app.command("clear-expired")
def bans_clear_expired(
    db_path: Optional[str] = typer.Option(
        None,
        "--db",
        help="Database path",
    ),
) -> None:
    """Clear expired bans from cache."""
    if not db_path:
        db_path = os.path.expanduser("~/.animica/pool.db")
    
    try:
        from animica.pool.abuse_manager import AbuseConfig, AbuseManager
        
        db = PoolDatabase(db_path)
        db.connect()
        
        config = AbuseConfig()
        manager = AbuseManager(db, config)
        
        cleared = manager.clear_expired_bans()
        
        typer.echo(f"Cleared {cleared} expired ban(s) from cache")
        
        db.close()
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
