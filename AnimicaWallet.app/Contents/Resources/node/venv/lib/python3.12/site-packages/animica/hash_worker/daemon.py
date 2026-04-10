"""
Hash worker daemon.

Listens for HashJobPosted events, executes hash work using configured backend,
and submits results to the HashJobs contract.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from python.animica.hash_work.algorithms import HashAlgorithm
from python.animica.hash_work.schemas import DeviceType, HashResult

from .backends import HashBackend, get_backend

logger = logging.getLogger(__name__)


@dataclass
class DaemonConfig:
    """Configuration for hash worker daemon."""

    rpc_url: str
    chain_id: int
    backend_type: str  # cpu, gpu, asic, quantum
    worker_address: Optional[str] = None
    poll_interval_seconds: float = 5.0
    max_concurrent_jobs: int = 1
    state_file: Optional[str] = None
    backend_config: Optional[Dict[str, Any]] = None


class HashWorkerDaemon:
    """
    Hash worker daemon that processes jobs from the chain.

    This daemon:
    1. Polls for HashJobPosted events (or subscribes via websocket)
    2. Decodes job parameters
    3. Executes work using configured backend
    4. Calls HashJobs.mark_completed with result

    For this MVP, we use simple polling. Production would use websocket
    subscriptions and proper RPC client integration.
    """

    def __init__(self, config: DaemonConfig):
        self.config = config
        self.backend = get_backend(config.backend_type)
        self.running = False
        self.last_seen_job_id: Optional[bytes] = None

        # Load state if available
        if config.state_file:
            self._load_state()

        logger.info(
            f"HashWorkerDaemon initialized with backend={config.backend_type}, "
            f"device={self.backend.get_device_type().value}"
        )

    def _load_state(self) -> None:
        """Load daemon state from file (for restart resilience)."""
        if not self.config.state_file:
            return

        state_path = Path(self.config.state_file)
        if not state_path.exists():
            return

        try:
            with open(state_path, "r") as f:
                state = json.load(f)
                last_job = state.get("last_seen_job_id")
                if last_job:
                    self.last_seen_job_id = bytes.fromhex(last_job)
                logger.info(f"Loaded state: last_job={last_job}")
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")

    def _save_state(self) -> None:
        """Save daemon state to file."""
        if not self.config.state_file:
            return

        try:
            state = {}
            if self.last_seen_job_id:
                state["last_seen_job_id"] = self.last_seen_job_id.hex()

            state_path = Path(self.config.state_file)
            state_path.parent.mkdir(parents=True, exist_ok=True)

            with open(state_path, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

    def start(self) -> None:
        """Start the daemon (blocking)."""
        self.running = True
        logger.info("HashWorkerDaemon starting...")

        try:
            while self.running:
                self._process_jobs()
                time.sleep(self.config.poll_interval_seconds)
        except KeyboardInterrupt:
            logger.info("Received interrupt, stopping...")
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop the daemon."""
        self.running = False
        self._save_state()
        logger.info("HashWorkerDaemon stopped")

    def _process_jobs(self) -> None:
        """
        Process pending jobs.

        In a real implementation, this would:
        1. Query RPC for HashJobPosted events since last_seen_block
        2. Filter for jobs we haven't processed
        3. Execute each job
        4. Submit results via transaction

        For this MVP/mock, we just log that we're ready to process.
        """
        logger.debug("Checking for new hash jobs...")

        # Mock: In production, query events via RPC
        # For testing, this method can be called with job data directly
        # via process_job()

    def process_job(
        self,
        job_id: bytes,
        algorithm: str,
        input_commitment: bytes,
        target_bits: int,
        max_iterations: int,
        scrypt_n: Optional[int] = None,
        scrypt_r: Optional[int] = None,
        scrypt_p: Optional[int] = None,
    ) -> Optional[HashResult]:
        """
        Process a single hash job.

        Args:
            job_id: Job identifier
            algorithm: Algorithm name (e.g., "SHA256")
            input_commitment: Input commitment
            target_bits: Target difficulty
            max_iterations: Max iterations
            scrypt_n: Scrypt N (if applicable)
            scrypt_r: Scrypt r (if applicable)
            scrypt_p: Scrypt p (if applicable)

        Returns:
            HashResult if successful, None otherwise
        """
        logger.info(
            f"Processing job {job_id.hex()[:8]}... algo={algorithm}, "
            f"target={target_bits}, max_iters={max_iterations}"
        )

        try:
            # Parse algorithm
            hash_algo = HashAlgorithm(algorithm.upper())

            # Execute work
            start_time = time.time()
            result = self.backend.execute_hash_work(
                algorithm=hash_algo,
                input_commitment=input_commitment,
                target_bits=target_bits,
                max_iterations=max_iterations,
                scrypt_n=scrypt_n,
                scrypt_r=scrypt_r,
                scrypt_p=scrypt_p,
            )
            elapsed = time.time() - start_time

            if not result.success:
                logger.warning(
                    f"Job {job_id.hex()[:8]} failed: {result.error}"
                )
                return None

            logger.info(
                f"Job {job_id.hex()[:8]} completed in {elapsed:.2f}s, "
                f"iterations={result.iterations}"
            )

            # Build HashResult
            hash_result = HashResult(
                job_id=job_id,
                output_hash=result.output_hash,
                nonce=result.nonce,
                iterations=result.iterations,
                device_type=self.backend.get_device_type(),
                backend_id=self.backend.get_backend_id(),
                worker_address=self.config.worker_address,
                timestamp=int(time.time()),
            )

            # Update last seen
            self.last_seen_job_id = job_id
            self._save_state()

            return hash_result

        except Exception as e:
            logger.error(f"Error processing job {job_id.hex()[:8]}: {e}", exc_info=True)
            return None

    def submit_result(self, result: HashResult) -> bool:
        """
        Submit a result to the chain via HashJobs.mark_completed.

        In production, this would create and send a transaction.
        For this MVP, we just log the submission.

        Args:
            result: HashResult to submit

        Returns:
            True if successfully submitted
        """
        logger.info(
            f"Submitting result for job {result.job_id.hex()[:8]}: "
            f"hash={result.output_hash.hex()[:8]}..., "
            f"iterations={result.iterations}, "
            f"device={result.device_type.value}"
        )

        # Mock: In production, send transaction to HashJobs.mark_completed
        # tx = build_transaction(
        #     to=hash_jobs_contract_address,
        #     data=encode_function_call(
        #         "mark_completed",
        #         result.job_id,
        #         result.output_hash,
        #         result.nonce,
        #         result.iterations,
        #         result.device_type.value.encode(),
        #         result.backend_id.encode(),
        #     ),
        # )
        # send_transaction(tx)

        return True


