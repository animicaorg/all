from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI

try:
    # httpx >=0.28
    from httpx import ASGITransport, AsyncClient
except Exception:  # pragma: no cover - fallback for older httpx
    ASGITransport = None  # type: ignore
    AsyncClient = None  # type: ignore

try:
    # FastAPI TestClient (sync) — useful for a few simple tests
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None  # type: ignore

# App factory and (optionally) config helpers
from studio_services.app import create_app  # type: ignore[import]

# If your implementation exposes a Config class, we accept it; otherwise we just build from env.
try:  # pragma: no cover - optional
    from studio_services.config import Config  # type: ignore
except Exception:  # pragma: no cover
    Config = None  # type: ignore


# ----------------------------
# Asyncio event loop for tests
# ----------------------------
@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """Create an event loop for the entire test session (pytest-asyncio compatible)."""
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


# ----------------------------
# Temp workspace & environment
# ----------------------------
@pytest.fixture(scope="session")
def tmp_workspace(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Session-scoped temporary workspace holding storage/, sqlite DB, and artifacts."""
    root = tmp_path_factory.mktemp("studio_services_ws")
    (root / "storage").mkdir(parents=True, exist_ok=True)
    yield root
    # Cleanup after the run
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="session")
def app_env(tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """
    Minimal environment for the app to boot in tests.

    We intentionally keep limits generous to avoid flakiness while still exercising
    rate-limit paths where tests set smaller per-route buckets.
    """
    env: dict[str, str] = {
        "RPC_URL": "http://localhost:0",  # will be mocked by tests that hit the node
        "CHAIN_ID": "1",
        "STORAGE_DIR": str(tmp_workspace / "storage"),
        # Simple JSON for default rate limits understood by studio_services.security.rate_limit
        "RATE_LIMITS": '{"default_per_min": 1000, "burst": 200}',
        # Allow all origins in test; specific CORS tests can override
        "ALLOWED_ORIGINS": "*",
        # Provide a deterministic test API key for routes that require it (e.g., faucet)
        "API_KEYS": "test-key-1,test-key-2",
        # If your app uses SQLite file path explicitly, expose it here (optional).
        "SQLITE_PATH": str(tmp_workspace / "studio_services.sqlite3"),
        # Disable faucet by default; tests can flip it on via monkeypatch
        "FAUCET_KEY": "",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return env


# ----------------------------
# FastAPI application fixtures
# ----------------------------
@pytest.fixture(scope="session")
def app(app_env: dict[str, str]) -> FastAPI:
    """
    Build the FastAPI app once per test session.
    If your create_app accepts a Config object, we construct one; otherwise we let
    create_app read from the environment set by `app_env`.
    """
    if Config is not None:  # type: ignore
        # Best-effort: map env into Config if available
        cfg_kwargs = dict(
            rpc_url=os.environ.get("RPC_URL"),
            chain_id=int(os.environ.get("CHAIN_ID", "1")),
            storage_dir=Path(os.environ["STORAGE_DIR"]),
            rate_limits=os.environ.get("RATE_LIMITS"),
            allowed_origins=os.environ.get("ALLOWED_ORIGINS", "*"),
            faucet_key=os.environ.get("FAUCET_KEY") or None,
            sqlite_path=Path(os.environ.get("SQLITE_PATH", ":memory:")),
            api_keys=[
                k.strip()
                for k in os.environ.get("API_KEYS", "").split(",")
                if k.strip()
            ],
            testing=True,
        )
        # Filter None fields that your Config may not support
        cfg_kwargs = {k: v for k, v in cfg_kwargs.items() if v is not None}
        app = create_app(Config(**cfg_kwargs))  # type: ignore[call-arg]
    else:
        app = create_app()  # type: ignore[call-arg]
    return app


@pytest.fixture(scope="session")
def api_key() -> str:
    """Public test API key to use in authenticated routes."""
    return "test-key-1"


@pytest.fixture(scope="session")
async def aclient(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """
    Shared async HTTP client bound to the ASGI app.
    Prefer using this in tests; it's modern httpx over the app without starting a server.
    """
    if ASGITransport is None or AsyncClient is None:  # pragma: no cover
        pytest.skip("httpx ASGITransport not available in this environment")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture(scope="session")
def client(app: FastAPI) -> Iterator["TestClient"]:
    """
    Optional synchronous client (FastAPI TestClient) when async isn't needed.
    """
    if TestClient is None:  # pragma: no cover
        pytest.skip("FastAPI TestClient not available")
    with TestClient(app) as c:  # type: ignore[call-arg]
        yield c


# ----------------------------
# Convenience headers helper
# ----------------------------
@pytest.fixture
def auth_headers(api_key: str) -> dict[str, str]:
    """Bearer token header for routes protected by API-key."""
    return {"Authorization": f"Bearer {api_key}"}


# ----------------------------
# Per-test scratch directory
# ----------------------------
@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """Per-test scratch directory for writing temp files/artifacts."""
    return tmp_path
