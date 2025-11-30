"""
Retry helpers with exponential backoff and jitter.

Implements the three AWS Architecture Blog strategies:
- full jitter        : sleep U(0, cap)
- equal jitter       : sleep cap/2 + U(0, cap/2)
- decorrelated jitter: sleep U(base, prev*3) capped

Both sync and async variants are provided, plus decorator forms.

Example (sync)
--------------
from omni_sdk.utils.retry import retry_call

def flaky():
    ...

result = retry_call(flaky, retries=5, base=0.2, max_delay=2.0, jitter="full")

Example (async)
---------------
from omni_sdk.utils.retry import aretry_call

async def aflaky():
    ...

result = await aretry_call(aflaky, retries=5, base=0.2, max_delay=2.0)

Decorator form
--------------
from omni_sdk.utils.retry import retryable

@retryable(retries=4, jitter="equal")
def do_it(...):
    ...

Notes
-----
- By default, retries on Exception; customize via `exceptions` and/or `retry_if`.
- `on_retry` callback receives (attempt_index, exception, sleep_seconds).
- `total_timeout` puts a ceiling on overall time spent retrying.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import (Any, Awaitable, Callable, Iterable, Literal, Optional,
                    Sequence, Tuple, Type, TypeVar, Union, cast, overload)

__all__ = [
    "RetryError",
    "BackoffState",
    "backoff_delay",
    "retry_call",
    "aretry_call",
    "retryable",
    "aretryable",
]

T = TypeVar("T")
E = TypeVar("E", bound=BaseException)

JitterMode = Literal["full", "equal", "decorrelated"]


class RetryError(RuntimeError):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, last_exception: BaseException, attempts: int) -> None:
        super().__init__(f"exhausted after {attempts} attempts: {last_exception!r}")
        self.last_exception = last_exception
        self.attempts = attempts


class BackoffState:
    """
    Mutable state for decorrelated jitter.

    You usually don't need to create this yourself; it's managed by retry helpers.
    """

    __slots__ = ("prev_delay",)

    def __init__(self) -> None:
        self.prev_delay: float = 0.0


def _cap(val: float, max_delay: float) -> float:
    return min(val, max_delay)


def backoff_delay(
    attempt: int,
    *,
    base: float,
    max_delay: float,
    jitter: JitterMode = "full",
    state: Optional[BackoffState] = None,
) -> float:
    """
    Compute a backoff delay (in seconds) for the given attempt (1-based).

    - base: initial backoff (seconds), e.g. 0.1
    - max_delay: maximum per-attempt delay (cap)
    - jitter: strategy name (full|equal|decorrelated)
    - state: required only for decorrelated to persist `prev_delay`
    """
    if attempt < 1:
        attempt = 1
    cap = _cap(base * (2 ** (attempt - 1)), max_delay)

    if jitter == "full":
        # 'Full Jitter' — U(0, cap)
        delay = random.uniform(0.0, cap)
    elif jitter == "equal":
        # 'Equal Jitter' — cap/2 + U(0, cap/2)
        delay = (cap * 0.5) + random.uniform(0.0, cap * 0.5)
    elif jitter == "decorrelated":
        # 'Decorrelated Jitter' — U(base, prev*3) capped
        if state is None:
            state = BackoffState()
        low = base
        high = max(base, state.prev_delay * 3.0 if state.prev_delay > 0 else base)
        delay = _cap(random.uniform(low, high), max_delay)
        state.prev_delay = delay
    else:
        raise ValueError(f"unknown jitter mode: {jitter}")
    # Ensure non-negative small floor to avoid zero tight loops
    return max(0.0, float(delay))


def _should_retry(
    exc: BaseException,
    exceptions: Tuple[Type[BaseException], ...],
    retry_if: Optional[Callable[[BaseException], bool]],
) -> bool:
    if not isinstance(exc, exceptions):
        return False
    if retry_if is not None:
        try:
            return bool(retry_if(exc))
        except Exception:
            # If predicate itself fails, be conservative and do not retry
            return False
    return True


def retry_call(
    fn: Callable[..., T],
    *args: Any,
    retries: int = 5,
    base: float = 0.2,
    max_delay: float = 3.0,
    jitter: JitterMode = "full",
    exceptions: Union[Type[BaseException], Sequence[Type[BaseException]]] = Exception,
    retry_if: Optional[Callable[[BaseException], bool]] = None,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
    total_timeout: Optional[float] = None,
    **kwargs: Any,
) -> T:
    """
    Call `fn` with retries.

    Parameters mirror `aretry_call` (sync variant). See module docstring.
    """
    if isinstance(exceptions, type):
        exc_types: Tuple[Type[BaseException], ...] = (exceptions,)
    else:
        exc_types = tuple(exceptions)  # type: ignore[arg-type]

    deadline = time.monotonic() + total_timeout if total_timeout is not None else None
    state = BackoffState()

    attempt = 0
    while True:
        attempt += 1
        try:
            return fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            if not _should_retry(exc, exc_types, retry_if):
                raise
            if attempt > retries:
                raise RetryError(exc, attempts=attempt - 1) from exc

            sleep_s = backoff_delay(
                attempt=attempt,
                base=base,
                max_delay=max_delay,
                jitter=jitter,
                state=state if jitter == "decorrelated" else None,
            )
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RetryError(exc, attempts=attempt - 1) from exc
                sleep_s = min(sleep_s, max(0.0, remaining))

            if on_retry is not None:
                try:
                    on_retry(attempt, exc, sleep_s)
                except Exception:
                    # Don't let callbacks break retry
                    pass

            time.sleep(sleep_s)


async def aretry_call(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    retries: int = 5,
    base: float = 0.2,
    max_delay: float = 3.0,
    jitter: JitterMode = "full",
    exceptions: Union[Type[BaseException], Sequence[Type[BaseException]]] = Exception,
    retry_if: Optional[Callable[[BaseException], bool]] = None,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
    total_timeout: Optional[float] = None,
    **kwargs: Any,
) -> T:
    """
    Async version of `retry_call`. Awaits `fn(*args, **kwargs)` with retries.

    The same semantics for delays, jitter, exceptions, and callback apply.
    """
    if isinstance(exceptions, type):
        exc_types: Tuple[Type[BaseException], ...] = (exceptions,)
    else:
        exc_types = tuple(exceptions)  # type: ignore[arg-type]

    deadline = time.monotonic() + total_timeout if total_timeout is not None else None
    state = BackoffState()

    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            if not _should_retry(exc, exc_types, retry_if):
                raise
            if attempt > retries:
                raise RetryError(exc, attempts=attempt - 1) from exc

            sleep_s = backoff_delay(
                attempt=attempt,
                base=base,
                max_delay=max_delay,
                jitter=jitter,
                state=state if jitter == "decorrelated" else None,
            )
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RetryError(exc, attempts=attempt - 1) from exc
                sleep_s = min(sleep_s, max(0.0, remaining))

            if on_retry is not None:
                try:
                    on_retry(attempt, exc, sleep_s)
                except Exception:
                    pass

            await asyncio.sleep(sleep_s)


def retryable(
    *,
    retries: int = 5,
    base: float = 0.2,
    max_delay: float = 3.0,
    jitter: JitterMode = "full",
    exceptions: Union[Type[BaseException], Sequence[Type[BaseException]]] = Exception,
    retry_if: Optional[Callable[[BaseException], bool]] = None,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
    total_timeout: Optional[float] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for sync functions.

    Example:
        @retryable(retries=4, jitter="equal")
        def fetch(): ...
    """

    def _decorator(fn: Callable[..., T]) -> Callable[..., T]:
        def _wrapped(*args: Any, **kwargs: Any) -> T:
            return retry_call(
                fn,
                *args,
                retries=retries,
                base=base,
                max_delay=max_delay,
                jitter=jitter,
                exceptions=exceptions,
                retry_if=retry_if,
                on_retry=on_retry,
                total_timeout=total_timeout,
                **kwargs,
            )

        _wrapped.__name__ = getattr(fn, "__name__", "_wrapped")  # preserve name
        _wrapped.__doc__ = fn.__doc__
        return _wrapped

    return _decorator


def aretryable(
    *,
    retries: int = 5,
    base: float = 0.2,
    max_delay: float = 3.0,
    jitter: JitterMode = "full",
    exceptions: Union[Type[BaseException], Sequence[Type[BaseException]]] = Exception,
    retry_if: Optional[Callable[[BaseException], bool]] = None,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
    total_timeout: Optional[float] = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """
    Decorator for async functions.

    Example:
        @aretryable(retries=6, jitter="decorrelated")
        async def fetch(): ...
    """

    def _decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        async def _wrapped(*args: Any, **kwargs: Any) -> T:
            return await aretry_call(
                fn,
                *args,
                retries=retries,
                base=base,
                max_delay=max_delay,
                jitter=jitter,
                exceptions=exceptions,
                retry_if=retry_if,
                on_retry=on_retry,
                total_timeout=total_timeout,
                **kwargs,
            )

        _wrapped.__name__ = getattr(fn, "__name__", "_wrapped")  # type: ignore[attr-defined]
        _wrapped.__doc__ = fn.__doc__
        return _wrapped

    return _decorator
