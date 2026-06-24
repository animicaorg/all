"""The developer-facing API: ``App`` and the ``@app.function`` decorator.

```python
import animica.studio as studio

app = studio.App("demo")

@app.function(image=studio.Image.debian_slim().pip_install("numpy"), gpu="A100", timeout=120)
def mean(seed: int) -> float:
    import numpy as np
    return float(np.random.default_rng(seed).standard_normal(1_000).mean())

mean.remote(7)          # run on the fleet (or locally in dev)
list(mean.map(range(4)))# fan-out
mean.local(7)           # run in this process, no dispatch
```
"""

from __future__ import annotations

import functools
from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .config import StudioConfig
from .image import DEFAULT_IMAGE, Image


class Function:
    """A function registered with an :class:`App`. Not directly callable — use
    ``.remote()`` (dispatch), ``.map()`` (fan-out), or ``.local()`` (in-process).
    """

    def __init__(
        self,
        app: "App",
        fn: Callable,
        *,
        image: Optional[Image],
        gpu: Optional[str],
        cpu: Optional[float],
        memory,
        timeout: Optional[int],
        secrets: Tuple,
        volumes: Tuple,
        schedule,
        billing: Optional[str],
        retries: int,
        serialization: str,
        name: Optional[str],
    ):
        self.app = app
        self.fn = fn
        self.image = image or app.image or DEFAULT_IMAGE
        self.gpu = gpu
        self.cpu = cpu
        self.memory = memory
        self.timeout = timeout
        self.secrets = tuple(secrets or ())
        self.volumes = tuple(volumes or ())
        self.schedule = schedule
        self.billing = billing
        self.retries = int(retries or 0)
        self.serialization = serialization
        self._name = name
        functools.update_wrapper(self, fn)  # carries __name__/__doc__/__wrapped__
        # Marker so the self-contained sandbox wrapper can unwrap a re-imported
        # decorated function back to the raw callable (it must not import animica).
        self.__animica_studio_function__ = True

    @property
    def name(self) -> str:
        return self._name or f"{self.app.name}.{self.fn.__name__}"

    # ---- invocation -------------------------------------------------------

    def local(self, *args, **kwargs) -> Any:
        """Run the function in *this* process — no sandbox, no dispatch."""
        return self.fn(*args, **kwargs)

    def remote(self, *args, **kwargs) -> Any:
        """Run once on the fleet (or local sandbox in dev), returning the result."""
        runner = self.app._runner()
        attempt = 0
        while True:
            try:
                return runner.run(self, args, kwargs)
            except Exception:
                if attempt >= self.retries:
                    raise
                attempt += 1

    def map(self, iterable: Iterable, *, order_outputs: bool = True) -> List[Any]:
        """Apply the function to each item concurrently; return the results."""
        return self.app._runner().map(self, iterable, order_outputs=order_outputs)

    def spawn(self, *args, **kwargs):
        """Fire-and-forget: returns a handle with ``.get()`` for the result."""
        return self.app._runner().spawn(self, args, kwargs)

    def __call__(self, *args, **kwargs):
        raise TypeError(
            f"{self.name} is a Studio function — call {self.fn.__name__}.remote(...) to "
            f"dispatch it, or .local(...) to run it here."
        )

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        bits = [f"image={self.image.ref}"]
        if self.gpu:
            bits.append(f"gpu={self.gpu!r}")
        return f"<studio.Function {self.name} ({', '.join(bits)})>"


class App:
    """A named collection of functions sharing a default image + config."""

    def __init__(self, name: str, *, image: Optional[Image] = None, config: Optional[StudioConfig] = None):
        self.name = name
        self.image = image
        self.config = config or StudioConfig.from_env()
        self.functions: Dict[str, Function] = {}
        self._runner_obj = None

    def function(
        self,
        _fn: Optional[Callable] = None,
        *,
        image: Optional[Image] = None,
        gpu: Optional[str] = None,
        cpu: Optional[float] = None,
        memory=None,
        timeout: Optional[int] = None,
        secrets: Iterable = (),
        volumes: Iterable = (),
        schedule=None,
        billing: Optional[str] = None,
        retries: int = 0,
        serialization: str = "auto",
        name: Optional[str] = None,
    ):
        """Decorator that registers a function for remote execution."""

        def deco(fn: Callable) -> Function:
            f = Function(
                self, fn,
                image=image, gpu=gpu, cpu=cpu, memory=memory, timeout=timeout,
                secrets=tuple(secrets), volumes=tuple(volumes), schedule=schedule,
                billing=billing, retries=retries, serialization=serialization, name=name,
            )
            self.functions[f.name] = f
            return f

        return deco(_fn) if callable(_fn) else deco

    # ---- runner lifecycle -------------------------------------------------

    def _runner(self):
        if self._runner_obj is None:
            from .runners import make_runner
            self._runner_obj = make_runner(self.config)
        return self._runner_obj

    @contextmanager
    def run(self, *, mode: Optional[str] = None):
        """Optional context that pins a runner (e.g. ``with app.run(mode='local')``)."""
        from .runners import make_runner
        cfg = self.config if mode is None else replace(self.config, mode=mode)
        previous = self._runner_obj
        self._runner_obj = make_runner(cfg)
        try:
            yield self
        finally:
            try:
                self._runner_obj.close()
            except Exception:
                pass
            self._runner_obj = previous

    def deploy(self) -> dict:
        """Register every function with the on-chain-adjacent ``aicf.fn.*`` registry."""
        return self._runner().deploy(list(self.functions.values()))

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<studio.App {self.name!r} fns={list(self.functions)}>"
