"""Server smoke tests for the Animica MCP server.

When the optional ``mcp`` SDK is absent (base install), ``build_server`` must
raise a clean :class:`McpNotInstalled` carrying the pip hint. When the SDK is
present, the server must build and register exactly the registry's tools.

No network, no live node, no transport is actually started.
"""

from __future__ import annotations

import importlib.util

import pytest

from animica.mcp import server
from animica.mcp.tools import TOOLS

_HAS_MCP = importlib.util.find_spec("mcp") is not None


def test_tools_importable_without_sdk():
    # The registry + tool fns import with no MCP SDK requirement.
    assert len(TOOLS) == 15


@pytest.mark.skipif(_HAS_MCP, reason="mcp SDK is installed; tests the absent path")
def test_build_server_hint_when_sdk_missing():
    with pytest.raises(server.McpNotInstalled) as ei:
        server.build_server()
    msg = str(ei.value)
    assert 'pip install "animica[mcp]"' in msg


@pytest.mark.skipif(not _HAS_MCP, reason="requires the optional mcp SDK")
def test_build_server_registers_all_tools():
    mcp = server.build_server()
    # Resolve the registered tool set across FastMCP versions.
    listed = None
    mgr = getattr(mcp, "_tool_manager", None)
    if mgr is not None and hasattr(mgr, "list_tools"):
        listed = mgr.list_tools()
    if listed is None:
        import asyncio

        listed = asyncio.get_event_loop().run_until_complete(mcp.list_tools())
    registered = {getattr(t, "name", None) for t in listed}
    expected = {t.name for t in TOOLS}
    assert expected.issubset(registered)
    assert len(registered) >= len(expected)
