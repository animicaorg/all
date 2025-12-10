"""
animica.cli.tx — Transaction subcommands.

Implements:
  - animica tx build      Build a transaction
  - animica tx sign       Sign a transaction
  - animica tx send       Build, sign, and broadcast
  - animica tx simulate   Dry-run a transaction
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional, Tuple

import typer

try:
    from omni_sdk.rpc.http import RpcClient

    HAVE_RPC = True
except Exception:
    HAVE_RPC = False

try:
    from pq.py.signing import sign_message

    HAVE_SIGN = True
except Exception:
    HAVE_SIGN = False

from animica.config import load_network_config

app = typer.Typer(help="Transaction operations (build, sign, send, simulate)")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    """Resolve RPC URL from option, env, or config."""
    if rpc_url:
        return rpc_url
    cfg = load_network_config()
    return cfg.rpc_url


def _ensure_rpc_available() -> None:
    if not HAVE_RPC:
        typer.echo(
            "Error: omni_sdk.rpc.http.RpcClient required. "
            "Ensure 'omni_sdk' is installed.",
            err=True,
        )
        raise typer.Exit(1)


def _request_rpc(method: str, params: Optional[list], rpc_url: Optional[str]):
    url = _resolve_rpc_url(rpc_url)
    if HAVE_RPC:
        client = RpcClient(url, timeout=10.0)
        return client.request(method, params)
    else:
        import httpx

        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        resp = httpx.post(url, json=payload, timeout=10.0)
        resp.raise_for_status()
        parsed = resp.json()
        if "error" in parsed:
            raise RuntimeError(parsed.get("error"))
        return parsed.get("result")


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def resolve_chain_id(rpc_url: Optional[str], cli_chain_id: Optional[int]) -> int:
    """
    Resolve chain ID with validation.
    
    Args:
        rpc_url: RPC endpoint URL (will be resolved if None)
        cli_chain_id: Chain ID from CLI flag or env var (None = auto-detect)
    
    Returns:
        Validated chain ID to use for transaction signing
    
    Raises:
        typer.Exit: If CLI chain ID doesn't match node's chain ID
    
    Logic:
        1. Query node's chain ID via chain.getChainId RPC
        2. If cli_chain_id is None: return node's chain ID
        3. If cli_chain_id is set:
           - If matches node: return it
           - If differs: fail with clear error message
    """
    # Fetch node's chain ID
    try:
        node_chain_id_result = _request_rpc("chain.getChainId", [], rpc_url)
        node_chain_id = int(node_chain_id_result) if node_chain_id_result else None
    except Exception as e:
        # If we can't reach the node, fail gracefully
        typer.echo(
            f"Error: Could not query node's chain ID: {e}",
            err=True,
        )
        typer.echo(
            "Ensure the node is running and accessible via RPC.",
            err=True,
        )
        raise typer.Exit(1)
    
    if node_chain_id is None:
        typer.echo(
            "Error: Node returned invalid chain ID (null/empty)",
            err=True,
        )
        raise typer.Exit(1)
    
    # Case 1: No CLI chain ID specified -> use node's chain ID
    if cli_chain_id is None:
        return node_chain_id
    
    # Case 2: CLI chain ID specified -> validate it matches node
    if cli_chain_id == node_chain_id:
        return cli_chain_id
    
    # Case 3: Mismatch -> fail with clear error
    typer.echo("=" * 60, err=True)
    typer.echo("Error: Chain ID mismatch between CLI and node", err=True)
    typer.echo("=" * 60, err=True)
    typer.echo(f"CLI chain ID:  {cli_chain_id}", err=True)
    typer.echo(f"Node chain ID: {node_chain_id}", err=True)
    typer.echo("", err=True)
    typer.echo("The transaction would be rejected by the node.", err=True)
    typer.echo("", err=True)
    typer.echo("Solutions:", err=True)
    typer.echo(f"  1. Remove --chain-id flag (auto-detect from node: {node_chain_id})", err=True)
    typer.echo(f"  2. Set --chain-id {node_chain_id} to match the node", err=True)
    typer.echo(f"  3. Unset ANIMICA_CHAIN_ID env var if set", err=True)
    typer.echo("  4. Connect to a different node with --rpc-url", err=True)
    typer.echo("=" * 60, err=True)
    raise typer.Exit(1)


def _get_wallet_path(wallet_file: Optional[Path]) -> Path:
    """Get wallet file path from option, env, or default."""
    if wallet_file is not None:
        return Path(wallet_file)
    import os
    env_path = os.environ.get("ANIMICA_WALLETS_FILE")
    if env_path:
        return Path(env_path)
    return Path.home() / ".animica" / "wallets.json"


def _resolve_sender(identifier: str, wallet_file: Optional[Path]) -> Tuple[str, Any]:
    """
    Resolve sender identifier to (address, wallet_entry).
    
    Identifier can be:
    - A wallet label (e.g., "alice")
    - A Bech32 address (e.g., "anim1...")
    
    Returns the resolved address and the wallet entry (for signing).
    """
    from animica.cli.wallet import _load_store, _find_wallet, _entry_from_dict
    
    wallet_path = _get_wallet_path(wallet_file)
    
    if not wallet_path.exists():
        typer.echo(
            f"Error: Wallet store not found at {wallet_path}",
            err=True,
        )
        typer.echo("Create a wallet with: animica wallet create --label <name>", err=True)
        raise typer.Exit(1)
    
    store = _load_store(wallet_path)
    
    # Try to find by label or address
    try:
        wallet_entry = _find_wallet(store, identifier=identifier)
        return wallet_entry.address, wallet_entry
    except typer.Exit:
        # Not found in wallet - check if it's a valid address
        if identifier.startswith("anim1"):
            # It's a Bech32 address but not in wallet
            typer.echo(
                f"Error: Address {identifier} not found in wallet",
                err=True,
            )
            typer.echo(
                f"Available wallets: {', '.join(e.get('label', e.get('address')) for e in store.get('wallets', []))}",
                err=True,
            )
        else:
            typer.echo(
                f"Error: Wallet label '{identifier}' not found",
                err=True,
            )
            typer.echo(
                f"Available labels: {', '.join(e.get('label', '') for e in store.get('wallets', []) if e.get('label'))}",
                err=True,
            )
        raise typer.Exit(1)


def _resolve_destination(addr: str) -> str:
    """
    Validate and resolve destination address.
    
    Accepts Bech32 addresses (anim1...).
    """
    if not addr or not isinstance(addr, str):
        typer.echo("Error: destination address is required", err=True)
        raise typer.Exit(1)
    
    # Basic validation - should start with anim1
    if not addr.startswith("anim1"):
        typer.echo(
            f"Error: invalid destination address '{addr}' (must start with 'anim1')",
            err=True,
        )
        raise typer.Exit(1)
    
    # Optional: validate with pq.py.address if available
    try:
        from pq.py.address import validate_address
        validate_address(addr, expect_hrp="anim")
    except ImportError:
        pass  # PQ not available, basic validation is enough
    except Exception as e:
        typer.echo(f"Error: invalid destination address: {e}", err=True)
        raise typer.Exit(1)
    
    return addr


@app.command()
def build(
    from_addr: str = typer.Option(..., "--from", help="Sender address or key index"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address"),
    value: float = typer.Option(0, "--value", help="Amount to transfer (in ANM)"),
    data: Optional[str] = typer.Option(
        None, "--data", help="Contract call data (hex, starts with 0x)"
    ),
    gas: int = typer.Option(200000, "--gas", help="Gas limit"),
    gas_price: Optional[float] = typer.Option(
        None, "--gas-price", help="Gas price (wei/gas)"
    ),
    nonce: Optional[int] = typer.Option(
        None, "--nonce", help="Transaction nonce (auto-fetched if omitted)"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Save transaction JSON to file"
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Build a transaction (does not sign or broadcast).

    Examples:
      animica tx build --from anim1... --to anim1... --value 1.5 --gas 200000
      animica tx build --from 0 --to anim1... --value 1 --output tx.json
    """
    # Resolve nonce via RPC (uses fallback helper)
    try:
        # Fetch nonce if not provided
        if nonce is None:
            try:
                nonce_result = _request_rpc(
                    "chain_getTransactionCount", [from_addr], rpc_url
                )
                nonce = int(nonce_result) if nonce_result else 0
            except Exception:
                nonce = 0

        # Build transaction
        tx_data = {
            "from": from_addr,
            "to": to_addr,
            "value": int(value * 1e18) if value else 0,  # Convert ANM to wei
            "data": data or "0x",
            "gas": gas,
            "gasPrice": int(gas_price * 1e9) if gas_price else 1000000000,
            "nonce": nonce,
            "chainId": 31337,  # Default to local devnet
        }

        if output:
            output.write_text(json.dumps(tx_data, indent=2))
            typer.echo(f"✓ Transaction saved to {output}")
        else:
            typer.echo("Transaction (unsigned):")
            typer.echo(_pretty(tx_data))

    except Exception as e:
        typer.echo(f"Error building transaction: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def sign(
    tx_file: Path = typer.Option(..., "--file", "-f", help="Transaction JSON file"),
    key_id: Optional[str] = typer.Option(None, "--key", help="Key ID or wallet index"),
) -> None:
    """
    Sign a transaction with a key from the wallet.

    Examples:
      animica tx sign --file tx.json --key 0
      animica tx sign --file tx.json --key path/to/key.json
    """
    # Signing requires wallet integration; this command is intentionally
    # a placeholder until wallet signing is hooked up.

    try:
        if not tx_file.exists():
            typer.echo(f"File not found: {tx_file}", err=True)
            raise typer.Exit(1)

        tx_data = json.loads(tx_file.read_text())

        if not key_id:
            typer.echo("Error: --key is required", err=True)
            raise typer.Exit(1)

        # TODO: Implement actual signing with wallet integration
        typer.echo("Transaction signing not yet fully implemented", err=True)
        typer.echo("TODO: integrate with wallet keystore", err=True)
        raise typer.Exit(1)

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def send(
    from_addr: str = typer.Option(..., "--from", help="Sender address or wallet label"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address"),
    value: float = typer.Option(..., "--value", help="Amount to transfer (in ANM)"),
    gas: Optional[int] = typer.Option(None, "--gas", help="Gas limit (auto if omitted)"),
    gas_price: Optional[float] = typer.Option(
        None, "--gas-price", help="Gas price in gwei (auto if omitted)"
    ),
    nonce: Optional[int] = typer.Option(
        None, "--nonce", help="Transaction nonce (auto-fetched if omitted)"
    ),
    chain_id: Optional[int] = typer.Option(
        None,
        "--chain-id",
        help="Chain ID (auto-fetched if omitted)",
        envvar="ANIMICA_CHAIN_ID",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Build and sign but do not broadcast"
    ),
    wallet_file: Optional[Path] = typer.Option(
        None,
        "--wallet-file",
        help="Override wallet store location",
        envvar="ANIMICA_WALLETS_FILE",
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Build, sign, and broadcast a native value transfer transaction.

    Resolves sender and destination addresses from wallet labels or Bech32 addresses.
    Uses chain params (chainId, decimals) and state RPCs to populate nonce and suggested fee/gas.
    Supports explicit overrides for fee/gas/nonce.

    Examples:
      # Send from wallet label to address
      animica tx send --from alice --to anim1... --value 1.5

      # Send with explicit gas and nonce
      animica tx send --from anim1... --to anim1... --value 2.0 --gas 50000 --nonce 10

      # Dry-run (build and sign without broadcast)
      animica tx send --from alice --to anim1... --value 1.0 --dry-run
    """
    try:
        # Step 0: Check PQ signing availability
        from animica.cli.pq_utils import check_pq_signing_available
        
        available, error_msg = check_pq_signing_available()
        if not available:
            from animica.cli.pq_utils import get_pq_missing_error_message
            typer.echo(get_pq_missing_error_message(), err=True)
            if error_msg:
                typer.echo(f"\nAdditional info: {error_msg}", err=True)
            raise typer.Exit(1)
        
        # Step 1: Resolve sender address and load wallet
        sender_address, wallet_entry = _resolve_sender(from_addr, wallet_file)
        
        # Step 2: Validate destination address
        dest_address = _resolve_destination(to_addr)
        
        # Step 3: Resolve RPC URL
        url = _resolve_rpc_url(rpc_url)
        
        # Step 4: Resolve and validate chain ID
        chain_id = resolve_chain_id(url, chain_id)
        
        # Step 5: Fetch nonce if not provided
        if nonce is None:
            try:
                nonce_result = _request_rpc(
                    "state.getTransactionCount", [sender_address], url
                )
                nonce = int(nonce_result) if nonce_result else 0
            except Exception:
                nonce = 0
        
        # Step 6: Determine gas parameters
        if gas is None:
            # Use SDK to suggest gas limit for transfer
            try:
                from omni_sdk.tx.build import suggest_gas_limit
                gas = suggest_gas_limit("transfer")
            except Exception:
                gas = 21000  # Basic transfer intrinsic gas
        
        if gas_price is None:
            # Fetch suggested gas price from node
            try:
                gas_price_result = _request_rpc("state.suggestGasPrice", [], url)
                # Result in wei, convert to gwei for consistency
                gas_price = int(gas_price_result) / 1e9 if gas_price_result else 1.0
            except Exception:
                gas_price = 1.0  # 1 gwei default
        
        # Convert values to proper units
        value_wei = int(value * 1e18)  # ANM to wei
        max_fee = int(gas_price * 1e9)  # gwei to wei
        
        # Step 7: Build transaction using SDK
        try:
            from omni_sdk.tx.build import transfer
            from omni_sdk.tx.encode import sign_bytes, pack_signed
            from omni_sdk.wallet.signer import PQSigner
            
            tx = transfer(
                from_addr=sender_address,
                to_addr=dest_address,
                amount=value_wei,
                nonce=nonce,
                gas_limit=gas,
                max_fee=max_fee,
                chain_id=chain_id,
            )
        except ImportError:
            typer.echo(
                "Error: omni_sdk required. Ensure SDK is installed.",
                err=True,
            )
            raise typer.Exit(1)
        
        # Step 8: Sign transaction
        try:
            # Create signer from wallet entry
            signer = PQSigner.from_keypair(
                alg_name=wallet_entry.alg_name,
                secret_key=bytes.fromhex(wallet_entry.secret_key_hex),
                public_key=bytes.fromhex(wallet_entry.public_key_hex),
            )
            
            # Sign the transaction
            sign_bytes_data = sign_bytes(tx)
            signature = signer.sign(sign_bytes_data)
            
            # Pack into signed CBOR envelope
            raw_tx = pack_signed(
                tx,
                signature=signature,
                alg_id=signer.alg_id,
                public_key=signer.public_key,
            )
        except Exception as e:
            typer.echo(f"Error signing transaction: {e}", err=True)
            raise typer.Exit(1)
        
        # Step 9: Dry-run or broadcast
        if dry_run:
            # Dry-run: show summary and raw tx
            from omni_sdk.tx.encode import tx_hash_hex
            tx_hash = tx_hash_hex(raw_tx)
            
            # Format value to avoid scientific notation and fix precision issues
            # Round to remove floating point artifacts, then format
            from decimal import Decimal, getcontext
            getcontext().prec = 28  # High precision for decimal arithmetic
            value_decimal = Decimal(str(value))
            # Format without scientific notation
            value_str = format(value_decimal, 'f')
            
            typer.echo("=== Dry-Run Mode ===")
            typer.echo(f"From:       {sender_address}")
            typer.echo(f"To:         {dest_address}")
            typer.echo(f"Value:      {value_str} ANM ({value_wei} wei)")
            typer.echo(f"Gas Limit:  {gas}")
            typer.echo(f"Max Fee:    {gas_price} gwei ({max_fee} wei)")
            typer.echo(f"Nonce:      {nonce}")
            typer.echo(f"Chain ID:   {chain_id}")
            typer.echo(f"Tx Hash:    {tx_hash}")
            typer.echo(f"Raw Size:   {len(raw_tx)} bytes")
            typer.echo(f"Raw Hex:    {raw_tx.hex()[:100]}...")
            typer.echo("\n✓ Transaction built and signed (not broadcast)")
        else:
            # Broadcast transaction
            try:
                from omni_sdk.tx.send import submit_raw
                from omni_sdk.rpc.http import RpcClient
                from omni_sdk.errors import RpcError
                
                rpc = RpcClient(url, timeout=30.0)
                tx_hash = submit_raw(rpc, raw_tx)
                
                typer.echo("=== Transaction Submitted ===")
                typer.echo(f"Tx Hash: {tx_hash}")
                typer.echo(f"From:    {sender_address}")
                typer.echo(f"To:      {dest_address}")
                typer.echo(f"Value:   {value} ANM")
                typer.echo("\n✓ Transaction broadcast successfully")
            except RpcError as e:
                # Display RPC error in a user-friendly format
                typer.echo("=== Transaction Failed ===", err=True)
                if e.method:
                    typer.echo(f"Method:  {e.method}", err=True)
                typer.echo(f"Code:    {e.code}", err=True)
                typer.echo(f"Message: {e.message}", err=True)
                if e.data:
                    typer.echo(f"Data:    {e.data}", err=True)
                raise typer.Exit(1)
            except Exception as e:
                typer.echo(f"Error broadcasting transaction: {e}", err=True)
                raise typer.Exit(1)
    
    except typer.Exit:
        raise
    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def simulate(
    tx_file: Path = typer.Option(..., "--file", "-f", help="Transaction JSON file"),
    from_addr: Optional[str] = typer.Option(
        None, "--from", help="Override sender for simulation"
    ),
    rpc_url: Optional[str] = typer.Option(
        None,
        "--rpc-url",
        help="Override RPC URL",
        envvar="ANIMICA_RPC_URL",
    ),
) -> None:
    """
    Simulate a transaction without broadcasting (dry-run).

    Shows gas usage, return value, and logs.

    Examples:
      animica tx simulate --file tx.json
    """
    try:
        if not tx_file.exists():
            typer.echo(f"File not found: {tx_file}", err=True)
            raise typer.Exit(1)

        tx_data = json.loads(tx_file.read_text())

        if from_addr:
            tx_data["from"] = from_addr

        # Call eth_call (or animica_vm_call) via helper
        result = _request_rpc("eth_call", [tx_data, "latest"], rpc_url)

        typer.echo("Simulation result:")
        typer.echo(_pretty(result))

    except Exception as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(1)
