"""
Regression tests for the 6.0.0 RPC hardening cluster (C08 / M02 / M06):

* ANM-C08 — optional bearer-token auth on the JSON-RPC HTTP endpoints.
* ANM-M02 — refuse admin/debug/dangerous methods from non-loopback clients when
             the server is bound to a public address AND opt-in is enabled.
* ANM-M06 — never emit a wildcard CORS origin together with credentials.

Every test proves BOTH the fail-closed behavior AND that the current
open/backward-compatible behavior is preserved by default.
"""

from __future__ import annotations

# DEV-ONLY fake PQ backend + skip genesis bootstrap so RPC tests don't require a
# production PQ install or a synced data dir. Mirrors rpc/tests/__init__.py.
import os

os.environ.setdefault("ANIMICA_UNSAFE_PQ_FAKE", "1")
os.environ.setdefault("ANIMICA_UNSAFE_SKIP_GENESIS_BOOTSTRAP", "1")

import pytest
from fastapi.testclient import TestClient

from rpc import deps
from rpc.access_policy import AccessMode, AccessPolicy
from rpc.config import Config
from rpc.errors import RpcMethodRestricted
from rpc.server import create_app


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class DummyCtx:
    """Minimal ctx matching what AccessPolicy.authorize inspects."""

    def __init__(self, headers=None, client=("127.0.0.1", 0)):
        self.headers = headers or {}
        self.client = client


def _cfg(**kw) -> Config:
    base = dict(
        host="127.0.0.1",
        port=0,
        db_uri="sqlite:///:memory:",
        chain_id=9999,  # bypasses mainnet/testnet/devnet genesis validation
        logging="ERROR",
        cors_allow_origins=["*"],
    )
    base.update(kw)
    return Config(**base)


@pytest.fixture()
def no_deps(monkeypatch):
    """Neutralize deps startup/shutdown so no real P2P/genesis work happens."""

    async def _noop_startup(cfg=None):
        return None

    async def _noop_shutdown():
        return None

    monkeypatch.setattr(deps, "startup", _noop_startup)
    monkeypatch.setattr(deps, "shutdown", _noop_shutdown)
    yield


def _rpc(client: TestClient, method="rpc.listMethods", **kw):
    return client.post(
        "/rpc", json={"jsonrpc": "2.0", "id": 1, "method": method}, **kw
    )


# --------------------------------------------------------------------------- #
# ANM-M06 — CORS wildcard + credentials
# --------------------------------------------------------------------------- #
def test_cors_wildcard_never_emits_credentials(no_deps):
    """A wildcard origin must not be paired with Allow-Credentials, even when a
    (browser) cookie is present — the classic credentialed-origin-reflection."""
    app = create_app(_cfg(cors_allow_origins=["*"], cors_allow_credentials=True))
    with TestClient(app) as client:
        r = _rpc(
            client,
            headers={"Origin": "https://evil.example", "Cookie": "session=abc"},
        )
        assert r.status_code == 200
        # Credentials must NOT be granted to a wildcard/reflected origin.
        assert r.headers.get("access-control-allow-credentials") != "true"


def test_cors_default_no_credentials(no_deps):
    """Default config keeps credentials off (safe default)."""
    app = create_app(_cfg(cors_allow_origins=["https://console.animica.org"]))
    with TestClient(app) as client:
        r = _rpc(client, headers={"Origin": "https://console.animica.org"})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-credentials") != "true"
        # explicit allowlisted origin is still reflected (calls keep working)
        assert (
            r.headers.get("access-control-allow-origin")
            == "https://console.animica.org"
        )


def test_cors_explicit_origin_may_use_credentials(no_deps):
    """Credentialed CORS is still allowed for an explicit (non-wildcard) origin."""
    app = create_app(
        _cfg(
            cors_allow_origins=["https://console.animica.org"],
            cors_allow_credentials=True,
        )
    )
    with TestClient(app) as client:
        r = _rpc(client, headers={"Origin": "https://console.animica.org"})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-credentials") == "true"
        assert (
            r.headers.get("access-control-allow-origin")
            == "https://console.animica.org"
        )


# --------------------------------------------------------------------------- #
# ANM-C08 — bearer-token auth
# --------------------------------------------------------------------------- #
def test_auth_disabled_by_default_is_open(no_deps):
    """No token configured → endpoint stays open (backward compatible)."""
    app = create_app(_cfg())
    with TestClient(app) as client:
        r = _rpc(client)
        assert r.status_code == 200
        assert "result" in r.json()


def test_auth_enabled_rejects_missing_and_wrong_token(no_deps):
    app = create_app(_cfg(auth_token="s3cr3t"))
    with TestClient(app) as client:
        missing = _rpc(client)
        assert missing.status_code == 401
        assert missing.json()["error"]["code"] == -32003

        wrong = _rpc(client, headers={"Authorization": "Bearer nope"})
        assert wrong.status_code == 401


def test_auth_enabled_accepts_valid_token_both_headers(no_deps):
    app = create_app(_cfg(auth_token="s3cr3t"))
    with TestClient(app) as client:
        bearer = _rpc(client, headers={"Authorization": "Bearer s3cr3t"})
        assert bearer.status_code == 200
        assert "result" in bearer.json()

        header = _rpc(client, headers={"X-Animica-Auth-Token": "s3cr3t"})
        assert header.status_code == 200


