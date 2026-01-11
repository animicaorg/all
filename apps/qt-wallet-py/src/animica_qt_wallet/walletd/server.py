from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from animica_qt_wallet.walletd.config import (
    DEFAULT_PORT,
    load_or_create_token,
    resolve_data_dir,
    resolve_log_path,
    resolve_wallet_path,
    resolve_tx_history_path,
    resolve_node_log_path,
    resolve_port,
    resolve_approval_queue_path,
    resolve_app_allowlist_path,
)
from animica_qt_wallet.walletd.node_manager import NodeManager, NodeStatus
from animica_qt_wallet.walletd.wallet_store import WalletStore
from animica_qt_wallet.walletd.tx_history import TxHistory
from animica_qt_wallet.walletd.approval_queue import ApprovalQueue
from animica_qt_wallet.walletd.app_allowlist import AppAllowlist
from animica_qt_wallet.walletd.rate_limiter import RateLimiter

# Import for transaction hash computation
import hashlib


@dataclass
class WalletdState:
    token: str
    rpc_url: str
    log_path: Path
    node_manager: NodeManager
    wallet_store: WalletStore
    tx_history: TxHistory
    approval_queue: ApprovalQueue
    app_allowlist: AppAllowlist
    rate_limiter: RateLimiter
    node_network: str = "mainnet"
    last_error: str | None = None


def _setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_path),
            logging.StreamHandler(),
        ],
    )


async def handle_rpc(request: web.Request) -> web.Response:
    state: WalletdState = request.app["state"]
    token = _extract_token(request)
    if token != state.token:
        return web.json_response({"error": {"code": 401, "message": "Unauthorized"}}, status=401)

    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": {"code": 400, "message": "Invalid JSON"}}, status=400)

    method = payload.get("method")
    params = payload.get("params") or {}
    request_id = payload.get("id")

    try:
        result = await dispatch(method, params, state)
        response = {"jsonrpc": "2.0", "result": result, "id": request_id}
    except Exception as exc:  # noqa: BLE001
        state.last_error = str(exc)
        response = {
            "jsonrpc": "2.0",
            "error": {"code": 500, "message": state.last_error},
            "id": request_id,
        }
    return web.json_response(response)


async def handle_external_rpc(request: web.Request) -> web.Response:
    """Handle external RPC calls from other local applications."""
    state: WalletdState = request.app["state"]
    
    # Verify localhost only
    peername = request.transport.get_extra_info("peername") if request.transport else None
    remote_addr = peername[0] if peername else ""
    if remote_addr not in ("127.0.0.1", "::1"):
        return web.json_response(
            {"error": {"code": 403, "message": "Only localhost connections allowed"}},
            status=403,
        )
    
    # Verify token
    token = _extract_token(request)
    if token != state.token:
        return web.json_response({"error": {"code": 401, "message": "Unauthorized"}}, status=401)
    
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": {"code": 400, "message": "Invalid JSON"}}, status=400)
    
    method = payload.get("method")
    params = payload.get("params") or {}
    request_id = payload.get("id")
    
    try:
        result = await dispatch_external(method, params, state, request)
        response = {"jsonrpc": "2.0", "result": result, "id": request_id}
    except Exception as exc:  # noqa: BLE001
        response = {
            "jsonrpc": "2.0",
            "error": {"code": 500, "message": str(exc)},
            "id": request_id,
        }
    return web.json_response(response)


