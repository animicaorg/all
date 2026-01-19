"""
animica.cli.chain — Blockchain query subcommands.

Implements:
  - animica chain head       Current chain head
  - animica chain block      Query block by height or hash
  - animica chain tx         Query transaction
  - animica chain account    Query account state
  - animica chain events     Query events/logs
  - animica chain genesis    Genesis verification and info
"""

from __future__ import annotations

import json
import os
import sys
from typing import Iterable, List, Optional

import typer

from animica.config import load_network_config
from animica.cli.rpc import call_rpc

app = typer.Typer(help="Chain queries (head, blocks, transactions, accounts)")
genesis_app = typer.Typer(help="Genesis verification and info")
app.add_typer(genesis_app, name="genesis")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    """Resolve RPC URL from option, env, or config."""
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    cfg = load_network_config()
    return cfg.rpc_url


def _try_rpc(methods: Iterable[str], params: Optional[list], rpc_url: Optional[str]):
    """
    Try a list of RPC method names until one succeeds.

    Falls back only on "method not found" errors, and raises the last
    non-fallback error if everything fails.
    """

    last_error: Exception | None = None
    for method in methods:
        try:
            return call_rpc(method, params or [], rpc_url)
        except Exception as exc:  # noqa: BLE001 - best-effort fallback handling
            msg = str(exc).lower()
            if "method not found" in msg or "-32601" in msg:
                last_error = exc
                continue
            last_error = exc
            break
    if last_error:
        raise last_error
    return None