def test_auth_enabled_leaves_health_and_metadata_open(no_deps):
    """Liveness/metadata probes must not require a token."""
    app = create_app(_cfg(auth_token="s3cr3t"))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/version").status_code == 200
        # GET banner at root is not the JSON-RPC POST surface → open
        assert client.get("/").status_code == 200


def test_auth_enabled_does_not_block_cors_preflight(no_deps):
    """OPTIONS preflight must never be gated by the bearer check."""
    app = create_app(
        _cfg(cors_allow_origins=["https://console.animica.org"], auth_token="s3cr3t")
    )
    with TestClient(app) as client:
        pre = client.options(
            "/rpc",
            headers={
                "Origin": "https://console.animica.org",
                "Access-Control-Request-Method": "POST",
            },
        )
        # Preflight succeeds (200) without a token.
        assert pre.status_code == 200


# --------------------------------------------------------------------------- #
# ANM-M02 — sensitive-method restriction on public bind
# --------------------------------------------------------------------------- #
SENSITIVE = ["wallet.send", "p2p.addPeer", "sync.force", "debug.traceTx"]


def test_sensitive_open_by_default_preserves_behavior():
    """Default (restrict_sensitive off) leaves sensitive methods callable — this
    is the current external behavior we must not break by default."""
    policy = AccessPolicy(
        mode=AccessMode.LOCAL_DEV, bind_host="0.0.0.0", restrict_sensitive=False
    )
    remote = DummyCtx(client=("203.0.113.9", 40000))
    for m in SENSITIVE:
        policy.authorize(m, remote)  # must not raise


def test_sensitive_refused_from_remote_when_enabled_on_public_bind():
    policy = AccessPolicy(
        mode=AccessMode.LOCAL_DEV, bind_host="0.0.0.0", restrict_sensitive=True
    )
    remote = DummyCtx(client=("203.0.113.9", 40000))
    for m in SENSITIVE:
        with pytest.raises(RpcMethodRestricted):
            policy.authorize(m, remote)


def test_sensitive_allowed_from_localhost_and_token_when_enabled():
    policy = AccessPolicy(
        mode=AccessMode.LOCAL_DEV,
        bind_host="0.0.0.0",
        restrict_sensitive=True,
        admin_token="tok",
    )
    local = DummyCtx(client=("127.0.0.1", 5))
    tokd = DummyCtx(
        headers={"x-animica-admin-token": "tok"}, client=("203.0.113.9", 5)
    )
    for m in SENSITIVE:
        policy.authorize(m, local)  # local always allowed
        policy.authorize(m, tokd)  # valid admin token allowed


def test_sensitive_gate_inactive_on_loopback_bind():
    """Bound to loopback → gate inactive even if opt-in is set (there are no
    non-loopback callers to protect against)."""
    policy = AccessPolicy(
        mode=AccessMode.LOCAL_DEV, bind_host="127.0.0.1", restrict_sensitive=True
    )
    # Loopback bind is not "public"; the client field here is irrelevant.
    policy.authorize("wallet.send", DummyCtx(client=("127.0.0.1", 5)))


def test_non_sensitive_methods_unaffected_by_gate():
    policy = AccessPolicy(
        mode=AccessMode.LOCAL_DEV, bind_host="0.0.0.0", restrict_sensitive=True
    )
    remote = DummyCtx(client=("203.0.113.9", 40000))
    # Read-only method is not sensitive → allowed.
    policy.authorize("chain.getChainId", remote)


@pytest.mark.parametrize(
    "host,public",
    [
        ("0.0.0.0", True),
        ("::", True),
        ("203.0.113.5", True),
        ("example.com", True),
        ("127.0.0.1", False),
        ("::1", False),
        ("localhost", False),
        (None, False),
        ("", False),
    ],
)
def test_server_bound_public_classification(host, public):
    assert AccessPolicy(bind_host=host).server_bound_public is public


def test_from_config_reads_env_and_host(monkeypatch):
    monkeypatch.setenv("ANIMICA_RPC_RESTRICT_SENSITIVE", "1")
    policy = AccessPolicy.from_config(_cfg(host="0.0.0.0"))
    assert policy.restrict_sensitive is True
    assert policy.bind_host == "0.0.0.0"
    assert policy.server_bound_public is True


# --------------------------------------------------------------------------- #
# Startup warnings
# --------------------------------------------------------------------------- #
def test_startup_warns_when_auth_disabled(no_deps, caplog):
    with caplog.at_level("WARNING", logger="animica.rpc.server"):
        create_app(_cfg())  # no auth_token
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "ANIMICA_RPC_AUTH_TOKEN" in msgs


def test_startup_warns_on_public_bind_without_restriction(no_deps, caplog):
    with caplog.at_level("WARNING", logger="animica.rpc.server"):
        create_app(_cfg(host="0.0.0.0"))
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "ANIMICA_RPC_RESTRICT_SENSITIVE" in msgs
