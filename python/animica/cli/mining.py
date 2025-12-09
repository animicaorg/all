"""Mining operations CLI for Animica.

Provides commands for:
  - Mining blocks via RPC (mine-blocks)
  - Running the Stratum pool server (run-pool)
  - Inspecting pool configuration (show-config)
  - Generating payout addresses (generate-payout-address)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import typer
from animica.config import load_network_config

try:
    from animica.cli.wallet import (WalletEntry, _wallet_file_path,
                                    create_wallet)
    from animica.stratum_pool import cli as pool_cli
    from animica.stratum_pool.config import PoolConfig, load_config_from_env

    HAVE_STRATUM = True
except Exception:
    HAVE_STRATUM = False

app = typer.Typer(help="Mining operations and Stratum pool management.")

RPC_ENV = "ANIMICA_RPC_URL"
DB_ENV = "ANIMICA_MINING_POOL_DB_URL"
LOG_LEVEL_ENV = "ANIMICA_MINING_POOL_LOG_LEVEL"
STRATUM_BIND_ENV = "ANIMICA_STRATUM_BIND"
API_BIND_ENV = "ANIMICA_POOL_API_BIND"


def _ensure_network_env() -> None:
    cfg = load_network_config()
    os.environ.setdefault("ANIMICA_NETWORK", cfg.name)
    os.environ.setdefault(RPC_ENV, cfg.rpc_url)


def _ensure_stratum_available() -> None:
    if not HAVE_STRATUM:
        typer.echo(
            "Error: Stratum pool modules required. "
            "Ensure 'animica[stratum]' is installed.",
            err=True,
        )
        raise typer.Exit(1)


def _validate_bech32_address(address: str) -> bool:
    """
    Validate that a string is a valid Animica Bech32 address.
    
    Args:
        address: Address string to validate
        
    Returns:
        bool: True if valid Animica Bech32 address, False otherwise
    """
    try:
        from pq.py.address import validate_address
        
        # Must start with 'anim1' prefix
        if not address.startswith("anim1"):
            return False
        
        # Use PQ library validation
        validate_address(address, expect_hrp="anim")
        return True
    except Exception:
        return False


def _resolve_wallet_label_to_address(label: str, wallet_file: Optional[Path] = None) -> Optional[str]:
    """
    Resolve a wallet label to its Bech32 address.
    
    Args:
        label: Wallet label to look up
        wallet_file: Optional wallet file path (uses default if None)
        
    Returns:
        str: Bech32 address if found, None otherwise
    """
    try:
        from animica.cli.wallet import _load_store, _wallet_file_path
        
        path = _wallet_file_path(wallet_file)
        store = _load_store(path)
        
        # Search for wallet by label
        for entry in store.get("wallets", []):
            if entry.get("label") == label:
                return entry.get("address")
        
        return None
    except Exception:
        return None


def _resolve_payout_address(address_or_label: str) -> str:
    """
    Resolve a payout address from either a wallet label or raw Bech32 address.
    
    Priority:
    1. If it's a valid Bech32 address (starts with 'anim1' and passes validation), use it directly
    2. Otherwise, try to resolve as a wallet label
    3. If both fail, raise an error
    
    Args:
        address_or_label: Either a Bech32 address or wallet label
        
    Returns:
        str: Resolved Bech32 address
        
    Raises:
        typer.Exit: If address cannot be resolved
    """
    # First check if it's a valid Bech32 address
    if _validate_bech32_address(address_or_label):
        return address_or_label
    
    # Try to resolve as a wallet label
    resolved_address = _resolve_wallet_label_to_address(address_or_label)
    if resolved_address:
        return resolved_address
    
    # Could not resolve - fail fast with clear error
    typer.secho(
        f"Error: '{address_or_label}' is neither a valid Animica Bech32 address "
        f"(must start with 'anim1') nor a known wallet label.",
        fg=typer.colors.RED,
        err=True,
    )
    typer.secho(
        "Use 'animica wallet list' to see available wallet labels, "
        "or provide a valid Bech32 address.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    raise typer.Exit(2)


@app.command("run-pool")
def run_pool(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url", help="Animica node RPC URL", envvar=RPC_ENV
    ),
    db_url: Optional[str] = typer.Option(
        None, "--db-url", help="Database URL", envvar=DB_ENV
    ),
    stratum_bind: Optional[str] = typer.Option(
        None, "--stratum-bind", help="Stratum bind address", envvar=STRATUM_BIND_ENV
    ),
    api_bind: Optional[str] = typer.Option(
        None, "--api-bind", help="API bind address", envvar=API_BIND_ENV
    ),
    log_level: Optional[str] = typer.Option(
        None, "--log-level", help="Log level", envvar=LOG_LEVEL_ENV
    ),
) -> None:
    """Start the Animica Stratum mining pool."""
    _ensure_stratum_available()
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
    _ensure_stratum_available()
    _ensure_network_env()
    # load_config_from_env is provided by animica.stratum_pool when installed
    try:
        cfg: PoolConfig = load_config_from_env()
    except Exception as e:
        typer.echo(
            "Error: could not load pool config; ensure animica[stratum] is installed",
            err=True,
        )
        raise typer.Exit(1)
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
    wallet_file: Optional[Path] = typer.Option(
        None, "--wallet-file", help="Wallet store for generated address"
    ),
    label: str = typer.Option(
        "pool-payout", "--label", help="Label for the generated wallet"
    ),
) -> None:
    """Generate a dev wallet for pool payouts using the wallet CLI helpers."""
    # Delegate to wallet module for key generation (no stratum dependency required)
    try:
        from animica.cli.wallet import (_generate_entry, _load_store,
                                        _save_store, _wallet_file_path)

        path = _wallet_file_path(wallet_file)
        store = _load_store(path)
        entry = _generate_entry(label, allow_fallback=True)
        store.setdefault("wallets", []).append(entry.to_dict())
        _save_store(path, store)
        typer.echo(f"Generated payout address {entry.address} (label: {entry.label})")
    except Exception as e:
        typer.echo(f"Error generating payout address: {e}", err=True)
        raise typer.Exit(1)


@app.command("mine-blocks")
def mine_blocks(
    address: str = typer.Option(
        ...,
        "--address",
        help="Payout address: wallet label (e.g., 'premine') or Animica Bech32 address (e.g., 'anim1...')",
    ),
    count: int = typer.Option(
        ...,
        "--count",
        help="Number of blocks to mine (must be > 0)",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Node JSON-RPC endpoint URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Mine blocks with proof-of-work to a specified payout address.
    
    This command performs actual mining by iterating through nonces until finding
    block hashes that meet the current difficulty target (derived from the network's
    theta parameter). Block rewards are credited to the specified payout address.
    
    Address Resolution:
      The --address parameter accepts either:
      1. A wallet label (e.g., 'premine') - resolved from ~/.animica/wallets.json
      2. A raw Animica Bech32 address (e.g., 'anim1...') - used directly
      If neither is valid, the command fails with exit code 2.
    
    The mining process:
    1. Retrieves current chain head and difficulty (theta) from the node
    2. Iterates through nonces to find a valid block hash
    3. Submits the mined block when a hash meets the target
    4. Credits the block reward to the payout address
    
    Persistence:
      - Chain state is stored under ~/.animica/chain-{chain_id}/ by default
      - Use ANIMICA_RPC_DB_URI to specify a custom database location
    
    Difficulty:
      - Target is calculated from the network's theta (acceptance threshold)
      - Set ANIMICA_MINER_MAX_NONCE to limit nonce iterations (default: 100000)
      - Higher theta means harder mining (lower target)
    
    Examples:
        # Mine 5 blocks to a wallet label
        animica miner mine-blocks --address premine --count 5
        
        # Mine to a bech32 address
        animica miner mine-blocks --address anim1zqp8gjpns43wcy2p8rj3w3uvn2dwkxx99nkwg020u4ql6gu3yfqzgzglw560f --count 10
        
        # Mine with custom RPC endpoint
        animica miner mine-blocks --address premine --count 10 --rpc-url http://localhost:8545
    
    Environment variables:
        ANIMICA_RPC_URL          - Node RPC endpoint (default: http://127.0.0.1:8545/rpc)
        ANIMICA_MINER_ADDRESS    - Default payout address if --address not specified
        ANIMICA_MINER_MAX_NONCE  - Max nonce iterations per block (default: 100000)
    
    Note: For backward compatibility with older nodes, if the node doesn't support
    payout address selection, blocks will be mined to the node's default miner address.
    """
    # Note: This repository uses a custom stub implementation of Typer
    # (see python/typer/__init__.py) that doesn't automatically parse type annotations.
    # The stub Typer passes string values for integer options, so we need to convert manually.
    # This is intentional to keep the stub lightweight and avoid external dependencies.
    # When using the real Typer library, this conversion would be automatic.
    if isinstance(count, str):
        try:
            count = int(count)
        except ValueError:
            typer.secho(
                f"Error: count must be a valid integer, got {count}",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)
    
    # Validate count
    if count <= 0:
        typer.secho(
            f"Error: count must be greater than 0, got {count}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    
    # Validate and resolve address (label or raw Bech32)
    if not address or not address.strip():
        typer.secho(
            "Error: address is required",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    
    # Resolve address from label or validate raw Bech32 address
    resolved_address = _resolve_payout_address(address.strip())
    
    # Resolve RPC URL
    url = rpc_url or os.environ.get("ANIMICA_RPC_URL") or load_network_config().rpc_url
    
    # Try to import RPC client
    rpc_client = None
    try:
        from sdk.python.omni_sdk.rpc.http import RpcClient
        rpc_client = RpcClient
    except (ImportError, ModuleNotFoundError, RuntimeError):
        try:
            from omni_sdk.rpc.http import RpcClient  # type: ignore
            rpc_client = RpcClient
        except (ImportError, ModuleNotFoundError, RuntimeError):
            pass
    
    if rpc_client is None:
        typer.secho(
            "Error: RpcClient not available. Please install omni_sdk: pip install -e sdk/python",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(3)
    
    typer.echo(
        f"Mining {count} block(s) with payout to address {resolved_address} via RPC {url}"
    )
    
    # Import time for sleep between blocks
    import time
    
    # Minimum block interval (2 seconds, CLI-only throttling, not consensus)
    # This ensures we don't overwhelm the node when mining multiple blocks
    MIN_BLOCK_INTERVAL_SECONDS = 2.0
    
    # JSON-RPC error code constant for invalid params (JSON-RPC 2.0 spec)
    JSONRPC_INVALID_PARAMS = -32602
    
    try:
        # Import RpcError for proper exception handling
        try:
            from omni_sdk.errors import RpcError, JsonRpcCode
        except ImportError:
            # Fallback if SDK not available or older version
            RpcError = None  # type: ignore
            JsonRpcCode = None  # type: ignore
        
        with rpc_client(url, timeout=30.0) as client:
            total_mined = 0
            final_height = 0
            
            # Mine blocks one at a time with delay between them
            for i in range(count):
                # Call miner.mine RPC method with address parameter (mine 1 block at a time)
                # For backward compatibility, try with address first, fall back if not supported
                try:
                    result = client.request("miner.mine", {"count": 1, "address": resolved_address})
                except Exception as e:
                    # If the RPC rejects the address parameter (older node), try without it
                    # Check for INVALID_PARAMS error code or presence of "address" in error message
                    is_param_error = False
                    if RpcError is not None and isinstance(e, RpcError):
                        is_param_error = (
                            e.code == JsonRpcCode.INVALID_PARAMS if JsonRpcCode else e.code == JSONRPC_INVALID_PARAMS
                        )
                    elif "address" in str(e).lower() or "unexpected" in str(e).lower():
                        is_param_error = True
                    
                    if is_param_error:
                        typer.secho(
                            "Warning: Node does not support payout address selection (older version). "
                            "Mining to node's default miner address.",
                            fg=typer.colors.YELLOW,
                        )
                        result = client.request("miner.mine", [1])
                    else:
                        raise
                
                mined = result.get("mined", 0)
                final_height = result.get("height", 0)
                
                if mined > 0:
                    total_mined += mined
                    typer.echo(f"  Block {i + 1}/{count} mined (height: {final_height})")
                else:
                    typer.secho(
                        f"Warning: Block {i + 1}/{count} failed to mine",
                        fg=typer.colors.YELLOW,
                    )
                    break
                
                # Sleep between blocks (except after the last one)
                if i < count - 1:
                    time.sleep(MIN_BLOCK_INTERVAL_SECONDS)
            
            if total_mined == 0:
                typer.secho(
                    "Warning: No blocks were mined (may have failed)",
                    fg=typer.colors.YELLOW,
                )
                raise typer.Exit(4)
            elif total_mined < count:
                typer.secho(
                    f"Warning: Only {total_mined} of {count} requested blocks were mined",
                    fg=typer.colors.YELLOW,
                )
            
            typer.secho(
                f"✓ Successfully mined {total_mined} block(s). New chain height: {final_height}",
                fg=typer.colors.GREEN,
                bold=True,
            )
    
    except (RuntimeError, ConnectionError, OSError, TimeoutError) as e:
        typer.secho(
            f"Error: Failed to connect to RPC: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(5)
    except Exception as e:
        typer.secho(
            f"Error: Failed to mine blocks via RPC: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(5)


if __name__ == "__main__":  # pragma: no cover
    app()
