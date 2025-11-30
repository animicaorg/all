from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from itertools import count
from typing import (Awaitable, Callable, Dict, Iterable, List, Optional,
                    Protocol, Tuple)

log = logging.getLogger("animica.p2p.router")


# ---- Minimal interfaces the router relies on ---------------------------------


class ConnLike(Protocol):
    """Subset of a secure connection used by handlers."""

    remote_addr: str

    async def send_frame(
        self, msg_id: int, payload: bytes, *, acks: bool = False
    ) -> None: ...
    def is_closed(self) -> bool: ...


@dataclass(frozen=True)
class Frame:
    """Envelope from p2p/wire/frames.py (light alias for typing here)."""

    msg_id: int
    seq: int
    flags: int
    payload: bytes


class Handler(Protocol):
    """
    Protocol all protocol handlers should implement.
    - msg_ids(): iterable of numeric message IDs this handler accepts
    - handle(): process a single frame (must catch/translate its own errors)
    """

    def msg_ids(self) -> Iterable[int]: ...
    async def handle(self, conn: ConnLike, frame: Frame) -> None: ...


# ---- Router ------------------------------------------------------------------

Subscriber = Callable[[ConnLike, Frame], Awaitable[None]]


class Router:
    """
    Central router:
      • Registers protocol handlers by message ID
      • Dispatches inbound frames to the appropriate handler
      • Broadcasts frames to per-msg_id subscribers (debug, tracing, side-effects)
    """

    def __init__(
        self,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        events: Optional[object] = None,
    ) -> None:
        self._loop = loop or asyncio.get_event_loop()
        self._events = events  # optional event-bus (p2p/node/events.py)
        self._handlers: Dict[int, Handler] = {}
        self._subs: Dict[int, List[Tuple[int, Subscriber]]] = {}
        self._sub_id_gen = count(start=1)
        # Stats
        self._dispatch_ok = 0
        self._dispatch_drop_nohandler = 0
        self._dispatch_errors = 0

    # -- Handler registry ------------------------------------------------------

    def add_handler(self, handler: Handler) -> None:
        """Register a handler for all of its declared message IDs."""
        for mid in handler.msg_ids():
            if mid in self._handlers:
                # Deterministic: last write wins, but warn loudly
                prev = type(self._handlers[mid]).__name__
                log.warning(
                    "Overriding handler for msg_id=%s (%s -> %s)",
                    mid,
                    prev,
                    type(handler).__name__,
                )
            self._handlers[mid] = handler
        log.debug(
            "Mounted handler %s for msg_ids=%s",
            type(handler).__name__,
            list(handler.msg_ids()),
        )

    def remove_handler_for(self, msg_id: int) -> None:
        self._handlers.pop(msg_id, None)

    # -- Subscriptions ---------------------------------------------------------

    def on(self, msg_id: int, callback: Subscriber) -> int:
        """
        Subscribe an async callback to a specific message ID.
        Returns an integer subscription id that can be used to remove it.
        """
        sid = next(self._sub_id_gen)
        self._subs.setdefault(msg_id, []).append((sid, callback))
        return sid

    def off(self, subscription_id: int) -> None:
        """Remove a subscription by id."""
        for mid, subs in list(self._subs.items()):
            kept = [(sid, cb) for (sid, cb) in subs if sid != subscription_id]
            if kept:
                self._subs[mid] = kept
            else:
                self._subs.pop(mid, None)

    # -- Dispatch --------------------------------------------------------------

    async def dispatch(self, conn: ConnLike, frame: Frame) -> None:
        """
        Route an inbound frame:
          1) Deliver to the registered handler for frame.msg_id (if any)
          2) Fan-out to subscribers for that msg_id (best-effort)
        """
        handler = self._handlers.get(frame.msg_id)
        if handler is None:
            self._dispatch_drop_nohandler += 1
            log.debug(
                "No handler for msg_id=%s from %s",
                frame.msg_id,
                getattr(conn, "remote_addr", "?"),
            )
            # Still notify subscribers (useful for tracing unknown traffic)
            await self._notify_subscribers(conn, frame)
            return

        try:
            await handler.handle(conn, frame)
            self._dispatch_ok += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._dispatch_errors += 1
            log.warning(
                "Handler error for msg_id=%s (%s)",
                frame.msg_id,
                type(handler).__name__,
                exc_info=e,
            )
        finally:
            # Subscribers are fire-and-forget; they must handle their own errors
            await self._notify_subscribers(conn, frame)

    async def _notify_subscribers(self, conn: ConnLike, frame: Frame) -> None:
        subs = self._subs.get(frame.msg_id)
        if not subs:
            return
        # Fan out concurrently but isolate failures
        tasks = [asyncio.create_task(cb(conn, frame)) for _, cb in subs]
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)
        for t in done:
            if t.cancelled():
                continue
            exc = t.exception()
            if exc:
                log.debug("subscriber callback error", exc_info=exc)
        for t in pending:
            t.cancel()

    # -- Introspection ---------------------------------------------------------

    def snapshot(self) -> Dict[str, object]:
        """Lightweight state for /health or metrics export."""
        return {
            "handlers": {mid: type(h).__name__ for mid, h in self._handlers.items()},
            "subscriptions": {mid: len(lst) for mid, lst in self._subs.items()},
            "stats": {
                "ok": self._dispatch_ok,
                "drop_nohandler": self._dispatch_drop_nohandler,
                "errors": self._dispatch_errors,
            },
        }
