"""animica.mcp — the Animica Model Context Protocol (MCP) server.

ONE MCP server that exposes Animica's READ + COMPUTE capabilities to any
MCP client. The same server powers both ecosystems that converged on MCP:

  * Anthropic / Claude Code  — stdio transport (`animica mcp serve`).
  * OpenAI Apps SDK / GPTs   — streamable-HTTP transport
    (`animica mcp serve --transport http`).

HARD SECURITY BOUNDARY (non-negotiable): the exposed tool surface is
**READ + COMPUTE only**. There is NO transaction signing, NO spending /
transfers, NO private-key access and NO wallet mutation anywhere in the
exposed tools. Wallet tools READ balances/addresses only. The JSON-RPC
seam additionally enforces a hard allow-list of read methods, so even a
buggy tool cannot reach a mutating method.

Layout
------
* ``seams``  — backend access (HTTP/JSON-RPC + public defaults). The only
  place that touches the network; the single mock point for tests. Imports
  no MCP SDK, so it (and ``tools``) import cleanly on a base install.
* ``tools``  — the tool implementations (pure read/compute). Importable and
  testable WITHOUT the optional ``mcp`` SDK installed.
* ``server`` — lazily imports the ``mcp`` SDK, registers ``tools`` on a
  FastMCP instance and runs a transport.

The MCP SDK lives in the optional ``[mcp]`` extra; ``animica mcp serve``
imports it lazily and prints a clean ``pip install "animica[mcp]"`` hint
if it is absent.
"""

from __future__ import annotations

from .tools import TOOLS, ToolSpec  # noqa: F401  (re-export for CLI/tests)

__all__ = ["TOOLS", "ToolSpec"]
