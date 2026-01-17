"""
Stratum bridge adapter for Animica.

Connects the Stratum server to the node RPC, polling for block templates
and submitting blocks when valid shares are found.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import signal
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

try:
    from core.logging import get_logger
except Exception:
    def get_logger(name: str) -> logging.Logger:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
        )
        return logging.getLogger(name)

log = get_logger("mining.stratum_bridge")


@dataclass
class RpcClient:
    """Simple JSON-RPC HTTP client."""
    url: str
    timeout: float = 10.0
    _id: int = field(default=0, init=False)
    
    async def call(self, method: str, params: Any = None) -> Any:
        """Call an RPC method."""
        import aiohttp
        
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params or [],
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.url}/rpc" if not self.url.endswith("/rpc") else self.url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                ) as resp:
                    if resp.status != 200:
                        raise Exception(f"HTTP {resp.status}: {await resp.text()}")
                    
                    result = await resp.json()
                    
                    if "error" in result and result["error"]:
                        error = result["error"]
                        raise Exception(f"RPC error: {error}")
                    
                    return result.get("result")
        except asyncio.TimeoutError:
            raise Exception(f"RPC timeout after {self.timeout}s")
        except Exception as e:
            raise Exception(f"RPC call failed: {e}")


@dataclass
class StratumBridge:
    """
    Bridge between Animica node RPC and Stratum server.
    
    Responsibilities:
    - Poll getBlockTemplate from node RPC
    - Convert templates to Stratum jobs
    - Submit blocks when shares meet network difficulty
    - Track head changes for clean_jobs notifications
    """
    rpc_url: str
    poll_interval: float = 2.0
    default_share_target: float = 0.01
    
    _rpc: RpcClient = field(init=False)
    _current_template: Optional[Dict[str, Any]] = field(default=None, init=False)
    _current_job_id: Optional[str] = field(default=None, init=False)
    _last_parent_hash: Optional[str] = field(default=None, init=False)
    _payout_address: Optional[str] = field(default=None, init=False)
    _stop: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    
    def __post_init__(self) -> None:
        self._rpc = RpcClient(self.rpc_url)
    
    async def start(self, payout_address: str) -> None:
        """Start the bridge with a payout address."""
        self._payout_address = payout_address
        log.info(f"Starting Stratum bridge (RPC: {self.rpc_url}, payout: {payout_address})")
        
        # Start polling loop
        asyncio.create_task(self._poll_loop(), name="template-poll")
    
    async def stop(self) -> None:
        """Stop the bridge."""
        self._stop.set()
    
    async def _poll_loop(self) -> None:
        """Poll getBlockTemplate from node."""
        while not self._stop.is_set():
            try:
                await self._poll_template()
            except Exception as e:
                log.debug(f"Template poll error: {e}")
            
            await asyncio.sleep(self.poll_interval)
    
    async def _poll_template(self) -> None:
        """Fetch a new block template."""
        if not self._payout_address:
            return
        
        try:
            # Call miner.getBlockTemplate
            template = await self._rpc.call("miner.getBlockTemplate", {
                "address": self._payout_address,
                "include_mempool": True,
            })
            
            if not template or not template.get("enabled"):
                reason = template.get("reason", "unknown") if template else "no_response"
                log.debug(f"Template not available: {reason}")
                return
            
            # Check if head changed
            parent_hash = template.get("parent", {}).get("hash")
            if parent_hash and parent_hash != self._last_parent_hash:
                # Head changed, new job needed
                self._last_parent_hash = parent_hash
                self._current_template = template
                self._current_job_id = template.get("templateId") or uuid.uuid4().hex[:16]
                
                log.info(
                    f"New template: job={self._current_job_id} "
                    f"height={template.get('parent', {}).get('height', 0) + 1} "
                    f"parent={parent_hash[:18]}..."
                )
        
        except Exception as e:
            log.warning(f"Failed to get block template: {e}")
    
    async def get_current_job(self) -> Optional[Dict[str, Any]]:
        """
        Get current Stratum job from template.
        
        Returns Stratum-compatible job dict or None.
        """
        if not self._current_template:
            return None
        
        template = self._current_template
        header = template.get("header", {})
        
        # Build Stratum job
        job = {
            "job_id": self._current_job_id or uuid.uuid4().hex[:16],
            "height": header.get("height", 0),
            "parent_hash": template.get("parent", {}).get("hash"),
            "parent_height": template.get("parent", {}).get("height", 0),
            "chain_id": header.get("chainId", 1),
            "theta_micro": template.get("thetaMicro", 800_000),
            "share_target": self.default_share_target,
            "target": template.get("target"),
            "header": header,
            "sign_bytes": header.get("signBytes"),
            "coinbase": template.get("coinbase"),
            "payout_address": self._payout_address,
            "template_id": template.get("templateId"),
            "timestamp_min": template.get("timestampMin"),
            "timestamp_max": template.get("timestampMax"),
            "clean_jobs": True,  # Always true when returning new job
        }
        
        return job
    
    async def submit_block(self, block_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit a completed block to the node.
        
        Args:
            block_data: Block with header and transactions
            
        Returns:
            {"accepted": bool, "reason": str, "is_block": bool}
        """
        try:
            # Call miner.submitBlock
            result = await self._rpc.call("miner.submitBlock", [block_data])
            
            accepted = result.get("accepted", False)
            reason = result.get("reason")
            
            if accepted:
                log.info(
                    f"✓ Block accepted! "
                    f"height={block_data.get('header', {}).get('height', '?')} "
                    f"hash={result.get('hash', 'unknown')[:18]}..."
                )
            else:
                log.warning(f"✗ Block rejected: {reason}")
            
            return {
                "accepted": accepted,
                "reason": reason,
                "is_block": accepted,
            }
        
        except Exception as e:
            log.error(f"Block submission error: {e}")
            return {
                "accepted": False,
                "reason": str(e),
                "is_block": False,
            }
    
    async def submit_share(self, share: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate and potentially submit a share.
        
        Args:
            share: Share data from miner
            
        Returns:
            {"accepted": bool, "reason": str, "is_block": bool}
        """
        try:
            # Extract share components
            job_id = share.get("job_id")
            hashshare = share.get("hashshare", {})
            nonce_hex = hashshare.get("nonce", "0x0")
            
            # Check if share is for current job
            if job_id != self._current_job_id:
                return {
                    "accepted": False,
                    "reason": "stale_job",
                    "is_block": False,
                }
            
            if not self._current_template:
                return {
                    "accepted": False,
                    "reason": "no_template",
                    "is_block": False,
                }
            
            # Validate share against share target
            # (In real implementation, this would properly compute the hash
            # and check against both share target and network target)
            
            # For now, we'll use the node's submitShare if available,
            # or attempt to construct and submit a full block
            template = self._current_template
            header = template.get("header", {})
            
            # Check if this could be a full block
            # (Real implementation would verify hash meets network target)
            
            # Try to submit as a share first
            try:
                result = await self._rpc.call("miner.submitShare", [share])
                if result:
                    return {
                        "accepted": result.get("accepted", False),
                        "reason": result.get("reason"),
                        "is_block": result.get("is_block", False),
                    }
            except Exception:
                # submitShare not available, fall back to block submission
                pass
            
            # Construct full block for submission
            # Update header with nonce
            header_with_nonce = dict(header)
            try:
                nonce_int = int(nonce_hex, 16)
                header_with_nonce["nonce"] = nonce_int
            except Exception:
                return {
                    "accepted": False,
                    "reason": "invalid_nonce",
                    "is_block": False,
                }
            
            # Build block structure
            block = {
                "header": header_with_nonce,
                "txs": template.get("txs", []),
                "templateId": template.get("templateId"),
            }
            
            # Submit block
            return await self.submit_block(block)
        
        except Exception as e:
            log.error(f"Share submission error: {e}")
            return {
                "accepted": False,
                "reason": str(e),
                "is_block": False,
            }


def _create_stratum_job(job_dict: Dict[str, Any], share_target: float) -> "StratumJob":
    """
    Convert a job dictionary to a StratumJob instance.
    
    Args:
        job_dict: Job dictionary from bridge.get_current_job()
        share_target: Share difficulty target
        
    Returns:
        StratumJob instance ready for publishing
    """
    from .stratum_server import StratumJob
    
    return StratumJob(
        job_id=job_dict["job_id"],
        header=job_dict.get("header", {}),
        share_target=job_dict.get("share_target", share_target),
        theta_micro=job_dict.get("theta_micro", 800_000),
        target=job_dict.get("target"),
        sign_bytes=job_dict.get("sign_bytes"),
        height=job_dict.get("height"),
        parent_hash=job_dict.get("parent_hash"),
        parent_height=job_dict.get("parent_height"),
        chain_id=job_dict.get("chain_id"),
    )


async def run_bridge_server(
    rpc_url: str,
    listen_host: str,
    listen_port: int,
    payout_address: str,
    poll_interval: float = 2.0,
    share_target: float = 0.01,
) -> None:
    """
    Run the Stratum bridge server.
    
    Args:
        rpc_url: Node RPC URL
        listen_host: Stratum server bind address
        listen_port: Stratum server port
        payout_address: Default payout address for mining
        poll_interval: Template poll interval in seconds
        share_target: Default share difficulty target
    """
    from .stratum_server import StratumServer, StratumJob
    
    # Create bridge
    bridge = StratumBridge(
        rpc_url=rpc_url,
        poll_interval=poll_interval,
        default_share_target=share_target,
    )
    
    # Start bridge
    await bridge.start(payout_address)
    
    # Fetch initial template before starting server to ensure clients get a job immediately
    log.info("Fetching initial block template...")
    max_retries = 10
    for attempt in range(max_retries):
        try:
            await bridge._poll_template()
            if bridge._current_template:
                log.info(f"Initial template ready (job_id={bridge._current_job_id})")
                break
        except Exception as e:
            log.debug(f"Initial template fetch attempt {attempt + 1}/{max_retries} failed: {e}")
        
        if attempt < max_retries - 1:
            await asyncio.sleep(0.5)
    
    if not bridge._current_template:
        log.warning("Failed to fetch initial template; server will start without a job")
    
    # Create Stratum server
    server = StratumServer()
    
    # Set up job publisher - poll bridge for new jobs
    async def job_publisher():
        last_job_id = None
        while True:
            try:
                job_dict = await bridge.get_current_job()
                if job_dict and job_dict.get("job_id") != last_job_id:
                    # Create and publish job
                    job = _create_stratum_job(job_dict, share_target)
                    await server.publish_job(job)
                    last_job_id = job_dict["job_id"]
                    log.info(f"Published job {job_dict['job_id']} to miners")
            
            except Exception as e:
                log.debug(f"Job publisher error: {e}")
            
            await asyncio.sleep(poll_interval)
    
    # Set up share submission hook
    async def submit_hook(session, job, params, ok, reason, is_block, tx_count):
        if ok:
            # Submit share to bridge
            result = await bridge.submit_share(params)
            if result.get("is_block"):
                log.info(f"✓ Block found by worker {session.worker}!")
    
    server.set_submit_hook(submit_hook)
    
    # Publish initial job to server if available
    # This ensures clients connecting immediately after startup receive a job
    if bridge._current_template:
        initial_job_dict = await bridge.get_current_job()
        if initial_job_dict:
            initial_job = _create_stratum_job(initial_job_dict, share_target)
            # Use publish_job to properly set up the job in the server
            await server.publish_job(initial_job)
            log.info(f"Initial job loaded into server (job_id={initial_job.job_id})")
    
    # Start job publisher
    asyncio.create_task(job_publisher(), name="job-publisher")
    
    try:
        # Start and run Stratum server (run_async handles both start and serve_forever)
        log.info(f"Stratum bridge listening on {listen_host}:{listen_port}")
        await server.run_async(listen_host, listen_port)
    finally:
        await bridge.stop()
        await server.stop()


if __name__ == "__main__":
    # CLI argument parser
    parser = argparse.ArgumentParser(description="Animica Stratum Bridge Server")
    parser.add_argument("--rpc-url", type=str, default="http://127.0.0.1:8545",
                        help="Node RPC URL (default: http://127.0.0.1:8545)")
    parser.add_argument("--listen", type=str, default="127.0.0.1:3333",
                        help="Bind address HOST:PORT (default: 127.0.0.1:3333)")
    parser.add_argument("--address", type=str, required=True,
                        help="Payout address (Bech32 format)")
    parser.add_argument("--log-level", type=str, default="info",
                        choices=["debug", "info", "warning", "error"],
                        help="Logging level (default: info)")
    parser.add_argument("--poll-interval", type=float, default=2.0,
                        help="Template poll interval in seconds (default: 2.0)")
    parser.add_argument("--share-target", type=float, default=0.01,
                        help="Default share difficulty target (default: 0.01)")
    parser.add_argument("--auth-token", type=str, default=None,
                        help="Authentication token (not yet implemented)")
    
    args = parser.parse_args()
    
    # Set logging level
    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    
    # Parse listen address
    try:
        if ":" in args.listen:
            listen_host, listen_port_str = args.listen.rsplit(":", 1)
            listen_port = int(listen_port_str)
        else:
            listen_host = args.listen
            listen_port = 3333
    except ValueError:
        print(f"Error: Invalid listen address: {args.listen}", file=sys.stderr)
        sys.exit(1)
    
    # Signal handling for graceful shutdown
    stop_event = asyncio.Event()
    
    def signal_handler(sig, frame):
        log.info("Received signal, shutting down...")
        stop_event.set()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run bridge server
    async def main():
        task = asyncio.create_task(run_bridge_server(
            rpc_url=args.rpc_url,
            listen_host=listen_host,
            listen_port=listen_port,
            payout_address=args.address,
            poll_interval=args.poll_interval,
            share_target=args.share_target,
        ))
        
        await stop_event.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    
    log.info("Stratum bridge shut down.")
