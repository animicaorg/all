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
from animica.coin import COIN_UNIT
from animica.config import load_network_config
from animica.cli.rpc_guard import guard_bootstrap_rpc
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

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

# Supported mining device backends
SUPPORTED_DEVICES = ["cpu", "cuda", "rocm", "opencl", "metal", "auto"]


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
    except (ValueError, ImportError, AttributeError):
        # ValueError: invalid address format
        # ImportError: PQ library not available
        # AttributeError: validate_address function not found
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
    except (ImportError, FileNotFoundError, KeyError, TypeError, ValueError):
        # ImportError: wallet module not available
        # FileNotFoundError: wallet file doesn't exist
        # KeyError/TypeError: malformed wallet store
        # ValueError: invalid JSON in wallet file
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
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
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
    effective_rpc = rpc_url or os.environ.get(RPC_ENV) or load_network_config().rpc_url
    guard_bootstrap_rpc(effective_rpc, allow_remote=allow_remote_rpc, method="miner.runPool")
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
    address: Optional[str] = typer.Argument(
        None,
        help="Payout address (positional): wallet label or Bech32 address",
    ),
    count: int = typer.Option(
        ...,
        "--count",
        help="Number of blocks to mine (must be > 0)",
    ),
    address_opt: Optional[str] = typer.Option(
        None,
        "--address",
        help="Payout address (option, for backward compat): wallet label or Bech32 address",
    ),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
    ),
    device: str = typer.Option(
        "auto",
        "--device",
        help="Mining device backend (cpu, cuda, rocm, opencl, metal, auto). Default: auto (auto-detect best device)",
        envvar="ANIMICA_MINER_DEVICE",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Node JSON-RPC endpoint URL",
        envvar="ANIMICA_RPC_URL",
    ),
    use_proxy: bool = typer.Option(
        False,
        "--use-proxy/--no-proxy",
        help="(DEPRECATED) Use external proxy endpoint (requires ANIMICA_TRUSTED_RPC_URL). Default: disabled (use P2P)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose output (show tx selection details)",
    ),
    no_timeout: bool = typer.Option(
        False,
        "--no-timeout",
        help="Disable RPC timeout (wait indefinitely). Useful for high-load or slow network conditions.",
    ),
) -> None:
    """
    Mine blocks with proof-of-work to a specified payout address.
    
    This command performs actual mining by iterating through nonces until finding
    block hashes that meet the current difficulty target (derived from the network's
    theta parameter). Block rewards are credited to the specified payout address.
    
    Pending mempool transactions are included in mined blocks and executed to update
    balances and nonces. After mining, included transactions are removed from the mempool.
    
    Address Resolution:
      The address can be provided as a positional argument or via --address option:
      1. A wallet label (e.g., 'premine') - resolved from ~/.animica/wallets.json
      2. A raw Animica Bech32 address (e.g., 'anim1...') - used directly
      If neither is valid, the command fails with exit code 2.
    
    Device Selection:
      The --device flag specifies the mining backend to use (CLI-only, not sent to RPC):
      - cpu: CPU backend (pure Python, always available)
      - cuda: NVIDIA CUDA backend (requires CUDA-capable GPU)
      - rocm: AMD ROCm backend (requires ROCm-capable GPU)
      - opencl: OpenCL backend (requires OpenCL-capable device)
      - metal: Apple Metal backend (requires Metal-capable device)
      - auto: Automatically select best available device (default)
      
      When 'auto' is selected (or no device specified), the system automatically detects
      and uses the best available device in priority order: CUDA > ROCm > OpenCL > Metal > CPU.
      Falls back to CPU if no GPU is detected or if detection fails.
      
      Note: Device selection is a local CLI feature for future use. The RPC node
      handles mining execution and does not receive the device parameter.
      
      Default is 'auto'. Can also be set via ANIMICA_MINER_DEVICE environment variable.
    
    The mining process:
    1. Selects pending transactions from mempool (nonce-ordered, fee policy enforced)
    2. Executes transactions to update state (balances, nonces)
    3. Iterates through nonces to find a valid block hash
    4. Includes transactions and receipts in the mined block
    5. Credits the block reward to the payout address
    6. Removes included transactions from the mempool
    
    P2P-First Mining (default):
      By default, mining uses local node validation via P2P consensus (no proxy).
      The node syncs state with peers and validates blocks locally.
      
    Legacy Proxy Mode (DEPRECATED):
      Use --use-proxy to enable the legacy proxy (requires ANIMICA_TRUSTED_RPC_URL):
      - Forwards requests to external endpoint (NOT recommended for production)
      - Automatically retries on transient failures (3 attempts by default)
      - Falls back to local node if external endpoint is unreachable
      - Only for specialized testing scenarios
    
    Persistence:
      - Chain state is stored under ~/.animica/chain-{chain_id}/ by default
      - Use ANIMICA_RPC_DB_URI to specify a custom database location
    
    Difficulty:
      - Target is calculated from the network's theta (acceptance threshold)
      - Set ANIMICA_MINER_MAX_NONCE to limit nonce iterations (default: 100000)
      - Higher theta means harder mining (lower target)
    
    Examples:
        # Mine 5 blocks to a wallet label (uses local P2P validation by default)
        animica miner mine-blocks --count 5 premine
        
        # Mine with --address option (backward compatible)
        animica miner mine-blocks --address premine --count 5
        
        # Mine to a bech32 address with verbose output
        animica miner mine-blocks --count 10 --verbose anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz
        
        # Mine with custom RPC endpoint (local P2P validation)
        animica miner mine-blocks --address premine --count 10 --rpc-url http://localhost:8545
        
        # Mine with CUDA backend
        animica miner mine-blocks --address premine --count 5 --device cuda
        
        # Mine with auto device selection
        animica miner mine-blocks --address premine --count 5 --device auto
        
        # Mine without timeout (useful for high-load scenarios)
        animica miner mine-blocks --address premine --count 10 --no-timeout
    
    Environment variables:
        ANIMICA_RPC_URL             - Node RPC endpoint (default: http://127.0.0.1:8545/rpc)
        ANIMICA_MINER_ADDRESS       - Default payout address if --address not specified
        ANIMICA_MINER_DEVICE        - Default mining device (default: cpu)
        ANIMICA_MINER_MAX_NONCE     - Max nonce iterations per block (default: 100000)
        ANIMICA_TRUSTED_RPC_URL     - (DEPRECATED) External proxy endpoint (only for --use-proxy)
        ANIMICA_PROXY_MAX_RETRIES   - (DEPRECATED) Max proxy retries (default: 3)
        ANIMICA_PROXY_RETRY_DELAY_MS - (DEPRECATED) Delay between retries in ms (default: 1000)
    
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
    
    # Validate device parameter
    device_normalized = device.strip().lower() if isinstance(device, str) else "cpu"
    
    if device_normalized not in SUPPORTED_DEVICES:
        typer.secho(
            f"Error: unsupported device '{device}'. "
            f"Supported devices: {', '.join(SUPPORTED_DEVICES)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    
    # Auto-detect device if requested
    if device_normalized == "auto":
        try:
            import sys
            # Add mining module to path if needed
            repo_root = Path(__file__).resolve().parents[3]
            if str(repo_root) not in sys.path:
                sys.path.insert(0, str(repo_root))
            
            from mining.device import auto_detect_device
            
            device_normalized = auto_detect_device()
            typer.secho(
                f"✓ Auto-detected device: {device_normalized}",
                fg=typer.colors.GREEN,
            )
        except Exception as e:
            typer.secho(
                f"Warning: Could not auto-detect device ({e}). Falling back to CPU.",
                fg=typer.colors.YELLOW,
            )
            device_normalized = "cpu"
    
    # Validate count
    if count <= 0:
        typer.secho(
            f"Error: count must be greater than 0, got {count}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    
    # Resolve address: positional takes precedence over --address option
    # Strip once and reuse; treat empty strings as None
    address_stripped = address.strip() if address and address.strip() else None
    address_opt_stripped = address_opt.strip() if address_opt and address_opt.strip() else None
    final_address = address_stripped or address_opt_stripped
    
    # Validate and resolve address (label or raw Bech32)
    if not final_address:
        typer.secho(
            "Error: address is required (provide as positional arg or --address option)",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    
    # Resolve address from label or validate raw Bech32 address
    resolved_address = _resolve_payout_address(final_address)
    
    # Resolve RPC URL
    url = rpc_url or os.environ.get("ANIMICA_RPC_URL") or load_network_config().rpc_url
    guard_bootstrap_rpc(url, allow_remote=allow_remote_rpc, method="miner.mineBlocks")
    
    # Initialize proxy if enabled (DEPRECATED - proxy is disabled by default)
    proxy = None
    if use_proxy:
        try:
            import sys
            # Add rpc module to path if needed
            repo_root = Path(__file__).resolve().parents[3]
            if str(repo_root) not in sys.path:
                sys.path.insert(0, str(repo_root))
            
            from rpc.proxy import create_proxy, ProxyConfig
            
            # Try to create proxy - will fail if ANIMICA_TRUSTED_RPC_URL is not set
            proxy = create_proxy()
            
            typer.secho(
                f"⚠ DEPRECATED: Proxy mode enabled - forwarding to {proxy.config.trusted_rpc_url}",
                fg=typer.colors.YELLOW,
            )
            typer.secho(
                "  WARNING: Proxy is for testing only. Use P2P networking for production.",
                fg=typer.colors.YELLOW,
            )
            if verbose:
                typer.echo(
                    f"  Max retries: {proxy.config.max_retries}, "
                    f"Retry delay: {proxy.config.retry_delay_ms}ms, "
                    f"Timeout: {proxy.config.timeout_seconds}s"
                )
        except ValueError as e:
            # Proxy not configured (expected - it's disabled by default)
            typer.secho(
                f"Error: Proxy not configured. {e}",
                fg=typer.colors.RED,
                err=True,
            )
            typer.secho(
                "To use proxy: export ANIMICA_TRUSTED_RPC_URL=<endpoint>",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(1)
        except ImportError as e:
            typer.secho(
                f"Error: Could not load proxy module ({e}). Mining directly to {url}",
                fg=typer.colors.YELLOW,
            )
            proxy = None
    
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
    
    mode_str = "with DEPRECATED proxy" if proxy else "with local P2P validation"
    typer.echo(
        f"Mining {count} block(s) {mode_str} with payout to address {resolved_address} via RPC {url}"
    )
    typer.secho(
        f"Using device: {device_normalized}",
        fg=typer.colors.CYAN,
    )
    
    if no_timeout:
        typer.secho(
            "⚠ RPC timeout disabled - operations will wait indefinitely",
            fg=typer.colors.YELLOW,
        )
    
    # Import time for sleep between blocks
    import time
    
    # CLI-only throttling: minimum interval between blocks (not consensus-related)
    # This ensures we don't overwhelm the node when mining multiple blocks.
    # The value is based on target_block_interval_ms from params (2000ms = 2s).
    # Note: This is a fixed delay for simplicity in the CLI. The actual consensus
    # retargeting is handled by the node's PoIES implementation.
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
        
        # Set timeout based on --no-timeout flag (default: no timeout)
        base_timeout = resolve_timeout("RPC timeout", None, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)
        # None means no timeout (wait indefinitely)
        timeout_value = None if no_timeout else base_timeout
        
        with rpc_client(url, timeout=timeout_value) as client:
            total_mined = 0
            final_height = 0
            total_reward = 0
            
            # Mine blocks one at a time with delay between them
            for i in range(count):
                # Call miner.mine RPC method with address parameter (mine 1 block at a time)
                # For backward compatibility, try with address first, fall back if not supported
                
                # Define mining function for proxy fallback
                def mine_via_local():
                    """Fallback: mine directly via local RPC."""
                    if verbose:
                        typer.echo(f"  [Fallback] Mining via local RPC at {url}")
                    # Note: device is CLI-only parameter, not sent to RPC
                    return client.request("miner.mine", {"count": 1, "address": resolved_address})
                
                try:
                    if proxy:
                        # Use proxy with fallback to local node
                        if verbose:
                            typer.echo(f"  [Proxy] Forwarding mining request to trusted RPC")
                        # Note: device is CLI-only parameter for local device selection, not sent to RPC
                        result = proxy.sync_forward_request(
                            "miner.mine",
                            {"count": 1, "address": resolved_address},
                            fallback_handler=mine_via_local,
                        )
                    else:
                        # Direct mining to specified RPC
                        # Note: device is CLI-only parameter, not sent to RPC
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
                        if proxy:
                            result = proxy.sync_forward_request(
                                "miner.mine",
                                [1],
                                fallback_handler=lambda: client.request("miner.mine", [1]),
                            )
                        else:
                            result = client.request("miner.mine", [1])
                    else:
                        raise
                
                mined = result.get("mined", 0)
                final_height = result.get("height", 0)
                block_reward = result.get("totalReward", 0)
                
                if mined > 0:
                    total_mined += mined
                    total_reward += block_reward
                    # Convert nANM to ANM for display (1 ANM = 10^9 nANM)
                    reward_anm = block_reward / COIN_UNIT
                    
                    # Verbose output: show transaction details
                    # Max number of tx hashes to display in verbose mode
                    MAX_VERBOSE_TX_DISPLAY = 5
                    tx_info = ""
                    if verbose:
                        try:
                            # Query block to get transaction count
                            block_result = client.request("chain.getBlockByNumber", [final_height, False])
                            if block_result and "transactions" in block_result:
                                tx_count = len(block_result["transactions"])
                                tx_info = f", txs: {tx_count}"
                                if tx_count > 0:
                                    # List tx hashes if there are any (ensure they're strings and truncate safely)
                                    tx_hashes = block_result["transactions"][:MAX_VERBOSE_TX_DISPLAY]
                                    formatted_hashes = []
                                    for h in tx_hashes:
                                        h_str = str(h) if h else ""
                                        formatted_hashes.append(h_str[:10] + "..." if len(h_str) > 10 else h_str)
                                    tx_info += f" ({', '.join(formatted_hashes)}{'...' if tx_count > MAX_VERBOSE_TX_DISPLAY else ''})"
                        except Exception:
                            # Ignore errors in verbose mode - don't fail mining for this
                            pass
                    
                    typer.echo(
                        f"  Block {i + 1}/{count} mined (height: {final_height}, "
                        f"reward: {reward_anm:.9f} ANM = {block_reward} nANM{tx_info})"
                    )
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
            
            # Display total reward summary
            total_reward_anm = total_reward / COIN_UNIT
            typer.secho(
                f"✓ Successfully mined {total_mined} block(s). "
                f"New chain height: {final_height}. "
                f"Total reward: {total_reward_anm:.9f} ANM ({total_reward} nANM)",
                fg=typer.colors.GREEN,
                bold=True,
            )
    
    except typer.Exit:
        raise
    except (RuntimeError, ConnectionError, OSError, TimeoutError) as e:
        typer.secho(
            f"Error: Failed to connect to RPC: {e}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(5)
    except Exception as e:
        error_str = str(e)
        typer.secho(
            f"Error: Failed to mine blocks via RPC: {error_str}",
            fg=typer.colors.RED,
            err=True,
        )
        # Provide hint about --no-timeout if this is a timeout error
        # Check for timeout indicators: RpcError with code -32098 or "timed out" in message
        is_timeout = False
        if RpcError is not None and isinstance(e, RpcError):
            # Check if this is a timeout error (code -32098 with timeout message)
            is_timeout = e.code == -32098 and "timed out" in error_str.lower()
        elif "timed out" in error_str.lower():
            # Fallback: check error message for timeout indication
            is_timeout = True
        
        if is_timeout and not no_timeout:
            typer.secho(
                "Hint: For long-running operations, consider using --no-timeout flag",
                fg=typer.colors.YELLOW,
                err=True,
            )
        raise typer.Exit(5)


if __name__ == "__main__":  # pragma: no cover
    app()