def _pretty(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


@app.command()
def head(
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """Display the current chain head (height, hash, timestamp)."""
    # RPC availability handled by _request_rpc fallback

    try:
        head_data = _try_rpc(("chain_getHead", "chain.getHead"), None, rpc_url)
        if head_data is None:
            head_data = _try_rpc(
                ("block_getBlockByNumber", "chain_getBlockByHeight", "chain.getBlockByNumber"),
                ["latest", False, False],
                rpc_url,
            )

        if head_data is None:
            typer.echo("Could not fetch head from node", err=True)
            raise typer.Exit(1)

        # Pretty-print
        typer.echo("Chain Head:")
        typer.echo("-" * 60)
        height = head_data.get("height") or head_data.get("number") or "?"
        hash_val = head_data.get("hash") or head_data.get("blockHash") or "?"
        timestamp = head_data.get("timestamp") or "?"

        typer.echo(f"Height:    {height}")
        typer.echo(f"Hash:      {hash_val}")
        typer.echo(f"Timestamp: {timestamp}")

        # Additional fields if present
        if "parentHash" in head_data:
            typer.echo(f"Parent:    {head_data['parentHash']}")
        if "proposer" in head_data:
            typer.echo(f"Proposer:  {head_data['proposer']}")
        if "stateRoot" in head_data:
            typer.echo(f"State:     {head_data['stateRoot']}")
        if "txsRoot" in head_data:
            typer.echo(f"Txs Root:  {head_data['txsRoot']}")

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def block(
    height_or_hash: str = typer.Argument(..., help="Block height or hash (0x...)"),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """Display block details (transactions, receipts, state changes)."""
    # RPC availability handled by _request_rpc fallback

    try:
        # Determine if it's a height or hash
        is_hash = height_or_hash.startswith("0x")

        if is_hash:
            method_options: List[str] = [
                "block_getBlockByHash",
                "chain_getBlockByHash",
                "chain.getBlockByHash",
            ]
            params = [height_or_hash, False, True]
        else:
            method_options = [
                "block_getBlockByNumber",
                "chain_getBlockByHeight",
                "chain.getBlockByNumber",
            ]
            params = [height_or_hash, False, True]

        block_data = _try_rpc(method_options, params, rpc_url)

        if block_data is None:
            typer.echo(f"Block not found: {height_or_hash}", err=True)
            raise typer.Exit(1)

        typer.echo("Block:")
        typer.echo(_pretty(block_data))

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def tx(
    tx_hash: str = typer.Argument(..., help="Transaction hash (0x...)"),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """Display transaction details and receipt."""
    # RPC availability handled by _request_rpc fallback

    try:
        # Fetch tx and receipt
        tx_data = _try_rpc(
            ["tx_getTransactionByHash", "tx.getTransactionByHash", "chain_getTx"],
            [tx_hash],
            rpc_url,
        )
        receipt = _try_rpc(
            ["tx_getTransactionReceipt", "tx.getTransactionReceipt", "chain_getReceipt"],
            [tx_hash],
            rpc_url,
        )

        if tx_data is None:
            typer.echo(f"Transaction not found: {tx_hash}", err=True)
            raise typer.Exit(1)

        typer.echo("Transaction:")
        typer.echo(_pretty(tx_data))

        if receipt:
            typer.echo("\nReceipt:")
            typer.echo(_pretty(receipt))

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def account(
    address: str = typer.Argument(..., help="Account address (anim1...)"),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """Display account balance and state."""
    # RPC availability handled by _request_rpc fallback

    try:
        # Try different balance methods
        balance = None
        for method in (
            "state.getBalance",
            "state_getBalance",
            "chain_getBalance",
            "eth_getBalance",
        ):
            try:
                params = [address] if method != "eth_getBalance" else [address, "latest"]
                balance = _try_rpc([method], params, rpc_url)
                break
            except Exception:
                continue

        if balance is None:
            typer.echo("Could not fetch account balance", err=True)
            raise typer.Exit(1)

        typer.echo(f"Address: {address}")
        # Balance may be a hex quantity; normalize for readability
        try:
            numeric_balance = int(str(balance), 0)
            typer.echo(f"Balance: {numeric_balance} ({balance})")
        except Exception:
            typer.echo(f"Balance: {balance}")

        # Try to get nonce
        try:
            nonce = _try_rpc(
                ["state.getNonce", "state_getNonce", "chain_getTransactionCount"],
                [address],
                rpc_url,
            )
            if nonce is not None:
                typer.echo(f"Nonce:   {nonce}")
        except Exception:
            pass

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def events(
    from_height: int = typer.Option(0, "--from", help="Start block height"),
    to_height: Optional[int] = typer.Option(
        None, "--to", help="End block height (default: latest)"
    ),
    filter_type: Optional[str] = typer.Option(
        None, "--type", help="Filter by event type"
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """Query chain events/logs in a height range."""
    # RPC availability handled by _request_rpc fallback

    try:
        # First, try native log endpoints if available
        filter_params = {"fromHeight": from_height}
        if to_height is not None:
            filter_params["toHeight"] = to_height
        if filter_type:
            filter_params["type"] = filter_type

        events_data = None
        try:
            events_data = _try_rpc(("chain_getLogs", "eth_getLogs"), [filter_params], rpc_url)
        except Exception:
            events_data = None

        # Fallback: scan blocks and gather logs from receipts
        if events_data is None:
            latest_height = to_height
            if latest_height is None:
                try:
                    head = _try_rpc(["chain_getHead", "chain.getHead"], None, rpc_url)
                    latest_height = int(head.get("height") or head.get("number") or 0)
                except Exception:
                    latest_height = from_height

            collected: list[dict] = []
            for h in range(from_height, (latest_height or from_height) + 1):
                blk = _try_rpc(
                    ["block_getBlockByNumber", "chain_getBlockByHeight", "chain.getBlockByNumber"],
                    [h, False, True],
                    rpc_url,
                )
                if not blk:
                    continue
                tx_hashes = blk.get("transactions") or blk.get("txs") or []
                receipts = blk.get("receipts") or []
                for idx, rec in enumerate(receipts):
                    tx_hash = tx_hashes[idx] if idx < len(tx_hashes) else None
                    logs = rec.get("logs") if isinstance(rec, dict) else None
                    if not logs:
                        continue
                    for log_index, log in enumerate(logs):
                        if filter_type:
                            event_name = None
                            if isinstance(log, dict):
                                event_name = log.get("type") or log.get("event")
                            if event_name and str(event_name).lower() != filter_type.lower():
                                continue
                        entry = {
                            "blockNumber": blk.get("number"),
                            "blockHash": blk.get("hash"),
                            "txHash": tx_hash,
                            "logIndex": log_index,
                            "event": log,
                        }
                        collected.append(entry)
            events_data = collected

        if events_data is None:
            typer.echo("No events found or method not supported", err=True)
            raise typer.Exit(1)

        if isinstance(events_data, list):
            typer.echo(f"Found {len(events_data)} events:")
            typer.echo(_pretty(events_data))
        else:
            typer.echo(_pretty(events_data))

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@genesis_app.command("verify")
def genesis_verify(
    network: Optional[str] = typer.Option(
        None,
        "--network",
        help="Network to verify (mainnet, testnet, devnet)",
        envvar="ANIMICA_NETWORK",
    ),
    chain_id: Optional[int] = typer.Option(
        None,
        "--chain-id",
        help="Chain ID to verify",
        envvar="ANIMICA_CHAIN_ID",
    ),
) -> None:
    """
    Verify genesis file matches pinned genesis hash for the network.

    This command checks that:
    1. The genesis file exists at the expected location
    2. The computed genesis hash matches the pinned hash
    3. All network identity parameters are consistent

    Exit codes:
      0 = verification passed
      1 = verification failed (mismatch or error)
      2 = network not found or invalid parameters
    """
    try:
        # Import here to avoid circular dependencies
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
        from core.network_manifest import get_manifest, verify_genesis

        # Determine which manifest to use
        manifest = None
        if network:
            manifest = get_manifest(network=network)
            if not manifest:
                typer.echo(f"❌ Unknown network: {network}", err=True)
                typer.echo("Available networks: mainnet, testnet, devnet", err=True)
                raise typer.Exit(2)
        elif chain_id is not None:
            manifest = get_manifest(chain_id=chain_id)
            if not manifest:
                typer.echo(f"❌ Unknown chain_id: {chain_id}", err=True)
                typer.echo("Available chain_ids: 0 (mainnet), 2 (testnet), 1337 (devnet)", err=True)
                raise typer.Exit(2)
        else:
            # Try to detect from environment
            from core.network_manifest import get_manifest_for_env
            manifest = get_manifest_for_env()
            if not manifest:
                typer.echo("❌ No network specified", err=True)
                typer.echo("Use --network or --chain-id, or set ANIMICA_NETWORK env var", err=True)
                raise typer.Exit(2)

        # Print verification info
        typer.echo("=" * 80)
        typer.echo("Genesis Verification")
        typer.echo("=" * 80)
        typer.echo(f"Network:           {manifest.network_name}")
        typer.echo(f"Chain ID:          {manifest.chain_id}")
        typer.echo(f"Genesis Path:      {manifest.genesis_path}")
        typer.echo(f"Pinned Hash:       {manifest.pinned_genesis_hash_hex}")
        typer.echo(f"Network Identity:  {manifest.network_identity_string}")
        typer.echo(f"P2P Network ID:    {manifest.p2p_network_id}")
        typer.echo("-" * 80)

        # Verify genesis
        is_valid = verify_genesis(manifest, raise_on_mismatch=False)

        if is_valid:
            typer.echo("✓ Genesis verification PASSED", err=False)
            typer.echo("=" * 80)
            raise typer.Exit(0)
        else:
            typer.echo("❌ Genesis verification FAILED", err=True)
            typer.echo("=" * 80)
            typer.echo("", err=True)
            typer.echo("To fix:", err=True)
            typer.echo("  1. Pull latest code: git pull origin main", err=True)
            typer.echo("  2. Rebuild docker image: docker compose build", err=True)
            typer.echo("  3. Reset chain data: animica node reset", err=True)
            typer.echo("     or: docker compose down -v && docker compose up -d", err=True)
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"❌ Error: {e}", err=True)
        raise typer.Exit(1)


@genesis_app.command("info")
def genesis_info(
    network: Optional[str] = typer.Option(
        None,
        "--network",
        help="Network to show info for (mainnet, testnet, devnet)",
        envvar="ANIMICA_NETWORK",
    ),
    all_networks: bool = typer.Option(
        False,
        "--all",
        help="Show info for all networks",
    ),
) -> None:
    """Display genesis information for one or all networks."""
    try:
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
        from core.network_manifest import all_manifests, get_manifest, get_manifest_for_env

        manifests = []
        if all_networks:
            manifests = all_manifests()
        elif network:
            manifest = get_manifest(network=network)
            if manifest:
                manifests = [manifest]
        else:
            manifest = get_manifest_for_env()
            if manifest:
                manifests = [manifest]

        if not manifests:
            typer.echo("No network specified or found", err=True)
            typer.echo("Use --network or --all", err=True)
            raise typer.Exit(1)

        for manifest in manifests:
            typer.echo("=" * 80)
            typer.echo(f"Network:           {manifest.network_name}")
            typer.echo(f"Chain ID:          {manifest.chain_id}")
            typer.echo(f"Genesis Path:      {manifest.genesis_path}")
            typer.echo(f"Pinned Hash:       {manifest.pinned_genesis_hash_hex}")
            typer.echo(f"Network Identity:  {manifest.network_identity_string}")
            typer.echo(f"P2P Network ID:    {manifest.p2p_network_id}")
            typer.echo(f"Protocol Version:  {manifest.protocol_version}")
            typer.echo(f"Genesis Exists:    {'✓' if manifest.genesis_path.exists() else '✗'}")
            typer.echo("=" * 80)
            typer.echo("")

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@genesis_app.command("identity")
def genesis_identity(
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL to query running node",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Display complete network identity including local config and RPC node state.
    
    Shows both local configuration and RPC-reported values to detect mismatches.
    This is the authoritative diagnostic for network identity issues.
    
    Exit codes:
      0 = identity verified (all sources match)
      1 = mismatch detected or error
    """
    try:
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
        from core.network_manifest import get_manifest_for_env, compute_genesis_hash
        from animica.config import load_network_config
        
        # Get local config
        net_cfg = load_network_config()
        manifest = get_manifest_for_env()
        
        typer.echo("=" * 80)
        typer.echo("Network Identity Report")
        typer.echo("=" * 80)
        typer.echo("")
        
        # Local configuration
        typer.echo("LOCAL CONFIGURATION:")
        typer.echo(f"  Network Name:      {net_cfg.name}")
        typer.echo(f"  Chain ID:          {net_cfg.chain_id}")
        typer.echo(f"  Genesis Path:      {net_cfg.genesis_path}")
        typer.echo(f"  Data Directory:    {net_cfg.data_dir}")
        typer.echo(f"  RPC URL:           {net_cfg.rpc_url}")
        
        if manifest:
            typer.echo(f"  Pinned Genesis:    {manifest.pinned_genesis_hash_hex}")
            
            # Verify genesis file matches pinned hash
            computed_match = None
            if manifest.genesis_path.exists():
                try:
                    computed_hash = compute_genesis_hash(manifest.genesis_path)
                    computed_hex = "0x" + computed_hash.hex()
                    typer.echo(f"  Computed Genesis:  {computed_hex}")
                    computed_match = (computed_hash == manifest.pinned_genesis_hash)
                    if computed_match:
                        typer.echo(f"  File Verification: ✓ MATCH")
                    else:
                        typer.echo(f"  File Verification: ✗ MISMATCH")
                except Exception as e:
                    typer.echo(f"  File Verification: ✗ ERROR: {e}")
            else:
                typer.echo(f"  File Verification: ✗ FILE NOT FOUND")
        
        typer.echo("")
        
        # RPC node state
        typer.echo("RPC NODE STATE:")
        url = _resolve_rpc_url(rpc_url)
        typer.echo(f"  RPC URL:           {url}")
        
        try:
            rpc_chain_id = call_rpc("net.getChainId", [], url)
            typer.echo(f"  Chain ID:          {rpc_chain_id}")
        except Exception as e:
            typer.echo(f"  Chain ID:          (unavailable: {e})")
            rpc_chain_id = None
        
        try:
            rpc_genesis = call_rpc("net.getGenesisHash", [], url)
            typer.echo(f"  Genesis Hash:      {rpc_genesis}")
        except Exception as e:
            typer.echo(f"  Genesis Hash:      (unavailable: {e})")
            rpc_genesis = None
        
        typer.echo("")
        typer.echo("=" * 80)
        
        # Check for mismatches
        has_error = False
        
        if manifest and computed_match is False:
            typer.echo("❌ ERROR: Local genesis file does not match pinned hash!", err=True)
            typer.echo(f"   Genesis file: {manifest.genesis_path}", err=True)
            typer.echo(f"   This indicates the genesis file was modified.", err=True)
            has_error = True
        
        if rpc_chain_id is not None and rpc_chain_id != net_cfg.chain_id:
            typer.echo(f"❌ ERROR: Chain ID mismatch!", err=True)
            typer.echo(f"   Local config: {net_cfg.chain_id}", err=True)
            typer.echo(f"   RPC node:     {rpc_chain_id}", err=True)
            typer.echo(f"   You are querying a different network!", err=True)
            has_error = True
        
        if rpc_genesis and manifest and rpc_genesis != manifest.pinned_genesis_hash_hex:
            typer.echo(f"❌ ERROR: Genesis hash mismatch!", err=True)
            typer.echo(f"   Local pinned: {manifest.pinned_genesis_hash_hex}", err=True)
            typer.echo(f"   RPC node:     {rpc_genesis}", err=True)
            typer.echo(f"   Node was initialized with different genesis!", err=True)
            has_error = True
        
        if has_error:
            typer.echo("=" * 80)
            typer.echo("")
            typer.echo("TO FIX:")
            typer.echo("  1. Ensure ANIMICA_NETWORK and ANIMICA_CHAIN_ID are correct")
            typer.echo("  2. Ensure ANIMICA_RPC_URL points to the right network")
            typer.echo("  3. Reset node data if genesis changed: animica node reset")
            typer.echo("  4. For docker: docker compose down -v && docker compose build && docker compose up -d")
            raise typer.Exit(1)
        else:
            typer.echo("✓ All checks passed - network identity is consistent")
            raise typer.Exit(0)
            
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"❌ Error: {e}", err=True)
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)

