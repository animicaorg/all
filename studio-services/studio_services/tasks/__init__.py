from __future__ import annotations

"""
Background Tasks Bootstrap

Lightweight glue to mount background workers for the Studio Services app.
This module intentionally avoids hard imports so it can be imported even if
individual task modules aren't present yet. It exposes a single public helper:

    from studio_services.tasks import mount_background_tasks
    app = FastAPI(...)
    mount_background_tasks(app)

On startup:
  • Spins up the verification queue scheduler and workers (if available).
  • Starts the faucet pacer (if configured/available).

On shutdown:
  • Gracefully stops all running task loops.

The concrete implementations live in:
  - studio_services.tasks.scheduler
  - studio_services.tasks.worker
  - studio_services.tasks.queue
  - studio_services.tasks.faucet_pacer

Those modules are resolved at runtime to keep this bootstrap resilient to
refactors and to allow tests to import the package without side effects.
"""

import importlib
import logging
from typing import Any, Optional

from fastapi import FastAPI

log = logging.getLogger(__name__)

__all__ = ["mount_background_tasks"]


def _get_obj(mod_name: str, *candidates: str) -> Optional[Any]:
    """
    Import `mod_name` and return the first attribute that exists and is truthy.
    Returns None if the module or attributes aren't found.
    """
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:  # pragma: no cover
        log.debug("tasks bootstrap: module %s unavailable: %s", mod_name, e)
        return None
    for name in candidates:
        obj = getattr(mod, name, None)
        if obj:
            return obj
    return None


def mount_background_tasks(app: FastAPI) -> None:
    """
    Register startup/shutdown handlers that start/stop background services.

    Stores handles under:
      - app.state.tasks_scheduler
      - app.state.tasks_faucet_pacer
    """
    if getattr(app.state, "tasks_bootstrap_mounted", False):
        log.debug("tasks bootstrap already mounted; skipping")
        return

    async def _startup() -> None:
        # Scheduler (verification queue + workers)
        Scheduler = _get_obj(
            "studio_services.tasks.scheduler",
            "TaskScheduler",
            "Scheduler",
        )
        create_default_scheduler = _get_obj(
            "studio_services.tasks.scheduler",
            "create_default_scheduler",
        )
        scheduler_handle = None
        try:
            if create_default_scheduler:
                scheduler_handle = create_default_scheduler(app)  # type: ignore[misc]
            elif Scheduler:
                # Fallback: construct with sensible defaults
                scheduler_handle = Scheduler(app=app)  # type: ignore[call-arg]
                start = getattr(scheduler_handle, "start", None)
                if callable(start):
                    await start()  # type: ignore[func-returns-value]
            else:
                log.info("tasks: scheduler not available; skipping")
            if scheduler_handle:
                app.state.tasks_scheduler = scheduler_handle
        except Exception as e:  # pragma: no cover
            log.exception("tasks: failed to start scheduler: %s", e)

        # Faucet pacer (optional)
        FaucetPacer = _get_obj("studio_services.tasks.faucet_pacer", "FaucetPacer", "Pacer")
        try:
            pacer = None
            if FaucetPacer:
                pacer = FaucetPacer(app=app)  # type: ignore[call-arg]
                start = getattr(pacer, "start", None)
                if callable(start):
                    await start()  # type: ignore[func-returns-value]
                app.state.tasks_faucet_pacer = pacer
            else:
                log.debug("tasks: faucet pacer not available; skipping")
        except Exception as e:  # pragma: no cover
            log.exception("tasks: failed to start faucet pacer: %s", e)

    async def _shutdown() -> None:
        # Stop pacer first (usually independent)
        pacer = getattr(app.state, "tasks_faucet_pacer", None)
        try:
            if pacer:
                stop = getattr(pacer, "stop", None)
                if callable(stop):
                    await stop()  # type: ignore[func-returns-value]
        except Exception as e:  # pragma: no cover
            log.warning("tasks: error stopping faucet pacer: %s", e)

        # Then stop scheduler/workers
        scheduler = getattr(app.state, "tasks_scheduler", None)
        try:
            if scheduler:
                stop = getattr(scheduler, "stop", None)
                if callable(stop):
                    await stop()  # type: ignore[func-returns-value]
        except Exception as e:  # pragma: no cover
            log.warning("tasks: error stopping scheduler: %s", e)

    # Register handlers
    app.add_event_handler("startup", _startup)
    app.add_event_handler("shutdown", _shutdown)
    app.state.tasks_bootstrap_mounted = True
    log.info("tasks: background task hooks mounted")
