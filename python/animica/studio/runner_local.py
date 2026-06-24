"""Local execution backend — runs a packed call in a subprocess sandbox.

This is what makes the SDK demoable with no chain, no node, no provider fleet:
``train.remote(42)`` packs the call, runs the self-contained wrapper in a fresh
``python`` subprocess with a timeout and the function's secrets in the env, and
parses the result sentinel back into a Python object.

It is the same execution contract the distributed provider adapter implements —
the only difference is *where* the subprocess runs (here vs. a provider's
hardened sandbox) and whether ANM changed hands.
"""

from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable, List, Optional

from . import serialize
from .errors import ExecutionError, TimeoutError as StudioTimeout


class LocalFunctionCall:
    """Handle returned by ``Function.spawn()`` in local mode."""

    def __init__(self, future: "concurrent.futures.Future", object_id: str):
        self._future = future
        self.object_id = object_id

    def get(self, timeout: Optional[float] = None) -> Any:
        return self._future.result(timeout=timeout)

    @property
    def done(self) -> bool:
        return self._future.done()


class LocalRunner:
    mode = "local"

    def __init__(self, config):
        self.config = config
        self._pool: Optional[concurrent.futures.ThreadPoolExecutor] = None

    # ---- lifecycle --------------------------------------------------------

    def _ensure_pool(self) -> concurrent.futures.ThreadPoolExecutor:
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self.config.local_concurrency,
                thread_name_prefix="studio-local",
            )
        return self._pool

    def close(self) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    def deploy(self, functions) -> dict:
        # Nothing to register in local mode; report what would be deployed.
        return {"mode": "local", "deployed": [f.name for f in functions]}

    # ---- execution --------------------------------------------------------

    def run(self, function, args: tuple, kwargs: dict) -> Any:
        spec = serialize.pack_call(function.fn, args, kwargs, mode=function.serialization)
        env = dict(os.environ)
        for secret in function.secrets:
            env.update(secret.as_env())
        for k, v in function.image.env_vars:
            env.setdefault(k, v)

        timeout = function.timeout or self.config.default_timeout_s
        started = time.time()
        with tempfile.NamedTemporaryFile("w", suffix=".studio.json", delete=False) as fh:
            fh.write(serialize.dumps(spec))
            spec_path = fh.name
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "animica.studio._wrapper", spec_path],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise StudioTimeout(
                f"{function.name} exceeded its {timeout}s timeout"
            ) from exc
        finally:
            try:
                os.unlink(spec_path)
            except OSError:
                pass

        return self._parse(function, proc, time.time() - started)

    def map(self, function, iterable: Iterable, *, order_outputs: bool = True) -> List[Any]:
        pool = self._ensure_pool()
        items = list(iterable)
        futures = [pool.submit(self.run, function, (item,), {}) for item in items]
        if order_outputs:
            return [f.result() for f in futures]
        return [f.result() for f in concurrent.futures.as_completed(futures)]

    def spawn(self, function, args: tuple, kwargs: dict) -> LocalFunctionCall:
        pool = self._ensure_pool()
        fut = pool.submit(self.run, function, args, kwargs)
        return LocalFunctionCall(fut, object_id=f"local-{id(fut):x}")

    # ---- result parsing ---------------------------------------------------

    def _parse(self, function, proc: subprocess.CompletedProcess, elapsed: float) -> Any:
        result_line: Optional[str] = None
        error_line: Optional[str] = None
        passthrough: List[str] = []
        for line in (proc.stdout or "").splitlines():
            if line.startswith(serialize.RESULT_SENTINEL):
                result_line = line[len(serialize.RESULT_SENTINEL):]
            elif line.startswith(serialize.ERROR_SENTINEL):
                error_line = line[len(serialize.ERROR_SENTINEL):]
            else:
                passthrough.append(line)

        # Forward the function's own stdout/stderr so `print()` debugging works.
        if passthrough:
            sys.stdout.write("\n".join(passthrough) + "\n")
        if proc.stderr:
            sys.stderr.write(proc.stderr)

        if error_line is not None:
            import json
            payload = json.loads(serialize.b64decode(error_line).decode("utf-8"))
            raise ExecutionError(
                f"{function.name} raised: {payload.get('message')}",
                remote_traceback=payload.get("traceback"),
                exit_code=proc.returncode,
            )
        if result_line is not None:
            import json
            packed = json.loads(serialize.b64decode(result_line).decode("utf-8"))
            return serialize.unpack_result(packed)

        raise ExecutionError(
            f"{function.name} produced no result (exit={proc.returncode}). "
            f"stderr tail: {(proc.stderr or '')[-500:]}",
            exit_code=proc.returncode,
        )
