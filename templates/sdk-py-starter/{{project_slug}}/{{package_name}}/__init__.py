"""
{{ project_slug }} — a tiny, batteries-included wrapper around the Animica Python SDK.

Quickstart
----------
>>> from {{ package_name }} import connect
>>> c = connect()  # uses env ANIMICA_RPC_URL / ANIMICA_CHAIN_ID or sensible defaults
>>> head = c.http.call("chain.getHead", [])
>>> print(head)

This module keeps imports light and only pulls in `omni_sdk` inside `connect()`.
"""

from __future__ import annotations

from typing import Optional

__all__ = ["__version__", "connect", "user_agent"]
__version__: str = "{{ version | default('0.1.0') }}"


def user_agent() -> str:
    """
    Return a short User-Agent-ish string you can pass to logs or headers.
    """
    return f"{{ project_slug }}:{__version__}"


def connect(
    rpc_url: Optional[str] = None,
    chain_id: Optional[int] = None,
    *,
    timeout: float = 10.0,
    ws_url: Optional[str] = None,
):
    """
    Create a small, pre-configured SDK client bundle.

    Parameters
    ----------
    rpc_url:
        HTTP RPC endpoint. If None, uses $ANIMICA_RPC_URL or 'http://127.0.0.1:8545'.
    chain_id:
        Chain id integer. If None, uses $ANIMICA_CHAIN_ID or 1337.
    timeout:
        HTTP request timeout in seconds (default 10.0).
    ws_url:
        Optional explicit WebSocket URL. If omitted, the WS client is not created.

    Returns
    -------
    ClientBundle
        An object with fields:
          - http: omni_sdk.rpc.http.Client
          - ws:   omni_sdk.rpc.ws.Client | None
          - config: omni_sdk.config.Config

    Notes
    -----
    - Imports `omni_sdk` lazily so simply importing {{ package_name }} is fast.
    - Keeps a minimal abstraction so you can still access the full SDK surface.
    """
    import os

    # Lazy imports to keep top-level import time minimal
    from omni_sdk.config import Config
    from omni_sdk.rpc.http import Client as HttpClient
    try:
        from omni_sdk.rpc.ws import Client as WsClient  # optional; only if ws_url provided
    except Exception:  # pragma: no cover - ws is optional in some envs
        WsClient = None  # type: ignore[assignment]

    rpc_url = rpc_url or os.getenv("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    if chain_id is None:
        env_chain = os.getenv("ANIMICA_CHAIN_ID")
        chain_id = int(env_chain) if env_chain is not None else 1337

    cfg = Config(rpc_url=rpc_url, chain_id=chain_id, timeout=timeout)
    http = HttpClient(cfg)
    ws = None
    if ws_url and WsClient is not None:
        ws = WsClient(cfg, url_override=ws_url)

    class ClientBundle:
        """
        Lightweight namespace object returned by `connect()`.

        Attributes
        ----------
        http : omni_sdk.rpc.http.Client
            JSON-RPC HTTP client.
        ws : omni_sdk.rpc.ws.Client | None
            WebSocket client (if created).
        config : omni_sdk.config.Config
            Effective configuration used by the clients.
        """

        def __init__(self, http, ws, config):
            self.http = http
            self.ws = ws
            self.config = config

        def __repr__(self) -> str:  # pragma: no cover - trivial
            return (
                f"<{{ package_name }} Client "
                f"rpc={self.config.rpc_url!r} chain_id={self.config.chain_id}>"
            )

    return ClientBundle(http=http, ws=ws, config=cfg)