def _extract_token(request: web.Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header.removeprefix("Bearer ").strip()
    return request.headers.get("X-Auth-Token", "")


def _get_requester_info(request: web.Request) -> dict[str, Any]:
    """Extract information about the requester for approval dialogs."""
    import psutil
    
    # Get peer info
    peername = request.transport.get_extra_info("peername") if request.transport else None
    remote_addr = peername[0] if peername else "unknown"
    remote_port = peername[1] if peername else 0
    
    # Try to identify the process
    process_name = "unknown"
    pid = None
    
    # For localhost connections, try to find the connecting process
    if remote_addr in ("127.0.0.1", "::1", "localhost"):
        try:
            # Find connections matching the remote port
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr.port == remote_port and conn.status == "ESTABLISHED":
                    try:
                        proc = psutil.Process(conn.pid)
                        process_name = proc.name()
                        pid = conn.pid
                        break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
        except Exception:  # noqa: BLE001
            pass
    
    return {
        "remote_addr": remote_addr,
        "remote_port": remote_port,
        "process_name": process_name,
        "pid": pid,
        "user_agent": request.headers.get("User-Agent", ""),
    }


def _compute_tx_hash(raw_tx: bytes) -> bytes:
    """Compute SHA3-256 hash of a transaction."""
    return hashlib.sha3_256(raw_tx).digest()


async def _proxy_to_node(method: str, params: dict[str, Any], node_rpc_url: str | None) -> Any:
    """Proxy a method call to the node RPC endpoint."""
    if not node_rpc_url:
        raise RuntimeError("Node RPC URL not available")
    
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or {},
    }
    
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(node_rpc_url, json=payload) as response:
            if response.status != 200:
                raise RuntimeError(f"Node RPC error: {response.status}")
            data = await response.json()
    
    if "error" in data:
        error_msg = "Node RPC error"
        if isinstance(data.get("error"), dict):
            error_msg = data["error"].get("message", error_msg)
        elif data.get("error"):
            error_msg = str(data["error"])
        raise RuntimeError(error_msg)
    return data.get("result", {})


async def dispatch_external(
    method: str | None,
    params: dict[str, Any],
    state: WalletdState,
    request: web.Request,
) -> Any:
    """Dispatch external RPC calls with approval requirement."""
    import asyncio
    
    requester_info = _get_requester_info(request)
    app_id = requester_info.get("process_name", "unknown")
    
    # Check allowlist
    if not state.app_allowlist.is_allowed(app_id):
        raise RuntimeError(f"Application '{app_id}' is not allowed to access wallet")
    
    # Check rate limit
    client_id = f"{requester_info['remote_addr']}:{requester_info.get('pid', 'unknown')}"
    allowed, reason = state.rate_limiter.check_rate_limit(client_id)
    if not allowed:
        raise RuntimeError(reason)
    
    # Handle approval methods
    if method == "approval.list":
        # List pending approval requests
        requests = state.approval_queue.list_pending()
        return {
            "requests": [
                {
                    "request_id": req.request_id,
                    "method": req.method,
                    "params": req.params,
                    "requester_info": req.requester_info,
                    "created_at": req.created_at,
                }
                for req in requests
            ]
        }
    
    if method == "approval.respond":
        # Respond to an approval request
        request_id = params.get("request_id")
        approved = params.get("approved", False)
        reason = params.get("reason", "")
        
        if not isinstance(request_id, str):
            raise ValueError("request_id must be a string")
        
        if approved:
            # Execute the approved request
            approval_req = state.approval_queue.get_request(request_id)
            if not approval_req:
                raise ValueError(f"Request {request_id} not found")
            
            try:
                # Execute the original method
                result = await _execute_wallet_method(approval_req.method, approval_req.params, state)
                state.approval_queue.approve(request_id, result)
                return {"request_id": request_id, "approved": True, "result": result}
            except Exception as exc:  # noqa: BLE001
                state.approval_queue.deny(request_id, str(exc))
                raise
        else:
            state.approval_queue.deny(request_id, reason or "User denied")
            return {"request_id": request_id, "approved": False}
    
    # Methods that require approval
    if method in ("wallet_requestAccounts", "wallet_signTransaction", "wallet_sendTransaction"):
        # Check if auto-approve is enabled for this app
        if state.app_allowlist.should_auto_approve(app_id):
            # Auto-approve - execute directly
            return await _execute_wallet_method(method, params, state)
        
        # Create approval request
        request_id = state.approval_queue.create_request(method, params, requester_info)
        
        # Wait for approval (with timeout)
        timeout = params.get("_timeout", 120)  # 2 minute default
        start_time = asyncio.get_event_loop().time()
        
        while asyncio.get_event_loop().time() - start_time < timeout:
            approval_req = state.approval_queue.get_request(request_id)
            if approval_req.status == "approved":
                return approval_req.response
            elif approval_req.status == "denied":
                raise RuntimeError(f"Request denied: {approval_req.error}")
            elif approval_req.status == "expired":
                raise RuntimeError("Request expired")
            
            await asyncio.sleep(0.5)
        
        # Timeout
        state.approval_queue.deny(request_id, "Timeout waiting for approval")
        raise RuntimeError("Request timed out waiting for approval")
    
    # Non-approval methods
    if method == "wallet_getChainId":
        node_status = state.node_manager.status()
        if not node_status.running or not node_status.rpc_url:
            raise RuntimeError("Node is not running")
        
        try:
            chain_id_result = await _proxy_to_node("chain.getChainId", {}, node_status.rpc_url)
            return int(chain_id_result) if chain_id_result else 1
        except Exception:
            return 1  # Default to mainnet
    
    raise ValueError(f"Unknown external method: {method}")