def load_config_from_env() -> DaemonConfig:
    """
    Load daemon configuration from environment variables.

    Expected variables:
        ANIMICA_RPC_URL: RPC endpoint URL
        ANIMICA_CHAIN_ID: Chain ID (int)
        HASH_BACKEND_TYPE: Backend type (cpu|gpu|asic|quantum)
        HASH_WORKER_ADDRESS: Worker address (optional)
        HASH_POLL_INTERVAL: Poll interval in seconds (optional, default 5.0)
        HASH_STATE_FILE: Path to state file (optional)
        HASH_BACKEND_CONFIG: JSON config for backend (optional)

    Returns:
        DaemonConfig instance
    """
    rpc_url = os.environ.get("ANIMICA_RPC_URL", "http://localhost:8545")
    chain_id = int(os.environ.get("ANIMICA_CHAIN_ID", "1337"))
    backend_type = os.environ.get("HASH_BACKEND_TYPE", "cpu")
    worker_address = os.environ.get("HASH_WORKER_ADDRESS")
    poll_interval = float(os.environ.get("HASH_POLL_INTERVAL", "5.0"))
    state_file = os.environ.get("HASH_STATE_FILE", "/tmp/hash_worker_state.json")

    backend_config = None
    backend_config_str = os.environ.get("HASH_BACKEND_CONFIG")
    if backend_config_str:
        try:
            backend_config = json.loads(backend_config_str)
        except json.JSONDecodeError:
            logger.warning(f"Invalid HASH_BACKEND_CONFIG JSON: {backend_config_str}")

    return DaemonConfig(
        rpc_url=rpc_url,
        chain_id=chain_id,
        backend_type=backend_type,
        worker_address=worker_address,
        poll_interval_seconds=poll_interval,
        state_file=state_file,
        backend_config=backend_config,
    )


__all__ = [
    "HashWorkerDaemon",
    "DaemonConfig",
    "load_config_from_env",
]
