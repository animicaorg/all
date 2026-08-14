#!/usr/bin/env python3
"""mcp_demo.py — drive the Animica MCP server over stdio from Python.

Usage:  python3 mcp_demo.py
Needs:  pip install animica mcp   (or: pip install animica-mcp)

Spawns `python -m animica.mcp.server` (stdio transport) as a subprocess with
the official `mcp` client SDK, lists its tools, then calls two of them:
`animica_info` (discovery card) and `animica_chain_head` (live chain head).
The server is read-only by design — no private keys, no signing.

Working from a source checkout instead of pip? Point PYTHONPATH at it, e.g.:
  PYTHONPATH=/path/to/animica/python python3 mcp_demo.py
"""
from __future__ import annotations

import asyncio
import os
import sys

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    print("error: the `mcp` package is not installed — pip install mcp", file=sys.stderr)
    sys.exit(1)


def text_of(result) -> str:
    """Concatenate the text content blocks of a tool result."""
    parts = [c.text for c in result.content if getattr(c, "text", None)]
    return "\n".join(parts) if parts else repr(result.content)


async def run() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "animica.mcp.server"],
        env=dict(os.environ),  # propagate PYTHONPATH etc. to the server
    )
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()

                tools = await session.list_tools()
                print(f"tools ({len(tools.tools)}):")
                for t in tools.tools:
                    desc = (t.description or "").strip().splitlines()
                    print(f"  - {t.name}: {desc[0] if desc else ''}")

                for name in ("animica_info", "animica_chain_head"):
                    print(f"\n=== {name} ===")
                    result = await session.call_tool(name, {})
                    if getattr(result, "isError", False):
                        print(f"tool returned an error: {text_of(result)}", file=sys.stderr)
                        return 1
                    print(text_of(result))
    except Exception as e:
        print(f"error: MCP session failed: {e}", file=sys.stderr)
        print("hint: is `animica` importable? Try: python -m animica.mcp.server",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