async def _execute_wallet_method(method: str, params: dict[str, Any], state: WalletdState) -> Any:
    """Execute a wallet method that has been approved."""
    if method == "wallet_requestAccounts":
        if state.wallet_store.is_locked:
            raise RuntimeError("Wallet is locked")
        accounts = state.wallet_store.list_accounts()
        return [acc["address"] for acc in accounts]
    
    if method == "wallet_signTransaction":
        if state.wallet_store.is_locked:
            raise RuntimeError("Wallet is locked")
        
        tx = params.get("transaction")
        from_addr = params.get("from")
        
        if not isinstance(tx, dict):
            raise ValueError("transaction must be a transaction object")
        if not isinstance(from_addr, str):
            raise ValueError("from must be a string address")
        
        # Sign the transaction
        node_status = state.node_manager.status()
        if not node_status.running or not node_status.rpc_url:
            raise RuntimeError("Node is not running")
        
        # Build unsigned tx if needed
        if "nonce" not in tx:
            nonce_result = await _proxy_to_node("state.getNonce", {"address": from_addr, "tag": "pending"}, node_status.rpc_url)
            tx["nonce"] = int(nonce_result) if nonce_result is not None else 0
        
        if "chain_id" not in tx:
            try:
                chain_id_result = await _proxy_to_node("chain.getChainId", {}, node_status.rpc_url)
                tx["chain_id"] = int(chain_id_result) if chain_id_result else 1
            except Exception:
                tx["chain_id"] = 1
        
        # Sign
        from omni_sdk.tx.build import make_tx
        from omni_sdk.tx.encode import sign_bytes, pack_signed
        from omni_sdk.wallet.signer import create_signer_from_keypair
        
        account = state.wallet_store.export_account(from_addr)
        secret_key = bytes.fromhex(account["secret_key_hex"])
        public_key = bytes.fromhex(account["public_key_hex"])
        alg_name = account["alg_name"]
        signer = create_signer_from_keypair(alg_name, secret_key, public_key)
        
        tx_obj = make_tx(
            from_addr=tx["from"],
            to=tx.get("to"),
            nonce=int(tx["nonce"]),
            value=int(tx.get("value", 0)),
            data=bytes.fromhex(tx.get("data", "").replace("0x", "")),
            gas_limit=int(tx["gas_limit"]),
            max_fee=int(tx["max_fee"]),
            chain_id=int(tx["chain_id"]),
        )
        
        sign_bytes_data = sign_bytes(tx_obj)
        signature = signer.sign(sign_bytes_data)
        
        raw_tx = pack_signed(
            tx_obj,
            signature=signature,
            alg_id=signer.alg_id,
            public_key=signer.public_key,
        )
        
        return {
            "signed_tx": "0x" + raw_tx.hex(),
            "tx_hash": "0x" + _compute_tx_hash(raw_tx).hex(),
        }
    
    if method == "wallet_sendTransaction":
        if state.wallet_store.is_locked:
            raise RuntimeError("Wallet is locked")
        
        tx = params.get("transaction")
        from_addr = params.get("from")
        
        # Sign first
        sign_result = await _execute_wallet_method("wallet_signTransaction", {"transaction": tx, "from": from_addr}, state)
        
        # Then send
        node_status = state.node_manager.status()
        if not node_status.running or not node_status.rpc_url:
            raise RuntimeError("Node is not running")

        await _proxy_to_node(
            "tx.sendRawTransaction",
            {"rawTx": sign_result["signed_tx"]},
            node_status.rpc_url,
        )
        
        # Track in history
        tx_hash = sign_result["tx_hash"]
        state.tx_history.add_pending(
            tx_hash=tx_hash,
            from_addr=from_addr,
            to_addr=tx.get("to"),
            value=int(tx.get("value", 0)),
            gas_limit=tx.get("gas_limit"),
            max_fee=tx.get("max_fee"),
            nonce=tx.get("nonce"),
        )
        
        return tx_hash
    
    raise ValueError(f"Cannot execute method: {method}")


