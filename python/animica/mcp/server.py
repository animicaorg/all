"""animica.mcp.server — build + run the Animica MCP server.

The MCP SDK (``mcp`` on PyPI, the ``[mcp]`` extra) is imported lazily so a base
``pip install animica`` stays light and the rest of ``animica.mcp`` (seams +
tools) imports without it. Two transports:

  * ``stdio``            — for Claude Code / Anthropic MCP clients (the plugin).
  * ``streamable-http``  — for OpenAI's Apps SDK / any HTTP MCP client.
"""

from __future__ import annotations

from typing import Any

from .tools import TOOLS

SERVER_NAME = "animica"

INSTRUCTIONS = (
    "Animica MCP server — READ + COMPUTE tools for the Animica AI + blockchain "
    "network. Lead with the AI + quantum capabilities: ask the ENA AI "
    "(animica_ai_ask), generate and verify quantum-random draws "
    "(animica_quantum_draw / animica_quantum_verify), verify qDNA training "
    "provenance, estimate Studio compute cost, and read chain/pool stats. "
    "Wallet/chain tools are READ-ONLY: this server never signs, spends, transfers, "
    "or accesses private keys."
)

_INSTALL_HINT = (
    'the Animica MCP server needs the MCP SDK. Install it with:\n'
    '    pip install "animica[mcp]"\n'
    "(it lives in an optional extra to keep the base install light)."
)


class McpNotInstalled(RuntimeError):
    """Raised when the optional ``mcp`` SDK is not importable."""


def _import_fastmcp():
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore
    except ImportError as e:  # pragma: no cover - exercised via CLI hint path
        raise McpNotInstalled(_INSTALL_HINT) from e
    return FastMCP


def build_server() -> Any:
    """Construct a FastMCP server with every Animica tool registered.

    Raises :class:`McpNotInstalled` (with a clean pip hint) if the ``mcp`` SDK is
    not installed.
    """
    FastMCP = _import_fastmcp()
    mcp = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)
    for spec in TOOLS:
        # Register the plain read/compute function. FastMCP derives the tool name
        # from __name__ and the schema/description from the signature + docstring.
        mcp.tool(name=spec.name, description=spec.summary)(spec.fn)
    return mcp


def serve(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the server on the given transport ('stdio' or 'http'/'sse')."""
    mcp = build_server()
    t = (transport or "stdio").lower()
    if t in ("http", "streamable-http", "streamable_http"):
        # FastMCP reads host/port from its settings.
        try:
            mcp.settings.host = host
            mcp.settings.port = port
        except Exception:  # noqa: BLE001 - older SDKs differ; default 0.0.0.0:8000
            pass
        mcp.run(transport="streamable-http")
    elif t == "sse":
        try:
            mcp.settings.host = host
            mcp.settings.port = port
        except Exception:  # noqa: BLE001
            pass
        mcp.run(transport="sse")
    else:
        mcp.run()  # stdio


def main() -> None:
    """Entry point for ``python -m animica.mcp.server`` (stdio)."""
    import os

    serve(transport=os.environ.get("MCP_TRANSPORT", "stdio"))


if __name__ == "__main__":
    main()
