"""Node lifecycle and inspection CLI for Animica developers."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import typer
from animica.config import get_network_defaults, load_network_config
from animica.seeds import get_seed_nodes

from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, describe_timeout, resolve_timeout

from .state import get_cli_state

DEFAULT_RPC_URL = load_network_config().rpc_url
RPC_ENV = "ANIMICA_RPC_URL"
STATE_KEY_NETWORK = "active_network"
BOOTSTRAP_NODE_ENV = "ANIMICA_BOOTSTRAP_NODE"

# Networks that use the 'dev' profile in docker-compose
DEV_NETWORKS = {"devnet", "local-devnet"}

BOOTSTRAP_TIMEOUT_ENV = "ANIMICA_BOOTSTRAP_TIMEOUT"
# Default bootstrap timeout: None = wait indefinitely during seed fetch.
BOOTSTRAP_RPC_TIMEOUT: Optional[float] = None
BOOTSTRAP_SEED_RETRY_DELAY = 1.0
BOOTSTRAP_SEED_RETRY_DELAY_MAX = 30.0
BOOTSTRAP_HEAD_RETRIES: Optional[int] = None
BOOTSTRAP_HEAD_RETRY_DELAY = 1.0
BOOTSTRAP_HEAD_RETRY_DELAY_MAX = 30.0
ALLOWED_BOOTSTRAP_METHODS = {
    "bootstrap.getManifest",
    "bootstrap.getSeeds",
    "chain.getHead",
}
PEER_LIST_METHODS = (
    "p2p.listPeers",
    "p2p.getPeers",
    "p2p.peers",
    "admin_peers",
    "net_peers",
)
PEER_COUNT_METHODS = (
    "net.peerCount",
    "p2p.peerCount",
    "p2p.peer_count",
    "net_peerCount",
)

app = typer.Typer(help="Manage and query Animica nodes.")
p2p_app = typer.Typer(help="P2P diagnostics and peer helpers.")


async def rpc_call(
    method: str, params: Optional[list[Any]] = None, *, rpc_url: str, timeout: Optional[float] = None
) -> Any:
    resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    payload: Dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }
    async with httpx.AsyncClient(timeout=resolved_timeout) as client:
        response = await client.post(rpc_url, json=payload)
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            body = response.text.strip()
            snippet = body[:200] + ("..." if len(body) > 200 else "")
            detail = snippet if snippet else "<empty response>"
            raise RuntimeError(
                f"RPC returned non-JSON response (status {response.status_code}): {detail}"
            ) from exc
    if "error" in data:
        raise RuntimeError(data["error"])
    return data.get("result")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    """Resolve RPC URL from CLI arg, env var, or network config (defaults to mainnet).
    
    Empty strings are treated as unset and fall back to the next priority level.
    """
    # Check CLI argument first
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    
    # Check environment variable
    env_url = os.environ.get(RPC_ENV)
    if env_url and env_url.strip():
        return env_url.strip()
    
    # Fall back to network config
    return load_network_config().rpc_url


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_bootstrap_node() -> bool:
    return _is_truthy(os.getenv(BOOTSTRAP_NODE_ENV))


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2)


def _extract_field(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            value = data.get(key)
            if value is not None:
                return value
    return None


def _load_p2p_config() -> tuple[Optional[Any], Optional[str]]:
    try:
        from p2p.config import load_config as load_p2p_config
    except Exception as exc:
        return None, f"Failed to import p2p.config: {exc}"
    try:
        return load_p2p_config(), None
    except Exception as exc:
        return None, f"Failed to load P2P config: {exc}"


def _db_path(cfg: Any) -> Path:
    data_dir = Path(os.path.expanduser(cfg.data_dir))
    data_dir.mkdir(parents=True, exist_ok=True)
    db_name = getattr(cfg, "db_name", "animica.db")
    return data_dir / db_name


def _bootstrap_state_path(cfg: Any) -> Path:
    data_dir = Path(os.path.expanduser(cfg.data_dir))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "bootstrap.json"


def _persist_bootstrap_state(cfg: Any, manifest: dict[str, Any], seeds: list[str]) -> Path:
    state_path = _bootstrap_state_path(cfg)
    payload = {"manifest": manifest, "seeds": seeds, "fetched_at": int(time.time())}
    state_path.write_text(json.dumps(payload, indent=2))
    return state_path


def _sync_state_path(cfg: Any) -> Path:
    data_dir = Path(os.path.expanduser(cfg.data_dir))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "sync" / "progress.json"


def _load_sync_state(cfg: Any) -> Optional[Dict[str, Any]]:
    state_path = _sync_state_path(cfg)
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text())
    except Exception:
        return None


def _load_cached_bootstrap_head(cfg: Any) -> Optional[Dict[str, Any]]:
    bootstrap_url = getattr(cfg, "bootstrap_url", None)
    if not bootstrap_url:
        return None
    payload = _load_sync_state(cfg)
    if not payload or payload.get("rpc_url") != bootstrap_url:
        return None
    height = payload.get("height")
    if height is None:
        return None
    return {
        "height": height,
        "hash": payload.get("head_hash"),
        "chain_id": payload.get("chain_id"),
    }


def _format_sync_timestamp(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(raw)))
    except (TypeError, ValueError):
        return None


def _format_peer_timestamp(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(raw)))
    except (TypeError, ValueError):
        return None


def _coerce_peer_count(raw: Any) -> Optional[int]:
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        value = raw.strip()
        if value.startswith("0x"):
            try:
                return int(value, 16)
            except ValueError:
                return None
        if value.isdigit():
            return int(value)
    return None


def _get_peer_count(rpc_url: str, rpc_timeout: Optional[float]) -> tuple[Optional[int], Optional[str]]:
    last_error = None
    for method in PEER_COUNT_METHODS:
        try:
            result = asyncio.run(rpc_call(method, [], rpc_url=rpc_url, timeout=rpc_timeout))
            count = _coerce_peer_count(result)
            if count is not None:
                return count, None
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        return None, str(last_error)
    return None, "RPC peer count unavailable"


def _get_peers(rpc_url: str, rpc_timeout: Optional[float]) -> tuple[list[dict[str, Any]], Optional[str]]:
    last_error = None
    for method in PEER_LIST_METHODS:
        try:
            peers = asyncio.run(rpc_call(method, [], rpc_url=rpc_url, timeout=rpc_timeout))
            if isinstance(peers, list):
                return peers, None
            if peers is None:
                continue
            return [], None
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        return [], str(last_error)
    return [], "RPC peer list unavailable"


def _get_p2p_status(
    rpc_url: str, rpc_timeout: Optional[float]
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        result = asyncio.run(
            rpc_call("p2p.getStatus", [], rpc_url=rpc_url, timeout=rpc_timeout)
        )
        if isinstance(result, dict):
            return result, None
        return None, "unexpected p2p status response"
    except Exception as exc:
        return None, str(exc)


def _persist_sync_state(
    cfg: Any,
    *,
    rpc_url: str,
    head_info: dict[str, Any],
    note: Optional[str] = None,
) -> None:
    state_path = _sync_state_path(cfg)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "rpc_url": rpc_url,
        "height": _extract_field(head_info, "height", "number", "blockNumber"),
        "head_hash": _extract_field(head_info, "hash", "blockHash"),
        "chain_id": _extract_field(head_info, "chainId", "chain_id"),
        "peer_count": 0,
        "updated_at": time.time(),
    }
    if note:
        payload["note"] = note
    state_path.write_text(json.dumps(payload, indent=2))


def _record_bootstrap_head(net_cfg: Any, bootstrap_url: Optional[str], *, quiet: bool = False) -> bool:
    if not bootstrap_url:
        return False
    last_exc: Optional[Exception] = None
    delay = BOOTSTRAP_HEAD_RETRY_DELAY
    attempt = 1
    while True:
        try:
            head = _bootstrap_rpc(bootstrap_url, "chain.getHead")
            if not head:
                raise RuntimeError("empty head response")
            _persist_sync_state(
                net_cfg,
                rpc_url=bootstrap_url,
                head_info=head,
                note="bootstrap head snapshot",
            )
            if not quiet:
                typer.secho(
                    f"✓ Recorded bootstrap head at height {head.get('height') or head.get('number')}",
                    fg=typer.colors.GREEN,
                )
            return True
        except Exception as exc:
            last_exc = exc
            if BOOTSTRAP_HEAD_RETRIES is not None and attempt >= BOOTSTRAP_HEAD_RETRIES:
                break
            if not quiet:
                typer.secho(
                    "Warning: bootstrap head fetch failed "
                    f"(attempt {attempt}: {exc}); retrying in {delay:.1f}s.",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
            time.sleep(delay)
            delay = min(delay * 2, BOOTSTRAP_HEAD_RETRY_DELAY_MAX)
            attempt += 1
            continue

    cached = _load_sync_state(net_cfg)
    if not quiet:
        if cached and cached.get("height") is not None:
            updated_at = _format_sync_timestamp(cached.get("updated_at")) or "unknown time"
            typer.secho(
                "Warning: bootstrap head fetch failed after retries; using cached sync state "
                f"(height {cached.get('height')}, updated {updated_at}).",
                fg=typer.colors.YELLOW,
                err=True,
            )
        else:
            typer.secho(
                f"Warning: bootstrap head fetch failed after retries ({last_exc})",
                fg=typer.colors.YELLOW,
                err=True,
            )
    return False


def _bootstrap_seeds_from_state(net_cfg: Any) -> list[str]:
    state_path = _bootstrap_state_path(net_cfg)
    if not state_path.exists():
        return []
    try:
        payload = json.loads(state_path.read_text())
        seeds = payload.get("seeds")
        if isinstance(seeds, list):
            return [str(s) for s in seeds if s]
    except Exception:
        return []
    return []


def _collect_seed_candidates(net_cfg: Any) -> list[str]:
    seeds: list[str] = []
    seeds.extend(_bootstrap_seeds_from_state(net_cfg))

    try:
        os.environ.setdefault("ANIMICA_P2P_CHAIN_ID", str(getattr(net_cfg, "chain_id", "")))
        p2p_cfg, _ = _load_p2p_config()
        if p2p_cfg and getattr(p2p_cfg, "seeds", None):
            seeds.extend(list(p2p_cfg.seeds))
    except Exception:
        pass

    seeds.extend(get_seed_nodes(net_cfg.name))
    return list(dict.fromkeys([str(seed) for seed in seeds if seed]))


def _wait_for_rpc_ready(rpc_url: str, *, timeout_s: float = 60.0, interval_s: float = 2.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            _local_rpc(rpc_url, "chain.getHead", [])
            return True
        except Exception:
            time.sleep(interval_s)
    return False


def _post_start_peer_bootstrap(
    net_cfg: Any,
    *,
    rpc_url: str,
    bootstrap_url: Optional[str],
    wait_timeout: float = 30.0,
) -> None:
    try:
        from .sync import _seed_local_peerstores, _trigger_sync
    except Exception:
        return

    if not _wait_for_rpc_ready(rpc_url, timeout_s=60.0):
        typer.secho(
            "Warning: local RPC not ready yet; skipping auto peer bootstrap.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return

    peer_count, _ = _get_peer_count(rpc_url, None)
    if peer_count and peer_count > 0:
        return

    typer.secho(
        "No peers connected yet; seeding peers from bootstrap sources...",
        fg=typer.colors.YELLOW,
    )
    _seed_local_peerstores(
        net_cfg,
        target_rpc_url=rpc_url,
        bootstrap_url=bootstrap_url,
        quiet=False,
    )

    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        peer_count, _ = _get_peer_count(rpc_url, None)
        if peer_count and peer_count > 0:
            typer.secho(f"✓ Peers connected: {peer_count}", fg=typer.colors.GREEN)
            try:
                if asyncio.run(_trigger_sync(rpc_url)):
                    typer.secho("✓ Sync trigger sent", fg=typer.colors.GREEN)
            except Exception:
                pass
            return
        time.sleep(2.0)


def _bootstrap_rpc(bootstrap_url: str, method: str) -> Dict[str, Any]:
    if method not in ALLOWED_BOOTSTRAP_METHODS:
        raise ValueError(
            f"Unsupported bootstrap method '{method}'. Only read-only bootstrap RPC calls are permitted."
        )

    timeout = None
    if method != "chain.getHead":
        timeout = resolve_timeout(
            "bootstrap RPC timeout",
            None,
            env_var=BOOTSTRAP_TIMEOUT_ENV,
            default=BOOTSTRAP_RPC_TIMEOUT,
        )
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": []}
    try:
        resp = httpx.post(bootstrap_url, json=payload, timeout=timeout)
        if resp.status_code >= 400:
            body = resp.text.strip()
            snippet = body[:200] + ("..." if len(body) > 200 else "")
            detail = snippet if snippet else "<empty response>"
            raise RuntimeError(f"HTTP {resp.status_code} {resp.reason_phrase}: {detail}")
        parsed = resp.json()
        if "error" in parsed and parsed["error"]:
            raise RuntimeError(parsed["error"])
        result = parsed.get("result")
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        raise RuntimeError(f"Bootstrap RPC {method} failed: {exc}") from exc


def _local_rpc(rpc_url: str, method: str, params: list[Any] | None = None) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    resp = httpx.post(rpc_url, json=payload, timeout=5.0)
    resp.raise_for_status()
    parsed = resp.json()
    if "error" in parsed and parsed["error"]:
        raise RuntimeError(parsed["error"])
    return parsed.get("result")


def _fetch_bootstrap_data(
    net_cfg: Any,
    bootstrap_url: str,
    *,
    quiet: bool = False,
) -> tuple[dict[str, Any], list[str], Path]:
    manifest: dict[str, Any] = {}
    manifest_error: Optional[Exception] = None
    seed_error: Optional[Exception] = None

    try:
        manifest = _bootstrap_rpc(bootstrap_url, "bootstrap.getManifest")
    except Exception as exc:
        manifest_error = exc

    seeds: list[str] = []
    p2p_info = manifest.get("p2p") if isinstance(manifest, dict) else None
    if isinstance(p2p_info, dict):
        seeds = list(p2p_info.get("seeds") or [])

    if not seeds:
        try:
            seed_resp = _bootstrap_rpc(bootstrap_url, "bootstrap.getSeeds")
            seeds = list(seed_resp.get("seeds") or [])
        except Exception as exc:
            seed_error = exc
            seeds = []

    if not seeds:
        fallback_seeds = get_seed_nodes(getattr(net_cfg, "name", "mainnet"))
        if fallback_seeds:
            seeds = list(fallback_seeds)
            if not quiet:
                typer.secho(
                    "Warning: bootstrap RPC unavailable; using bundled seed list.",
                    fg=typer.colors.YELLOW,
                    err=True,
                )

    if not seeds and (manifest_error or seed_error):
        exc = manifest_error or seed_error
        raise RuntimeError(f"Bootstrap RPC failed and no fallback seeds available: {exc}") from exc

    if manifest_error and not quiet:
        typer.secho(
            f"Warning: bootstrap manifest fetch failed ({manifest_error}); continuing.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    state_path = _persist_bootstrap_state(net_cfg, manifest, seeds)
    if seeds:
        seed_csv = ",".join(str(s) for s in seeds)
        os.environ["ANIMICA_P2P_SEEDS"] = seed_csv
        os.environ["P2P_SEEDS"] = seed_csv
    return manifest, seeds, state_path


def _auto_bootstrap_if_needed(net_cfg: Any, bootstrap_url: str | None, *, force: bool = False, quiet: bool = False) -> bool:
    db_path = _db_path(net_cfg)
    db_exists = db_path.exists() and db_path.stat().st_size > 0
    if db_exists and not force:
        return False

    endpoint = bootstrap_url or getattr(net_cfg, "bootstrap_url", None)
    if not endpoint:
        return False

    if not quiet:
        typer.echo(f"Auto-bootstrap: fetching seeds from {endpoint}")

    delay = BOOTSTRAP_SEED_RETRY_DELAY
    attempt = 1
    while True:
        try:
            _fetch_bootstrap_data(net_cfg, endpoint, quiet=quiet)
            if not quiet:
                typer.secho("✓ Bootstrap metadata saved locally", fg=typer.colors.GREEN)
            return True
        except Exception as exc:
            if not quiet:
                typer.secho(
                    "Warning: auto-bootstrap failed "
                    f"(attempt {attempt}: {exc}); retrying in {delay:.1f}s (will keep trying).",
                    fg=typer.colors.YELLOW,
                    err=True,
                )
            time.sleep(delay)
            delay = min(delay * 2, BOOTSTRAP_SEED_RETRY_DELAY_MAX)
            attempt += 1


def _ensure_network_set() -> str:
    """
    Ensure a network is configured before performing node operations.
    
    Returns the active network name if set.
    Exits with error message if no network is configured.
    """
    # Check environment variable first (highest priority)
    network = os.environ.get("ANIMICA_NETWORK")
    if network:
        return network
    
    # Check persisted state
    state = get_cli_state()
    network = state.get(STATE_KEY_NETWORK)
    if network:
        return network
    
    # No network configured - exit with helpful message
    typer.echo(
        "Error: No network configured. Node lifecycle operations require a network to be set.",
        err=True
    )
    typer.echo("\nPlease set a network first using one of these methods:", err=True)
    typer.echo("  1. Set persistent network: animica network set <network>", err=True)
    typer.echo("  2. Set via environment: export ANIMICA_NETWORK=<network>", err=True)
    typer.echo("  3. Use --network flag: animica --network <network> node up", err=True)
    typer.echo("\nAvailable networks: mainnet, testnet, devnet, local-devnet", err=True)
    raise typer.Exit(code=1)


def _get_compose_file(network: str) -> Path:
    """
    Get the path to the docker-compose file for the specified network.
    
    Args:
        network: Network name (mainnet, testnet, devnet, local-devnet)
        
    Returns:
        Path to the appropriate docker-compose file
        
    Raises:
        typer.Exit: If compose file not found
    """
    defaults = get_network_defaults(network)
    compose_file = defaults["compose_file"]
    
    if not compose_file.exists():
        typer.echo(
            f"Error: Docker Compose file not found at {compose_file}",
            err=True
        )
        typer.echo(
            f"Node lifecycle management for {network} requires the compose setup.",
            err=True
        )
        typer.echo(
            f"\nExpected location: {compose_file}",
            err=True
        )
        raise typer.Exit(code=1)
    
    return compose_file


@app.command()
def status(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    retry_delay: float = typer.Option(
        1.0, "--retry-delay", help="Delay between RPC retry attempts in seconds (default: 1.0)", envvar="ANIMICA_RETRY_DELAY"
    ),
    max_retries: int = typer.Option(
        3, "--max-retries", help="Maximum number of RPC attempts before failing fast"
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    )
) -> None:
    """Show chain head, block info and sync state. Retries with bounded attempts on RPC errors."""
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    
    # Validate retry delay
    if retry_delay <= 0:
        typer.echo(f"Error: retry-delay must be greater than 0, got {retry_delay}", err=True)
        raise typer.Exit(code=1)
    if max_retries < 1:
        typer.echo("Error: max-retries must be at least 1", err=True)
        raise typer.Exit(code=1)

    net_cfg = load_network_config()
    
    # Bounded retry loop for RPC operations
    attempt = 0
    backoff_delay = retry_delay
    while attempt < max_retries:
        attempt += 1
        try:
            head = asyncio.run(rpc_call("chain.getHead", [], rpc_url=url, timeout=rpc_timeout))
            height = _extract_field(head, "height", "number", "blockNumber")
            if height is None:
                height = 0
            chain_id = _extract_field(head, "chainId", "chain_id")
            head_hash = _extract_field(head, "hash", "blockHash")
            cached_bootstrap = _load_cached_bootstrap_head(net_cfg)
            
            block = None
            if height is not None:
                try:
                    block = asyncio.run(
                        rpc_call("chain.getBlockByHeight", [height], rpc_url=url, timeout=rpc_timeout)
                    )
                except Exception:
                    block = None

            sync_status = None
            for method in ("node.syncStatus", "chain.syncing", "sync.isSyncing"):
                try:
                    sync_status = asyncio.run(rpc_call(method, [], rpc_url=url, timeout=rpc_timeout))
                    break
                except Exception:
                    continue

            peer_count, peer_count_error = _get_peer_count(url, rpc_timeout)
            peers, peers_error = _get_peers(url, rpc_timeout)
            peer_error = peer_count_error or peers_error
            if peer_count is None and peers:
                peer_count = len(peers)
            p2p_status, p2p_status_error = _get_p2p_status(url, rpc_timeout)

            typer.echo(f"RPC URL: {url}")
            typer.echo(f"Chain ID: {chain_id}")
            typer.echo(f"Head height: {height}")
            typer.echo(f"Head hash: {head_hash}")
            if cached_bootstrap:
                cached_height = cached_bootstrap.get("height")
                cached_hash = cached_bootstrap.get("hash")
                if cached_height is not None and (height == 0 or height < cached_height):
                    typer.echo(f"Bootstrap head (cached): {cached_height}")
                    if cached_hash:
                        typer.echo(f"Bootstrap hash (cached): {cached_hash}")
            typer.echo(f"Sync status: {sync_status}")
            if peer_error:
                typer.echo(f"Peer status: unavailable ({peer_error})")
            elif peer_count is not None:
                typer.echo(f"Peer count: {peer_count}")
            if peers:
                typer.echo("Peers:")
                for index, peer in enumerate(peers[:10], 1):
                    peer_id = peer.get("id") or peer.get("peerId") or peer.get("peer_id") or "unknown"
                    addr = peer.get("addr") or peer.get("address") or peer.get("multiaddr") or "unknown"
                    status = peer.get("status") or peer.get("state") or "connected"
                    direction = peer.get("direction")
                    height_info = peer.get("height")
                    last_seen = _format_peer_timestamp(peer.get("lastSeen") or peer.get("last_seen"))
                    summary = f"  {index}. {peer_id} ({addr}) [{status}]"
                    if direction:
                        summary += f" {direction}"
                    if height_info is not None:
                        summary += f" height={height_info}"
                    if last_seen:
                        summary += f" last_seen={last_seen}"
                    typer.echo(summary)
                if len(peers) > 10:
                    typer.echo(f"  ... and {len(peers) - 10} more peers")
            if p2p_status_error:
                typer.echo(f"P2P status: unavailable ({p2p_status_error})")
            elif p2p_status:
                typer.echo(f"P2P running: {p2p_status.get('p2p_running')}")
                typer.echo(
                    "P2P peers: total={total} inbound={inbound} outbound={outbound}".format(
                        total=p2p_status.get("peers_total"),
                        inbound=p2p_status.get("peers_inbound"),
                        outbound=p2p_status.get("peers_outbound"),
                    )
                )
                typer.echo(
                    "Bootstrap attempts (last 5m): {count}".format(
                        count=p2p_status.get("bootstrap_attempts_last_5m")
                    )
                )
                last_bootstrap = p2p_status.get("bootstrap_last_attempt") or {}
                if last_bootstrap:
                    last_bootstrap_at = _format_peer_timestamp(
                        last_bootstrap.get("at")
                    )
                    last_bootstrap_addr = last_bootstrap.get("addr")
                    last_bootstrap_ok = last_bootstrap.get("success")
                    last_bootstrap_err = last_bootstrap.get("error")
                    summary = f"Last bootstrap: {last_bootstrap_addr} success={last_bootstrap_ok}"
                    if last_bootstrap_at:
                        summary += f" at {last_bootstrap_at}"
                    if last_bootstrap_err:
                        summary += f" error={last_bootstrap_err}"
                    typer.echo(summary)
            if block is not None:
                typer.echo("Head block:")
                typer.echo(_pretty(block))
            
            # Success - exit the retry loop
            return
            
        except Exception as e:
            # RPC error - retry with backoff
            import time
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            error_message = str(e).strip()
            if not error_message:
                error_message = repr(e)
            if attempt >= max_retries:
                typer.echo(
                    f"[{timestamp}] Node status failed after {attempt} attempts: {error_message}",
                    err=True,
                )
                cached = _load_sync_state(load_network_config())
                if cached:
                    typer.secho(
                        "\n⚠ RPC unavailable. Showing last persisted sync state from 'animica sync force'.",
                        fg=typer.colors.YELLOW,
                    )
                    typer.echo(f"RPC URL: {cached.get('rpc_url', 'unknown')} (cached)")
                    typer.echo(f"Chain ID: {cached.get('chain_id')}")
                    typer.echo(f"Head height: {cached.get('height')}")
                    typer.echo(f"Head hash: {cached.get('head_hash')}")
                    typer.echo(f"Peer count: {cached.get('peer_count')}")
                    updated_at = _format_sync_timestamp(cached.get("updated_at"))
                    if updated_at:
                        typer.echo(f"Updated at: {updated_at}")
                    raise typer.Exit(code=0)
                raise typer.Exit(code=1)

            typer.echo(
                f"[{timestamp}] Retrying node status (attempt {attempt} failed: {error_message}). Retrying in {backoff_delay:.1f}s...",
                err=True,
            )
            time.sleep(backoff_delay)
            backoff_delay *= 2
            continue  # Retry bounded number of times


@app.command()
def head(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
) -> None:
    """Print the current chain head summary."""
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    head_info = asyncio.run(rpc_call("chain.getHead", [], rpc_url=url, timeout=rpc_timeout))
    typer.echo(_pretty(head_info))


@app.command()
def bootstrap(
    network: Optional[str] = typer.Option(
        None, "--network", help="Network to bootstrap (defaults to current network)", envvar="ANIMICA_NETWORK"
    ),
    bootstrap_url: Optional[str] = typer.Option(
        None,
        "--bootstrap-url",
        help="Bootstrap RPC endpoint (defaults to network bootstrap URL)",
        envvar="ANIMICA_BOOTSTRAP_RPC_URL",
    ),
    detach: bool = typer.Option(True, "--detach/--no-detach", help="Run node in background while bootstrapping"),
    max_peer_wait: int = typer.Option(60, "--peer-wait", help="Seconds to wait for peers after start"),
    retry_seeds: int = typer.Option(1, "--retry-seeds", help="Retry seed fetches when no peers"),
) -> None:
    """Bootstrap a node using the public bootstrap RPC, then switch to local RPC."""

    net_cfg = load_network_config(network)
    bootstrap_endpoint = bootstrap_url or net_cfg.bootstrap_url
    if not bootstrap_endpoint:
        typer.echo("No bootstrap RPC configured for this network", err=True)
        raise typer.Exit(code=1)

    db_path = _db_path(net_cfg)
    db_exists = db_path.exists() and db_path.stat().st_size > 0

    typer.secho(f"Bootstrapping network: {net_cfg.name}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Bootstrap RPC: {bootstrap_endpoint}")
    typer.echo(f"Local RPC: {net_cfg.rpc_url}")
    typer.echo(f"Data dir: {net_cfg.data_dir} (db exists: {'yes' if db_exists else 'no'})")

    try:
        manifest, seeds, state_path = _fetch_bootstrap_data(
            net_cfg,
            bootstrap_endpoint,
            quiet=False,
        )
        typer.echo(f"Saved bootstrap state to {state_path}")
    except Exception as exc:
        typer.secho(f"Warning: failed to fetch bootstrap manifest ({exc})", fg=typer.colors.YELLOW, err=True)
        manifest = {}
        seeds = _collect_seed_candidates(net_cfg)
        state_path = None
        if seeds:
            try:
                state_path = _persist_bootstrap_state(net_cfg, manifest, seeds)
                typer.echo(f"Saved bootstrap state to {state_path}")
            except Exception:
                state_path = None
        if not seeds:
            typer.secho("Warning: no bootstrap seeds available; continuing without seeds.", fg=typer.colors.YELLOW, err=True)

    # Ensure subsequent commands use this network
    os.environ["ANIMICA_NETWORK"] = net_cfg.name

    # Start node using existing up command
    try:
        up(detach=detach, build=True, with_miner=False)
    except SystemExit as exc:  # Typer exits bubble up as SystemExit
        if exc.code not in (0, None):
            raise
    except Exception as exc:
        typer.echo(f"Failed to start node: {exc}", err=True)
        raise typer.Exit(code=1)

    local_rpc = net_cfg.rpc_url
    typer.echo("Waiting for local node to become ready...")
    ready = False
    for _ in range(30):
        try:
            _local_rpc(local_rpc, "chain.getHead", [])
            ready = True
            break
        except Exception:
            time.sleep(2)
    if not ready:
        typer.echo("Node did not respond on local RPC within timeout", err=True)
        raise typer.Exit(code=1)

    attempts = max(1, max_peer_wait)
    refreshes = retry_seeds
    for attempt in range(attempts):
        try:
            peers = _local_rpc(local_rpc, "p2p.listPeers", [])
            peer_count = len(peers) if isinstance(peers, list) else int(peers or 0)
        except Exception:
            peer_count = 0

        if peer_count > 0:
            typer.secho(f"Peers connected: {peer_count}. Bootstrap complete.", fg=typer.colors.GREEN)
            break

        if peer_count == 0 and seeds and attempt == 0:
            try:
                _local_rpc(local_rpc, "p2p.importPeers", [seeds])
            except Exception:
                pass

        if peer_count == 0 and refreshes > 0 and attempt and attempt % 10 == 0:
            if bootstrap_endpoint:
                try:
                    refreshed = _bootstrap_rpc(bootstrap_endpoint, "bootstrap.getSeeds")
                    new_seeds = list(refreshed.get("seeds") or [])
                    if new_seeds:
                        seeds = new_seeds
                    refreshes -= 1
                except Exception:
                    pass
            for seed in seeds:
                try:
                    _local_rpc(local_rpc, "p2p.addPeer", [str(seed)])
                except Exception:
                    continue

        time.sleep(1)
    else:
        typer.echo("Warning: no peers connected after bootstrap window", err=True)


@app.command()
def block(
    height: Optional[int] = typer.Option(None, "--height", help="Block height"),
    hash: Optional[str] = typer.Option(None, "--hash", help="Block hash"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
) -> None:
    """Fetch and display a block by height or hash."""
    if not height and not hash:
        raise typer.BadParameter("Provide --height or --hash")
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    if height is not None:
        result = asyncio.run(rpc_call("chain.getBlockByHeight", [height], rpc_url=url, timeout=rpc_timeout))
        if (
            isinstance(result, dict)
            and "transactions" not in result
            and result.get("hash")
        ):
            result = asyncio.run(
                rpc_call("chain.getBlockByHash", [result["hash"]], rpc_url=url, timeout=rpc_timeout)
            )
    else:
        result = asyncio.run(rpc_call("chain.getBlockByHash", [hash], rpc_url=url, timeout=rpc_timeout))
    typer.echo(_pretty(result))


@app.command()
def tx(
    hash: str = typer.Option(..., "--hash", help="Transaction hash"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
) -> None:
    """Fetch and display a transaction by hash."""
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    result = asyncio.run(rpc_call("chain.getTransactionByHash", [hash], rpc_url=url, timeout=rpc_timeout))
    typer.echo(_pretty(result))


@app.command()
def up(
    detach: bool = typer.Option(
        True,
        "--detach/--no-detach",
        help="Run in detached mode (background)"
    ),
    build: bool = typer.Option(
        True,
        "--build/--no-build",
        help="Build images before starting"
    ),
    with_miner: bool = typer.Option(
        False,
        "--with-miner",
        help="Also start miner service (uses 'miner' profile)"
    ),
) -> None:
    """
    Start an Animica node using Docker Compose.
    
    This command spins up a node with the configured network settings.
    The compose file and configuration are automatically selected based on
    the active network (set via 'animica network set <network>').
    
    Network-specific default host ports:
      - mainnet: RPC 8545, P2P 30333, Metrics 9000
      - testnet: RPC 18546, P2P 31334, Metrics 19000
      - devnet: RPC 28545, P2P 31335, Metrics 29000
      - local-devnet: RPC 38545, P2P 31336, Metrics 39000
    
    Each network uses isolated data directories and volumes to prevent cross-network
    contamination of blockchain data. Ports can be customized via environment variables:
      HOST_RPC_PORT, HOST_P2P_PORT, HOST_METRICS_PORT
    
    Note: Studio Services (deploy/verify API) are NOT started by default.
    To start Studio Services, use 'animica studio up' after the node is running.
    
    Before running this command, ensure you have set a network using:
      animica network set <network>
    
    Examples:
      animica network set mainnet
      animica node up
      
      animica network set testnet
      animica node up --no-detach  # Run in foreground
      
      animica network set devnet
      animica node up --with-miner  # Start node with miner
      
      # Override default ports
      HOST_RPC_PORT=9545 animica node up
      
    To also start Studio Services (optional):
      animica node up
      animica studio up  # Start studio services separately
    """
    # Enforce network requirement
    network = _ensure_network_set()
    
    # Get network-specific compose file
    compose_file = _get_compose_file(network)
    net_cfg = load_network_config(network)
    data_dir = str(Path(net_cfg.data_dir).expanduser())
    
    defaults = get_network_defaults(network)
    net_cfg = load_network_config(network)
    data_dir = str(Path(net_cfg.data_dir).expanduser())

    bootstrap_node = _is_bootstrap_node()
    if bootstrap_node:
        typer.secho(
            "Bootstrap node enabled: skipping auto-bootstrap checks.",
            fg=typer.colors.CYAN,
        )
    else:
        _auto_bootstrap_if_needed(net_cfg, os.getenv("ANIMICA_BOOTSTRAP_RPC_URL"), quiet=False)
        _record_bootstrap_head(
            net_cfg, os.getenv("ANIMICA_BOOTSTRAP_RPC_URL") or net_cfg.bootstrap_url, quiet=False
        )
    
    typer.secho(f"Starting node for network: {network}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Using compose file: {compose_file}")
    typer.echo(f"Chain ID: {defaults['chain_id']}")
    typer.echo(f"Host RPC Port: {os.environ.get('HOST_RPC_PORT', defaults['rpc_port'])}")
    typer.echo(f"Host P2P Port: {os.environ.get('HOST_P2P_PORT', defaults['p2p_port'])}")
    typer.echo(f"Host Metrics Port: {os.environ.get('HOST_METRICS_PORT', defaults['metrics_port'])}")
    typer.echo(f"Data directory: {data_dir}")
    
    # Build docker-compose command
    # For devnet, we need to use profiles; for mainnet/testnet, services run by default
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
    ]
    
    # Add profiles based on network and options
    if network in DEV_NETWORKS:
        # Devnet uses profiles: 'dev' for node+miner by default
        cmd.extend(["--profile", "dev"])
    
    if with_miner and network not in DEV_NETWORKS:
        # For mainnet/testnet, miner is in separate profile
        cmd.extend(["--profile", "miner"])
    
    if build:
        cmd.extend(["up", "--build"])
    else:
        cmd.append("up")
    
    if detach:
        cmd.append("-d")
    
    typer.echo(f"\nRunning: {' '.join(cmd)}")
    typer.echo("This may take a few minutes on first run...\n")
    
    compose_env = {
        **os.environ,
        "ANIMICA_NETWORK": network,
        "ANIMICA_DATA_DIR": data_dir,
        "ANIMICA_P2P_DATA_DIR": str(Path(data_dir) / "p2p"),
    }
    if bootstrap_node:
        compose_env.setdefault("ANIMICA_RPC_BOOTSTRAP_NODE", "1")

    try:
        result = subprocess.run(
            cmd,
            cwd=compose_file.parent,
            check=False,
            env=compose_env,
        )
        
        if result.returncode == 0:
            typer.secho("✓ Node started successfully!", fg=typer.colors.GREEN, bold=True)
            if detach:
                typer.echo(f"\nNode is running in the background on network: {network}")
                typer.echo(f"View logs with: docker compose -f {compose_file} logs -f")
                typer.echo("Check status with: animica node status")
                if network == "mainnet":
                    typer.echo("\n--- Mainnet Node Running ---")
                    typer.echo("To check balances:")
                    typer.echo("  animica wallet show <address|label>")
                    typer.echo("  animica rpc call state.getBalance '{\"params\": [\"<address>\"]}'")
                elif network == "testnet":
                    typer.echo("\n--- Testnet Node Running ---")
                    typer.echo("Request testnet tokens from faucet if available")
                else:
                    typer.echo("\n--- Devnet Node Running ---")
                    typer.echo("Premine accounts available for testing")
                typer.echo("\nWallet file location: ~/.animica/wallets.json")
                _post_start_peer_bootstrap(
                    net_cfg,
                    rpc_url=net_cfg.rpc_url,
                    bootstrap_url=os.getenv("ANIMICA_BOOTSTRAP_RPC_URL") or net_cfg.bootstrap_url,
                )
        else:
            typer.secho(
                f"Error: Node startup failed with exit code {result.returncode}",
                fg=typer.colors.RED,
                err=True
            )
            raise typer.Exit(code=result.returncode)
            
    except FileNotFoundError:
        typer.echo(
            "Error: 'docker' command not found. Please install Docker and Docker Compose.",
            err=True
        )
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        typer.echo("\n\nInterrupted by user", err=True)
        raise typer.Exit(code=130)


@app.command(name="up-all")
def up_all(
    detach: bool = typer.Option(
        True,
        "--detach/--no-detach",
        help="Run in detached mode (background)"
    ),
    build: bool = typer.Option(
        True,
        "--build/--no-build",
        help="Build images before starting"
    ),
    with_miner: bool = typer.Option(
        False,
        "--with-miner",
        help="Also start miner service for applicable networks"
    ),
) -> None:
    """
    Start all Animica node networks at once.
    
    This command sequentially starts all supported networks with non-conflicting
    default host ports:
    - mainnet (chain ID 1): RPC 8545, P2P 30333, Metrics 9000
    - testnet (chain ID 2): RPC 18546, P2P 31334, Metrics 19000
    - devnet (chain ID 1337): RPC 28545, P2P 31335, Metrics 29000
    - local-devnet (chain ID 1337): RPC 38545, P2P 31336, Metrics 39000
    
    Each network uses its own compose file and data directory to prevent
    cross-network contamination. The command will attempt to start all
    networks and report progress for each one.
    
    If a network's compose file is missing, it will be skipped with a warning.
    If any network fails to start, the command will continue with remaining
    networks but will exit with a non-zero code at the end.
    
    Port customization: Set environment variables before running:
      HOST_RPC_PORT, HOST_P2P_PORT, HOST_METRICS_PORT (apply globally to all networks)
    
    Examples:
      animica node up-all
      animica node up-all --no-detach  # Run in foreground
      animica node up-all --with-miner # Start all networks with miners
    """
    # List of all supported networks
    all_networks = ["mainnet", "testnet", "devnet", "local-devnet"]
    
    typer.secho("Starting all Animica node networks...", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Networks to start: {', '.join(all_networks)}\n")
    
    failed_networks = []
    skipped_networks = []
    successful_networks = []
    
    for network in all_networks:
        typer.secho(f"\n{'='*60}", fg=typer.colors.CYAN)
        typer.secho(f"Starting network: {network}", fg=typer.colors.CYAN, bold=True)
        typer.secho(f"{'='*60}", fg=typer.colors.CYAN)
        
        # Get network-specific compose file
        try:
            defaults = get_network_defaults(network)
            compose_file = defaults["compose_file"]
            net_cfg = load_network_config(network)
            data_dir = str(Path(net_cfg.data_dir).expanduser())
            
            if not compose_file.exists():
                typer.secho(
                    f"⚠ Warning: Compose file not found for {network}: {compose_file}",
                    fg=typer.colors.YELLOW
                )
                typer.echo(f"Skipping {network}...\n")
                skipped_networks.append(network)
                continue
        except Exception as e:
            typer.secho(
                f"⚠ Warning: Error getting compose file for {network}: {e}",
                fg=typer.colors.YELLOW
            )
            typer.echo(f"Skipping {network}...\n")
            skipped_networks.append(network)
            continue
        
        bootstrap_node = _is_bootstrap_node()
        if not bootstrap_node:
            _auto_bootstrap_if_needed(net_cfg, os.getenv("ANIMICA_BOOTSTRAP_RPC_URL"), quiet=True)
            _record_bootstrap_head(
                net_cfg, os.getenv("ANIMICA_BOOTSTRAP_RPC_URL") or net_cfg.bootstrap_url, quiet=True
            )

        typer.echo(f"Compose file: {compose_file}")
        typer.echo(f"Chain ID: {defaults['chain_id']}")
        typer.echo(f"Host RPC Port: {os.environ.get('HOST_RPC_PORT', defaults['rpc_port'])}")
        typer.echo(f"Host P2P Port: {os.environ.get('HOST_P2P_PORT', defaults['p2p_port'])}")
        typer.echo(f"Host Metrics Port: {os.environ.get('HOST_METRICS_PORT', defaults['metrics_port'])}")
        typer.echo(f"Data directory: {data_dir}")
        
        # Build docker-compose command
        cmd = [
            "docker", "compose",
            "-f", str(compose_file),
        ]
        
        # Add profiles based on network and options
        if network in DEV_NETWORKS:
            cmd.extend(["--profile", "dev"])
        
        if with_miner and network not in DEV_NETWORKS:
            cmd.extend(["--profile", "miner"])
        
        if build:
            cmd.extend(["up", "--build"])
        else:
            cmd.append("up")
        
        if detach:
            cmd.append("-d")

        typer.echo(f"\nRunning: {' '.join(cmd)}")

        compose_env = {
            **os.environ,
            "ANIMICA_NETWORK": network,
            "ANIMICA_DATA_DIR": data_dir,
            "ANIMICA_P2P_DATA_DIR": str(Path(data_dir) / "p2p"),
        }
        if bootstrap_node:
            compose_env.setdefault("ANIMICA_RPC_BOOTSTRAP_NODE", "1")

        try:
            result = subprocess.run(
                cmd,
                cwd=compose_file.parent,
                check=False,
                env=compose_env,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                typer.secho(f"✓ {network} started successfully!", fg=typer.colors.GREEN, bold=True)
                successful_networks.append(network)
            else:
                typer.secho(
                    f"✗ {network} failed to start (exit code {result.returncode})",
                    fg=typer.colors.RED,
                    bold=True
                )
                if result.stderr:
                    typer.echo(f"Error output:\n{result.stderr}", err=True)
                failed_networks.append(network)
                
        except FileNotFoundError:
            typer.secho(
                f"✗ Error: 'docker' command not found.",
                fg=typer.colors.RED
            )
            typer.echo("Please install Docker and Docker Compose.", err=True)
            failed_networks.append(network)
            break  # No point continuing if docker is not installed
        except Exception as e:
            typer.secho(
                f"✗ {network} failed with unexpected error: {e}",
                fg=typer.colors.RED
            )
            failed_networks.append(network)
    
    # Print summary
    typer.secho(f"\n{'='*60}", fg=typer.colors.CYAN)
    typer.secho("Summary", fg=typer.colors.CYAN, bold=True)
    typer.secho(f"{'='*60}", fg=typer.colors.CYAN)
    
    if successful_networks:
        typer.secho(
            f"✓ Successfully started ({len(successful_networks)}): {', '.join(successful_networks)}",
            fg=typer.colors.GREEN
        )
    
    if skipped_networks:
        typer.secho(
            f"⚠ Skipped ({len(skipped_networks)}): {', '.join(skipped_networks)}",
            fg=typer.colors.YELLOW
        )
    
    if failed_networks:
        typer.secho(
            f"✗ Failed ({len(failed_networks)}): {', '.join(failed_networks)}",
            fg=typer.colors.RED,
            bold=True
        )
        typer.echo(
            "\nSome networks failed to start. Check the error messages above for details.",
            err=True
        )
        raise typer.Exit(code=1)
    
    if not successful_networks and not failed_networks:
        typer.secho(
            "⚠ No networks were started. All were skipped.",
            fg=typer.colors.YELLOW
        )
        raise typer.Exit(code=1)
    
    typer.secho("\n✓ All requested networks started successfully!", fg=typer.colors.GREEN, bold=True)


@app.command()
def down(
    volumes: bool = typer.Option(
        False,
        "--volumes",
        "-v",
        help="Remove volumes (WARNING: deletes blockchain data)"
    ),
) -> None:
    """
    Stop and tear down an Animica node.
    
    This command stops all services started by 'node up' and optionally
    removes associated volumes. By default, blockchain data is preserved
    unless --volumes flag is used.
    
    The command automatically uses the correct compose file based on the
    active network setting.
    
    Before running this command, ensure you have set a network using:
      animica network set <network>
    
    Examples:
      animica node down
      animica node down --volumes  # Also delete blockchain data
    
    WARNING: Using --volumes will delete all blockchain data for the active network!
    """
    # Enforce network requirement
    network = _ensure_network_set()
    
    # Get network-specific compose file
    compose_file = _get_compose_file(network)
    net_cfg = load_network_config(network)
    data_dir = str(Path(net_cfg.data_dir).expanduser())
    
    typer.secho(f"Stopping node for network: {network}", fg=typer.colors.CYAN, bold=True)
    typer.echo(f"Using compose file: {compose_file}")
    
    if volumes:
        typer.secho(
            f"\n⚠ WARNING: --volumes flag will delete all {network} blockchain data!",
            fg=typer.colors.YELLOW,
            bold=True
        )
    
    # Build docker-compose command
    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
    ]
    
    # Add profiles for devnet
    if network in DEV_NETWORKS:
        cmd.extend(["--profile", "dev"])
    
    cmd.append("down")
    
    if volumes:
        cmd.append("-v")
    
    typer.echo(f"\nRunning: {' '.join(cmd)}\n")

    compose_env = {
        **os.environ,
        "ANIMICA_NETWORK": network,
        "ANIMICA_DATA_DIR": data_dir,
        "ANIMICA_P2P_DATA_DIR": str(Path(data_dir) / "p2p"),
    }

    try:
        result = subprocess.run(
            cmd,
            cwd=compose_file.parent,
            check=False,
            env=compose_env,
        )
        
        if result.returncode == 0:
            typer.secho("✓ Node stopped successfully!", fg=typer.colors.GREEN, bold=True)
            if volumes:
                typer.echo(f"All volumes and {network} blockchain data have been removed.")
            else:
                typer.echo(f"{network.capitalize()} blockchain data has been preserved in volumes.")
                typer.echo("Use 'animica node down --volumes' to remove data.")
        else:
            typer.secho(
                f"Error: Node shutdown failed with exit code {result.returncode}",
                fg=typer.colors.RED,
                err=True
            )
            raise typer.Exit(code=result.returncode)
            
    except FileNotFoundError:
        typer.echo(
            "Error: 'docker' command not found. Please install Docker and Docker Compose.",
            err=True
        )
        raise typer.Exit(code=1)
    except KeyboardInterrupt:
        typer.echo("\n\nInterrupted by user", err=True)
        raise typer.Exit(code=130)


@p2p_app.command("status")
def p2p_status(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
) -> None:
    """Show P2P peer counts and local listen/seeds configuration."""
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    peer_count, peer_count_error = _get_peer_count(url, rpc_timeout)
    peers, peers_error = _get_peers(url, rpc_timeout)
    peer_error = peer_count_error or peers_error
    peers_len = len(peers) if peers else None
    p2p_status, p2p_status_error = _get_p2p_status(url, rpc_timeout)

    typer.echo(f"RPC URL: {url}")
    if peer_error:
        typer.echo(f"Peer query error: {peer_error}")
    else:
        if peer_count is not None:
            typer.echo(f"Connected peers: {peer_count}")
        elif peers_len is not None:
            typer.echo(f"Connected peers: {peers_len}")
        if peers_len is not None:
            typer.echo(f"Peer details available: {peers_len}")
    if p2p_status_error:
        typer.echo(f"P2P status: unavailable ({p2p_status_error})")
    elif p2p_status:
        typer.echo(f"P2P running: {p2p_status.get('p2p_running')}")
        typer.echo(
            "P2P peers: total={total} inbound={inbound} outbound={outbound}".format(
                total=p2p_status.get("peers_total"),
                inbound=p2p_status.get("peers_inbound"),
                outbound=p2p_status.get("peers_outbound"),
            )
        )
        typer.echo(
            "Bootstrap attempts (last 5m): {count}".format(
                count=p2p_status.get("bootstrap_attempts_last_5m")
            )
        )

    cfg, cfg_err = _load_p2p_config()
    if cfg_err:
        typer.echo(f"Local P2P config unavailable: {cfg_err}")
        return

    seeds = list(getattr(cfg, "seeds", []) or [])
    listen_tcp = getattr(cfg, "listen_tcp", None)
    data_dir = getattr(cfg, "data_dir", None)
    typer.echo("Local P2P config:")
    if listen_tcp:
        typer.echo(f"  Listen TCP: {listen_tcp[0]}:{listen_tcp[1]}")
    if data_dir:
        typer.echo(f"  Data dir: {data_dir}")
    typer.echo(f"  Seeds loaded: {len(seeds)}")


@p2p_app.command("peers")
def p2p_peers(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
) -> None:
    """List connected peers via RPC."""
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    peers, peer_error = _get_peers(url, rpc_timeout)
    if peer_error and not peers:
        typer.echo(f"Error: Unable to retrieve peers ({peer_error})", err=True)
        raise typer.Exit(code=1)
    typer.echo(_pretty(peers))


@p2p_app.command("add")
def p2p_add(
    address: str = typer.Argument(..., help="Peer address (multiaddr or host:port)"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
) -> None:
    """Dial a peer address via RPC."""
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    result = asyncio.run(
        rpc_call("p2p.addPeer", [address], rpc_url=url, timeout=rpc_timeout)
    )
    typer.echo(_pretty(result))


@p2p_app.command("remove")
def p2p_remove(
    peer_id: str = typer.Argument(..., help="Peer ID to disconnect"),
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="JSON-RPC endpoint", envvar=RPC_ENV
    ),
    timeout: Optional[float] = typer.Option(
        None,
        "--timeout",
        help=f"JSON-RPC request timeout in seconds (default: {describe_timeout(DEFAULT_RPC_TIMEOUT)})",
        envvar=RPC_TIMEOUT_ENV,
    ),
) -> None:
    """Disconnect a peer by ID via RPC."""
    url = _resolve_rpc_url(rpc_url)
    try:
        rpc_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)
    result = asyncio.run(
        rpc_call("p2p.removePeer", [peer_id], rpc_url=url, timeout=rpc_timeout)
    )
    typer.echo(_pretty(result))


@p2p_app.command("seeds")
def p2p_seeds() -> None:
    """Show seeds from local P2P config and bootstrap cache."""
    cfg, cfg_err = _load_p2p_config()
    if cfg_err:
        typer.echo(f"Local P2P config unavailable: {cfg_err}")
        raise typer.Exit(code=1)

    seeds = list(getattr(cfg, "seeds", []) or [])
    typer.echo("Configured seeds:")
    if seeds:
        for seed in seeds:
            typer.echo(f"  {seed}")
    else:
        typer.echo("  (none)")

    net_cfg = load_network_config()
    bootstrap_seeds = _bootstrap_seeds_from_state(net_cfg)
    typer.echo("Bootstrap cached seeds:")
    if bootstrap_seeds:
        for seed in bootstrap_seeds:
            typer.echo(f"  {seed}")
    else:
        typer.echo("  (none)")


@p2p_app.command("config")
def p2p_config() -> None:
    """Print the resolved local P2P configuration."""
    cfg, cfg_err = _load_p2p_config()
    if cfg_err:
        typer.echo(f"Local P2P config unavailable: {cfg_err}")
        raise typer.Exit(code=1)
    if hasattr(cfg, "to_dict"):
        typer.echo(_pretty(cfg.to_dict()))
    else:
        typer.echo(_pretty(cfg))


app.add_typer(p2p_app, name="p2p")


if __name__ == "__main__":  # pragma: no cover
    app()