async def dispatch(method: str | None, params: dict[str, Any], state: WalletdState) -> Any:
    if method == "walletd.health":
        return {"status": "ok"}
    if method == "walletd.version":
        return {"version": _resolve_version()}
    if method == "walletd.getStatus":
        node_status = state.node_manager.status()
        return {
            "node_running": node_status.running,
            "pid": os.getpid(),
            "rpc_url": state.rpc_url,
            "node_rpc_url": node_status.rpc_url,
            "node_network": node_status.network,
            "last_error": state.last_error,
            "wallet_locked": state.wallet_store.is_locked,
            "wallet_initialized": state.wallet_store.is_initialized,
        }
    if method == "walletd.getLogsTail":
        lines = int(params.get("lines", 200))
        return {"lines": _tail_log(state.log_path, max(1, min(lines, 1000)))}
    if method == "node.start":
        network = params.get("network", state.node_network)
        extra_args = params.get("extra_args", [])
        if network not in {"mainnet", "testnet"}:
            raise ValueError("Unsupported network (expected mainnet or testnet)")
        if not isinstance(extra_args, list) or not all(isinstance(arg, str) for arg in extra_args):
            raise ValueError("extra_args must be a list of strings")
        state.node_network = network
        status = await state.node_manager.start(network, extra_args=extra_args)
        return _node_status_payload(status)
    if method == "node.stop":
        await state.node_manager.stop()
        return {"stopped": True}
    if method == "node.status":
        return _node_status_payload(state.node_manager.status())
    if method == "node.logsTail":
        lines = int(params.get("lines", 200))
        log_path = resolve_node_log_path(state.node_manager.data_dir, state.node_network)
        return {"lines": _tail_log(log_path, max(1, min(lines, 2000)))}
    if method == "node.rpcInfo":
        status = state.node_manager.status()
        return {
            "rpc_url": status.rpc_url,
            "network": status.network,
        }
    # Proxy safe read calls to node RPC
    if method == "chain.getHead":
        node_status = state.node_manager.status()
        return await _proxy_to_node(method, params, node_status.rpc_url)
    if method == "state.getBalance":
        node_status = state.node_manager.status()
        return await _proxy_to_node(method, params, node_status.rpc_url)
    if method == "net.peers":
        node_status = state.node_manager.status()
        return await _proxy_to_node(method, params, node_status.rpc_url)
    if method == "net.peerCount":
        node_status = state.node_manager.status()
        return await _proxy_to_node(method, params, node_status.rpc_url)
    if method == "wallet.lock":
        state.wallet_store.lock()
        return {"locked": True}
    if method == "wallet.unlock":
        password = params.get("password")
        if not isinstance(password, str):
            raise ValueError("password must be a string")
        result = state.wallet_store.unlock(password)
        return {
            "unlocked": True,
            "initialized": result.get("initialized", False),
            "accounts": len(state.wallet_store.list_accounts())
            if not state.wallet_store.is_locked
            else 0,
        }
    if method == "wallet.listAccounts":
        return {"accounts": state.wallet_store.list_accounts()}
    if method == "wallet.createAccount":
        label = params.get("label")
        if label is not None and not isinstance(label, str):
            raise ValueError("label must be a string")
        return state.wallet_store.create_account(label)
    if method == "wallet.importAccount":
        label = params.get("label")
        if label is not None and not isinstance(label, str):
            raise ValueError("label must be a string")
        secret = params.get("secret")
        if not isinstance(secret, str):
            raise ValueError("secret must be a string")
        return state.wallet_store.import_account(label, secret)
    if method == "wallet.exportAccount":
        address = params.get("address")
        if not isinstance(address, str):
            raise ValueError("address must be a string")
        return state.wallet_store.export_account(address)
    
    # Transaction history methods
    if method == "wallet.txs.list":
        address = params.get("address")
        limit = int(params.get("limit", 100))
        cursor = int(params.get("cursor", 0))
        status_filter = params.get("status")
        
        entries = state.tx_history.list(
            address=address,
            limit=limit,
            offset=cursor,
            status_filter=status_filter,
        )
        
        return {
            "transactions": [entry.to_dict() for entry in entries],
            "next_cursor": cursor + len(entries) if len(entries) == limit else None,
        }
    
    if method == "wallet.txs.lookup":
        tx_hash = params.get("hash")
        if not isinstance(tx_hash, str):
            raise ValueError("hash must be a string")
        
        # First check local history
        entry = state.tx_history.get(tx_hash)
        if entry:
            return entry.to_dict()
        
        # Then check node
        node_status = state.node_manager.status()
        if node_status.running and node_status.rpc_url:
            try:
                result = await _proxy_to_node("tx.getTransactionByHash", {"txHash": tx_hash}, node_status.rpc_url)
                if result:
                    # Store in history if found on chain
                    if result.get("from"):
                        state.tx_history.add_pending(
                            tx_hash=tx_hash,
                            from_addr=result["from"],
                            to_addr=result.get("to"),
                            value=int(result.get("value", 0)),
                            gas_limit=result.get("gasLimit"),
                            max_fee=result.get("maxFee"),
                            nonce=result.get("nonce"),
                        )
                        # Update status if confirmed
                        if result.get("blockNumber") is not None:
                            state.tx_history.update_status(
                                tx_hash,
                                "confirmed",
                                block_number=result["blockNumber"],
                            )
                    return result
            except Exception:
                pass
        
        return None
    
    if method == "wallet.txs.resync":
        address = params.get("address")
        if not isinstance(address, str):
            raise ValueError("address must be a string")
        
        # Get current height
        node_status = state.node_manager.status()
        if not node_status.running or not node_status.rpc_url:
            raise RuntimeError("Node is not running")
        
        try:
            head = await _proxy_to_node("chain.getHead", {}, node_status.rpc_url)
            current_height = int(head.get("height", 0))
        except Exception:
            current_height = 0
        
        # Scan recent blocks (default: last 1000 blocks)
        scan_window = int(params.get("window", 1000))
        start_height = max(0, current_height - scan_window)
        
        found_count = 0
        # Note: This is a simplified implementation. In production, you would
        # iterate through blocks and check transactions against the address.
        # For now, we'll just update any pending transactions in history.
        for entry in state.tx_history.list(address=address, limit=1000, status_filter="pending"):
            try:
                result = await _proxy_to_node("tx.getTransactionByHash", {"txHash": entry.tx_hash}, node_status.rpc_url)
                if result and result.get("blockNumber") is not None:
                    state.tx_history.update_status(
                        entry.tx_hash,
                        "confirmed",
                        block_number=result["blockNumber"],
                    )
                    found_count += 1
            except Exception:
                continue
        
        return {
            "scanned_from": start_height,
            "scanned_to": current_height,
            "updated": found_count,
        }
    
    # Transaction methods
    if method == "tx.estimateFees":
        node_status = state.node_manager.status()
        if not node_status.running or not node_status.rpc_url:
            raise RuntimeError("Node is not running")
        
        # Simple heuristic: use default gas price from chain
        # TODO: In production, query current network conditions from recent blocks
        # for more accurate fee estimation based on network congestion
        base_fee = int(params.get("base_fee", 1_000_000_000))  # 1 gwei default
        tip = int(params.get("tip", 0))
        
        return {
            "base_fee": base_fee,
            "tip": tip,
            "max_fee": base_fee + tip,
            "estimated_total": (base_fee + tip) * int(params.get("gas_limit", 21000)),
        }
    
    if method == "tx.build":
        # Build an unsigned transaction
        from_addr = params.get("from")
        to_addr = params.get("to")
        value = int(params.get("value", 0))
        gas_limit = params.get("gas_limit")
        max_fee = params.get("max_fee")
        nonce = params.get("nonce")
        data = params.get("data", "")
        
        if not isinstance(from_addr, str):
            raise ValueError("from must be a string address")
        if to_addr is not None and not isinstance(to_addr, str):
            raise ValueError("to must be a string address or null")
        if gas_limit is None:
            raise ValueError("gas_limit is required")
        if max_fee is None:
            raise ValueError("max_fee is required")
        
        # Get nonce from node if not provided
        node_status = state.node_manager.status()
        if not node_status.running or not node_status.rpc_url:
            raise RuntimeError("Node is not running")
        
        if nonce is None:
            nonce_result = await _proxy_to_node("state.getNonce", {"address": from_addr, "tag": "pending"}, node_status.rpc_url)
            nonce = int(nonce_result) if nonce_result is not None else 0
        
        # Get chain_id from node
        try:
            chain_id_result = await _proxy_to_node("chain.getChainId", {}, node_status.rpc_url)
            chain_id = int(chain_id_result) if chain_id_result else 1
        except Exception:
            chain_id = 1  # Default to mainnet
        
        tx = {
            "from": from_addr,
            "to": to_addr,
            "value": value,
            "gas_limit": int(gas_limit),
            "max_fee": int(max_fee),
            "nonce": int(nonce),
            "chain_id": chain_id,
            "data": data,
        }
        return tx
    
    if method == "tx.sign":
        # Sign a transaction with a wallet account
        if state.wallet_store.is_locked:
            raise ValueError("Wallet is locked")
        
        tx = params.get("tx")
        from_addr = params.get("from")
        
        if not isinstance(tx, dict):
            raise ValueError("tx must be a transaction object")
        if not isinstance(from_addr, str):
            raise ValueError("from must be a string address")
        
        # Find the account
        account = None
        for acct_dict in state.wallet_store.list_accounts():
            if acct_dict.get("address") == from_addr:
                account = state.wallet_store.export_account(from_addr)
                break
        
        if account is None:
            raise ValueError(f"Account {from_addr} not found in wallet")
        
        # Use omni_sdk to build and sign the transaction
        from omni_sdk.tx.build import make_tx
        from omni_sdk.tx.encode import sign_bytes, pack_signed
        from omni_sdk.wallet.signer import create_signer_from_keypair
        
        # Create signer from account
        secret_key = bytes.fromhex(account["secret_key_hex"])
        public_key = bytes.fromhex(account["public_key_hex"])
        alg_name = account["alg_name"]
        signer = create_signer_from_keypair(alg_name, secret_key, public_key)
        
        # Build tx object
        tx_obj = make_tx(
            from_addr=tx["from"],
            to=tx.get("to"),
            nonce=int(tx["nonce"]),
            value=int(tx.get("value", 0)),
            data=bytes.fromhex(tx.get("data", "").replace("0x", "")),
            gas_limit=int(tx["gas_limit"]),
            max_fee=int(tx["max_fee"]),
            chain_id=int(tx["chain_id"]),
        )
        
        # Sign
        sign_bytes_data = sign_bytes(tx_obj)
        signature = signer.sign(sign_bytes_data)
        
        # Pack into signed CBOR
        raw_tx = pack_signed(
            tx_obj,
            signature=signature,
            alg_id=signer.alg_id,
            public_key=signer.public_key,
        )
        
        return {
            "signed_tx": "0x" + raw_tx.hex(),
            "tx_hash": "0x" + _compute_tx_hash(raw_tx).hex(),
        }
    
    if method == "tx.send":
        # Send a signed transaction to the node
        signed_tx = params.get("signed_tx")
        if not isinstance(signed_tx, str):
            raise ValueError("signed_tx must be a hex string")
        
        # Get tx details if provided (for history tracking)
        tx_details = params.get("tx_details")
        
        node_status = state.node_manager.status()
        if not node_status.running or not node_status.rpc_url:
            raise RuntimeError("Node is not running")
        
        result = await _proxy_to_node("tx.sendRawTransaction", {"rawTx": signed_tx}, node_status.rpc_url)
        
        # Track transaction in history if we have details
        if tx_details and isinstance(result, (str, dict)):
            tx_hash = result if isinstance(result, str) else result.get("tx_hash", result.get("hash"))
            if tx_hash:
                state.tx_history.add_pending(
                    tx_hash=tx_hash,
                    from_addr=tx_details.get("from", ""),
                    to_addr=tx_details.get("to"),
                    value=int(tx_details.get("value", 0)),
                    gas_limit=tx_details.get("gas_limit"),
                    max_fee=tx_details.get("max_fee"),
                    nonce=tx_details.get("nonce"),
                )
        
        return result
    
    if method == "tx.get":
        # Get transaction by hash (proxy to node)
        tx_hash = params.get("hash")
        if not isinstance(tx_hash, str):
            raise ValueError("hash must be a string")
        
        node_status = state.node_manager.status()
        if not node_status.running or not node_status.rpc_url:
            raise RuntimeError("Node is not running")
        
        return await _proxy_to_node("tx.getTransactionByHash", {"txHash": tx_hash}, node_status.rpc_url)
    
    raise ValueError(f"Unknown method: {method}")


