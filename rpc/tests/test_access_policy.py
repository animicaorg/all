from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient

from rpc import deps
from rpc.access_policy import AccessMode, AccessPolicy
from rpc.errors import BootstrapOnlyEndpoint, RpcMethodRestricted
from rpc.server import create_app
from rpc.config import Config


class DummyCtx:
    def __init__(self, headers: dict | None = None, client: tuple[str, int] | None = ("127.0.0.1", 0)):
        self.headers = headers or {}
        self.client = client


def test_public_bootstrap_allows_bootstrap_methods_only() -> None:
    policy = AccessPolicy(mode=AccessMode.PUBLIC_BOOTSTRAP, bootstrap_methods={"bootstrap.getManifest"})
    ctx = DummyCtx()
    policy.authorize("bootstrap.getManifest", ctx)  # should not raise

    with pytest.raises(RpcMethodRestricted):
        policy.authorize("state.getBalance", ctx)


def test_private_full_requires_token() -> None:
    policy = AccessPolicy(mode=AccessMode.PRIVATE_FULL, admin_token="secret")
    ctx_denied = DummyCtx()
    with pytest.raises(RpcMethodRestricted):
        policy.authorize("chain.getHead", ctx_denied)

    ctx_allowed = DummyCtx(headers={"x-animica-admin-token": "secret"})
    policy.authorize("chain.getHead", ctx_allowed)


def test_rpc_endpoint_rejects_restricted_methods(monkeypatch, tmp_path) -> None:
    dummy_block_db = types.SimpleNamespace(get_genesis_hash=lambda: b"\x00" * 32)
    dummy_ctx = types.SimpleNamespace(block_db=dummy_block_db)

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(deps, "ensure_started", lambda: dummy_ctx)
    monkeypatch.setattr(deps, "startup", _noop)
    monkeypatch.setattr(deps, "shutdown", _noop)

    cfg = Config(
        host="127.0.0.1",
        port=0,
        db_uri=f"sqlite:///{tmp_path}/rpc.db",
        chain_id=1,
        access_mode=AccessMode.PUBLIC_BOOTSTRAP.value,
    )

    app = create_app(cfg)
    with TestClient(app) as client:
        allowed = client.post(
            "/rpc",
            json={"jsonrpc": "2.0", "id": 1, "method": "bootstrap.getManifest", "params": []},
        )
        assert allowed.status_code == 200
        assert "result" in allowed.json()

        denied = client.post(
            "/rpc",
            json={"jsonrpc": "2.0", "id": 2, "method": "state.getBalance", "params": ["0x0"]},
        )
        body = denied.json()
        assert body.get("error", {}).get("code") == -32040
        assert "restricted" in body.get("error", {}).get("message", "").lower()


def test_bootstrap_only_error(monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_RPC_BOOTSTRAP_ONLY", "1")
    policy = AccessPolicy.from_config(Config(host="0.0.0.0", port=0, db_uri=":memory:", chain_id=1))
    ctx = DummyCtx()

    # Allowed bootstrap method
    policy.authorize("bootstrap.getManifest", ctx)

    with pytest.raises(BootstrapOnlyEndpoint) as exc:
        policy.authorize("state.getBalance", ctx)

    assert exc.value.code == -32070


def test_bootstrap_node_restricts_non_bootstrap_methods(monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_RPC_ACCESS_MODE", AccessMode.PRIVATE_FULL.value)
    monkeypatch.setenv("ANIMICA_RPC_BOOTSTRAP_NODE", "1")
    policy = AccessPolicy.from_config(Config(host="0.0.0.0", port=0, db_uri=":memory:", chain_id=1))
    ctx = DummyCtx()

    policy.authorize("bootstrap.getManifest", ctx)

    with pytest.raises(BootstrapOnlyEndpoint):
        policy.authorize("state.getBalance", ctx)
