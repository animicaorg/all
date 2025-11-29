from __future__ import annotations

import httpx
from functools import wraps
from typing import Any, Callable, List, Optional

_active_router: Optional["MockRouter"] = None


class Route:
    def __init__(self, router: "MockRouter", url: str) -> None:
        self.router = router
        self.url = url
        self._responses: List[Any] = []
        self.called = False

    def mock(self, return_value: Any | None = None, side_effect: Any | None = None) -> "Route":
        if side_effect is not None:
            if callable(side_effect):
                self._responses.append(side_effect)
            elif isinstance(side_effect, list):
                self._responses.extend(side_effect)
            else:
                self._responses.append(lambda: side_effect)
        elif return_value is not None:
            self._responses.append(lambda: return_value)
        self.router.routes.append(self)
        return self

    def next_response(self) -> Any:
        self.called = True
        if not self._responses:
            raise RuntimeError("No mocked response configured")
        responder = self._responses.pop(0)
        return responder() if callable(responder) else responder

    def has_responses(self) -> bool:
        return bool(self._responses)


class MockRouter:
    def __init__(self) -> None:
        self.routes: List[Route] = []
        self._original_post = None

    def __enter__(self) -> "MockRouter":
        global _active_router
        _active_router = self
        self._patch_httpx()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        global _active_router
        _active_router = None
        self._restore_httpx()

    def _patch_httpx(self) -> None:
        original_post = httpx.AsyncClient.post

        async def fake_post(self, url: str, *args: Any, **kwargs: Any):  # type: ignore[override]
            if _active_router is None:
                return await original_post(self, url, *args, **kwargs)
            for route in _active_router.routes:
                if route.url == url and route.has_responses():
                    resp = route.next_response()
                    if isinstance(resp, Exception):
                        raise resp
                    if isinstance(resp, httpx.Response):
                        return resp
                    raise RuntimeError("Invalid mocked response type")
            raise RuntimeError(f"No route matched URL {url}")

        self._original_post = original_post
        httpx.AsyncClient.post = fake_post  # type: ignore[assignment]

    def _restore_httpx(self) -> None:
        if self._original_post is not None:
            httpx.AsyncClient.post = self._original_post  # type: ignore[assignment]


def post(url: str) -> Route:
    if _active_router is None:
        raise RuntimeError("respx.post must be used inside a mock context")
    return Route(_active_router, url)


def mock(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with MockRouter():
            return func(*args, **kwargs)

    return wrapper

__all__ = ["mock", "post", "MockRouter", "Route"]