def _tail_log(log_path: Path, lines: int) -> list[str]:
    if not log_path.exists():
        return []
    content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return content[-lines:]


def _resolve_version() -> str:
    try:
        from importlib.metadata import version

        return version("animica-qt-wallet")
    except Exception:  # noqa: BLE001
        return "0.0.0"


def create_app(state: WalletdState) -> web.Application:
    app = web.Application()
    app["state"] = state
    app.router.add_post("/", handle_rpc)
    app.router.add_post("/external", handle_external_rpc)
    return app


def _node_status_payload(status: NodeStatus) -> dict[str, Any]:
    return {
        "running": status.running,
        "pid": status.pid,
        "network": status.network,
        "rpc_url": status.rpc_url,
        "restarting": status.restarting,
        "last_exit_code": status.last_exit_code,
        "last_error": status.last_error,
        "backoff_seconds": status.backoff_seconds,
        "started_at": status.started_at,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Animica walletd service")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--data-dir", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)
    token = load_or_create_token(data_dir)
    port = resolve_port(args.port)
    rpc_url = f"http://127.0.0.1:{port}"
    log_path = resolve_log_path(data_dir)
    _setup_logging(log_path)
    logging.getLogger(__name__).info("Starting walletd on %s", rpc_url)

    state = WalletdState(
        token=token,
        rpc_url=rpc_url,
        log_path=log_path,
        node_manager=NodeManager(data_dir),
        wallet_store=WalletStore(resolve_wallet_path(data_dir)),
        tx_history=TxHistory(resolve_tx_history_path(data_dir)),
        approval_queue=ApprovalQueue(resolve_approval_queue_path(data_dir)),
        app_allowlist=AppAllowlist(resolve_app_allowlist_path(data_dir)),
        rate_limiter=RateLimiter(requests_per_minute=10, burst_size=5),
    )
    app = create_app(state)
    web.run_app(app, host="127.0.0.1", port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
