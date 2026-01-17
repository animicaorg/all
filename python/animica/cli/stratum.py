"""
Stratum mining bridge CLI for Animica.

Provides commands for:
  - Starting/stopping Stratum server (up/down)
  - Checking Stratum server status
  - Managing stratum bridge daemon
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import typer

from animica.config import load_network_config
from .paths import ensure_animica_dir, get_animica_home
from .timeouts import DEFAULT_RPC_TIMEOUT, resolve_timeout

app = typer.Typer(help="Manage Stratum mining bridge server.")

# Environment variables
RPC_ENV = "ANIMICA_RPC_URL"
STRATUM_BIND_ENV = "ANIMICA_STRATUM_BIND"
STRATUM_PORT_ENV = "ANIMICA_STRATUM_PORT"

# Defaults
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 3333
DEFAULT_LOG_LEVEL = "info"


def _resolve_stratum_pid_file() -> Path:
    """Resolve the PID file path for the stratum server."""
    animica_home = get_animica_home()
    return animica_home / "stratum.pid"


def _parse_pid_file(pid_file: Path) -> dict[str, Optional[int]]:
    """Parse PID file content."""
    if not pid_file.exists():
        return {}
    content = pid_file.read_text(encoding="utf-8").strip()
    if not content:
        return {}
    if content.isdigit():
        return {"pid": int(content), "port": None}
    
    data: dict[str, Optional[int]] = {"pid": None, "port": None, "bind": None}
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
        elif key == "bind":
            data["bind"] = value
    return data


def _is_process_running(pid: int) -> bool:
    """Check if a process with given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _write_pid_file(pid_file: Path, pid: int, port: int, bind: str) -> None:
    """Write PID file with process information."""
    ensure_animica_dir()
    content = f"pid={pid}\nport={port}\nbind={bind}\n"
    pid_file.write_text(content, encoding="utf-8")


def _remove_pid_file(pid_file: Path) -> None:
    """Remove PID file if it exists."""
    if pid_file.exists():
        pid_file.unlink(missing_ok=True)


def _get_stratum_status(pid_file: Path) -> tuple[bool, Optional[dict]]:
    """
    Get stratum server status.
    
    Returns:
        (is_running, info_dict)
    """
    data = _parse_pid_file(pid_file)
    if not data or data.get("pid") is None:
        return False, None
    
    pid = data["pid"]
    if not _is_process_running(pid):
        # Stale PID file
        _remove_pid_file(pid_file)
        return False, None
    
    return True, {
        "pid": pid,
        "port": data.get("port", DEFAULT_PORT),
        "bind": data.get("bind", DEFAULT_BIND),
    }


