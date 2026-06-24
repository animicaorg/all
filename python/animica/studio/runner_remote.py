"""Remote execution backend — dispatch a function call to the Animica fleet.

The exact wire (see ARCHITECTURE.md §4):

1. quote the call (``aicf.estimateJobCost`` or local fallback)
2. escrow ANM to the treasury (signed transfer) when ``billing='onchain'``
3. submit a ``function_compute`` job to ``aicf.submitInferenceJob``
4. poll ``aicf.settleJob`` until terminal
5. unpack the worker's packed result

Reuses the live broker entirely; the only Studio-specific pieces are the
``function_compute`` spec class and the packed-call/packed-result envelope, both
also understood by the provider-daemon ``function`` adapter.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from typing import Any, Iterable, List, Optional

from . import billing, serialize
from .client import BrokerClient
from .errors import BillingError, ExecutionError, JobError, TimeoutError as StudioTimeout


class RemoteFunctionCall:
    """Handle returned by ``Function.spawn()`` in remote mode."""

    def __init__(self, runner: "RemoteRunner", job_id: str, function):
        self._runner = runner
        self.object_id = job_id
        self._function = function

    def get(self, timeout: Optional[float] = None) -> Any:
        return self._runner._poll(self._function, self.object_id, timeout=timeout)


class RemoteRunner:
    mode = "remote"

    def __init__(self, config, *, signer=None):
        self.config = config
        self.client = BrokerClient(config.rpc_url)
        self._signer = signer
        self._signer_loaded = signer is not None
        self._pool: Optional[concurrent.futures.ThreadPoolExecutor] = None

    # ---- lifecycle --------------------------------------------------------

    def _ensure_pool(self):
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self.config.local_concurrency, thread_name_prefix="studio-remote"
            )
        return self._pool

    def close(self) -> None:
        self.client.close()
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    def _signer_or_none(self):
        if not self._signer_loaded:
            self._signer = _load_signer(self.config)
            self._signer_loaded = True
        return self._signer

    def deploy(self, functions) -> dict:
        out = []
        for f in functions:
            meta = {
                "name": f.name,
                "app": f.app.name,
                "image_ref": f.image.ref,
                "image": f.image.to_spec(),
                "gpu": f.gpu,
                "timeout": f.timeout or self.config.default_timeout_s,
                "schedule": f.schedule.to_spec() if f.schedule else None,
                "entrypoint": f"{f.fn.__module__}:{f.fn.__qualname__}",
            }
            out.append(self.client.fn_deploy(meta))
        return {"mode": "remote", "deployed": out}

    # ---- execution --------------------------------------------------------

    def run(self, function, args: tuple, kwargs: dict) -> Any:
        job_id = self._submit(function, args, kwargs)
        return self._poll(function, job_id, timeout=function.timeout or self.config.default_timeout_s)

    def map(self, function, iterable: Iterable, *, order_outputs: bool = True) -> List[Any]:
        pool = self._ensure_pool()
        items = list(iterable)
        futures = [pool.submit(self.run, function, (item,), {}) for item in items]
        if order_outputs:
            return [f.result() for f in futures]
        return [f.result() for f in concurrent.futures.as_completed(futures)]

    def spawn(self, function, args: tuple, kwargs: dict) -> RemoteFunctionCall:
        job_id = self._submit(function, args, kwargs)
        return RemoteFunctionCall(self, job_id, function)

    # ---- internals --------------------------------------------------------

    def _submit(self, function, args: tuple, kwargs: dict) -> str:
        rail = function.billing or self.config.billing
        quote = billing.quote_for(function, self.client)

        payment: dict = {}
        if rail == "onchain":
            tx_hash = billing.escrow(self.config, quote, signer=self._signer_or_none())
            payment = {"payment_tx_hash": tx_hash, "amount_anm": quote.cost_anm}
        elif rail == "credits":
            payment = {"credits": quote.cost_nanos}
        # rail == "none": dev/free path; broker may reject if it requires payment.

        call_spec = serialize.pack_call(function.fn, args, kwargs, mode=function.serialization)
        spec = {
            "class": "function_compute",
            "model": function.name,
            "input": {
                "call": call_spec,
                "image_ref": function.image.ref,
                "image": function.image.to_spec(),
                "secrets": [s.name for s in function.secrets],
                "volumes": [v.to_spec() for v in function.volumes],
            },
            "gpu": function.gpu,
            "timeoutSeconds": function.timeout or self.config.default_timeout_s,
        }
        res = self.client.submit_job(spec, payment)
        job_id = res.get("job_id") if isinstance(res, dict) else None
        if not job_id:
            raise JobError(f"broker did not return a job_id: {res}")
        if rail == "onchain" and isinstance(res, dict) and res.get("payment_accepted") is False:
            raise BillingError(f"payment not accepted for job {job_id}: {res}")
        return job_id

    def _poll(self, function, job_id: str, *, timeout: Optional[float]) -> Any:
        deadline = time.time() + (timeout or self.config.default_timeout_s) + 30
        backoff = 0.5
        while time.time() < deadline:
            settle = self.client.settle_job(job_id)
            if settle.get("settled") or settle.get("state") in ("completed", "failed"):
                if settle.get("state") == "failed" or settle.get("error"):
                    raise ExecutionError(
                        f"{function.name} job {job_id} failed: {settle.get('error', settle)}"
                    )
                return self._extract_result(function, settle)
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 5.0)
        raise StudioTimeout(f"{function.name} job {job_id} did not settle before timeout")

    def _extract_result(self, function, settle: dict) -> Any:
        # The function adapter returns the packed result; it can arrive in `text`
        # (sentinel-prefixed), or as a structured `studio_result`/`result_b64`.
        text = settle.get("text")
        if isinstance(text, str) and serialize.RESULT_SENTINEL in text:
            blob = text.split(serialize.RESULT_SENTINEL, 1)[1].strip().splitlines()[0]
            packed = json.loads(serialize.b64decode(blob).decode("utf-8"))
            return serialize.unpack_result(packed)
        for key in ("studio_result", "result"):
            if isinstance(settle.get(key), dict) and "kind" in settle[key]:
                return serialize.unpack_result(settle[key])
        if isinstance(settle.get("result_b64"), str):
            packed = json.loads(serialize.b64decode(settle["result_b64"]).decode("utf-8"))
            return serialize.unpack_result(packed)
        # Last resort: hand back the raw text (e.g. a non-Studio inference worker).
        return text


def _load_signer(config):
    """Best-effort PQ signer load from a seed env or keystore. Returns None on miss."""
    import os
    seed = os.environ.get("ANIMICA_STUDIO_WALLET_SEED")
    try:
        from omni_sdk.wallet.signer import PQSigner  # type: ignore
    except ImportError:
        return None
    if seed:
        try:
            return PQSigner.from_seed("ml-dsa-65", bytes.fromhex(seed))
        except Exception:
            return None
    return None