@app.command("up")
def stratum_up(
    bind: str = typer.Option(
        DEFAULT_BIND,
        "--bind",
        help="Bind address (default: 127.0.0.1 for localhost only)",
        envvar=STRATUM_BIND_ENV,
    ),
    port: int = typer.Option(
        DEFAULT_PORT,
        "--port",
        help="Stratum server port",
        envvar=STRATUM_PORT_ENV,
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Node RPC URL",
        envvar=RPC_ENV,
    ),
    log_level: str = typer.Option(
        DEFAULT_LOG_LEVEL,
        "--log-level",
        help="Logging level (debug, info, warning, error)",
    ),
    daemon: bool = typer.Option(
        True,
        "--daemon/--no-daemon",
        help="Run in background as daemon (default: True)",
    ),
    log_file: Optional[str] = typer.Option(
        None,
        "--log-file",
        help="Log file path (daemon mode only)",
    ),
    public: bool = typer.Option(
        False,
        "--public",
        help="Bind to 0.0.0.0 (requires --auth-token)",
    ),
    auth_token: Optional[str] = typer.Option(
        None,
        "--auth-token",
        help="Auth token for public binding",
    ),
) -> None:
    """
    Start the Stratum mining bridge server.
    
    By default, runs as a background daemon. Use --no-daemon to run in foreground.
    
    The server binds to localhost (127.0.0.1) only for security.
    Use --public to bind to 0.0.0.0, which requires --auth-token.
    
    Examples:
        # Start in background (default)
        animica stratum up
        
        # Start in foreground
        animica stratum up --no-daemon
        
        # Start on custom port
        animica stratum up --port 9999
        
        # Check if running
        animica stratum status
        
        # Stop the server
        animica stratum down
    """
    # Check if already running
    pid_file = _resolve_stratum_pid_file()
    is_running, info = _get_stratum_status(pid_file)
    
    if is_running:
        typer.echo(
            f"Stratum server is already running (PID {info['pid']}) "
            f"on {info['bind']}:{info['port']}"
        )
        raise typer.Exit(1)
    
    # Handle public binding security
    if public:
        bind = "0.0.0.0"
        if not auth_token:
            typer.echo(
                "Error: --auth-token is required when using --public",
                err=True,
            )
            raise typer.Exit(1)
    
    # Resolve RPC URL
    if not rpc_url:
        cfg = load_network_config()
        rpc_url = cfg.rpc_url
    
    # Validate bind address
    if bind not in ("127.0.0.1", "localhost", "0.0.0.0", "::1", "::"):
        typer.echo(
            f"Warning: Binding to {bind} may expose your server. "
            f"Use 127.0.0.1 for localhost only.",
            err=True,
        )
    
    # Build command to start stratum server
    python_exe = sys.executable
    cmd = [
        python_exe,
        "-m",
        "mining.stratum_bridge",
        "--rpc-url", rpc_url,
        "--listen", f"{bind}:{port}",
        "--address", "anim1placeholder",  # Will be overridden by miner
        "--log-level", log_level,
    ]
    
    if auth_token:
        cmd.extend(["--auth-token", auth_token])
    
    if daemon:
        typer.echo(f"Starting Stratum server in background (daemon mode)")
    else:
        typer.echo(f"Starting Stratum server in foreground (press Ctrl+C to stop)")
    typer.echo(f"Server URL: stratum+tcp://{bind}:{port}")
    typer.echo(f"RPC URL: {rpc_url}")
    typer.echo("Note: Payout address will be set by connecting miners")
    
    if daemon:
        # Run in background
        animica_home = get_animica_home()
        ensure_animica_dir()
        
        if log_file is None:
            log_file = str(animica_home / "stratum.log")
        
        typer.echo(f"Running in daemon mode, logs at: {log_file}")
        
        # Start subprocess in background
        with open(log_file, "a") as f:
            process = subprocess.Popen(
                cmd,
                stdout=f,
                stderr=f,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        
        # Write PID file
        _write_pid_file(pid_file, process.pid, port, bind)
        
        # Give it a moment to start
        time.sleep(1)
        
        # Check if still running
        if not _is_process_running(process.pid):
            typer.echo("Error: Stratum server failed to start. Check logs.", err=True)
            _remove_pid_file(pid_file)
            raise typer.Exit(1)
        
        typer.echo(f"\n✓ Stratum server started successfully!")
        typer.echo(f"  PID: {process.pid}")
        typer.echo(f"  URL: stratum+tcp://{bind}:{port}")
        typer.echo(f"  Logs: {log_file}")
        typer.echo(f"\nConnect miners with:")
        typer.echo(f"  animica miner stratum --address anim1... --url stratum+tcp://{bind}:{port}")
    else:
        # Run in foreground
        try:
            subprocess.run(cmd, check=True)
        except KeyboardInterrupt:
            typer.echo("\nStratum server stopped.")
        except subprocess.CalledProcessError as e:
            typer.echo(f"Error: Stratum server exited with code {e.returncode}", err=True)
            raise typer.Exit(e.returncode)


@app.command("down")
def stratum_down() -> None:
    """
    Stop the Stratum mining bridge server.
    """
    pid_file = _resolve_stratum_pid_file()
    is_running, info = _get_stratum_status(pid_file)
    
    if not is_running:
        typer.echo("Stratum server is not running.")
        # Clean up stale PID file if present
        _remove_pid_file(pid_file)
        return
    
    pid = info["pid"]
    typer.echo(f"Stopping Stratum server (PID {pid})...")
    
    try:
        # Send SIGTERM for graceful shutdown
        os.kill(pid, signal.SIGTERM)
        
        # Wait for process to stop (up to 10 seconds)
        for _ in range(100):
            if not _is_process_running(pid):
                break
            time.sleep(0.1)
        else:
            # Force kill if still running
            typer.echo("Process did not stop gracefully, forcing...")
            try:
                os.kill(pid, signal.SIGKILL)
                time.sleep(0.5)
            except (OSError, ProcessLookupError):
                pass
        
        # Remove PID file
        _remove_pid_file(pid_file)
        
        # Verify it's stopped
        if not _is_process_running(pid):
            typer.echo("Stratum server stopped successfully.")
        else:
            typer.echo("Warning: Process may still be running.", err=True)
    
    except (OSError, ProcessLookupError) as e:
        typer.echo(f"Error stopping process: {e}", err=True)
        _remove_pid_file(pid_file)
        raise typer.Exit(1)


@app.command("status")
def stratum_status(
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output status in JSON format",
    ),
) -> None:
    """
    Show Stratum server status.
    """
    pid_file = _resolve_stratum_pid_file()
    is_running, info = _get_stratum_status(pid_file)
    
    if not is_running:
        if json_output:
            typer.echo(json.dumps({"running": False}))
        else:
            typer.echo("Stratum server is not running.")
        return
    
    # Try to get stats from server if available
    # For now, just show basic info
    status_data = {
        "running": True,
        "pid": info["pid"],
        "bind": info["bind"],
        "port": info["port"],
        "url": f"stratum+tcp://{info['bind']}:{info['port']}",
    }
    
    if json_output:
        typer.echo(json.dumps(status_data, indent=2))
    else:
        typer.echo("Stratum server status:")
        typer.echo(f"  Status: Running")
        typer.echo(f"  PID: {info['pid']}")
        typer.echo(f"  Bind: {info['bind']}:{info['port']}")
        typer.echo(f"  URL: stratum+tcp://{info['bind']}:{info['port']}")
        
        # TODO: Add more stats when available:
        # - uptime
        # - connected miners
        # - shares accepted/rejected
        # - blocks found


if __name__ == "__main__":
    app()
