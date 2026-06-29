"""
animica mcp — run the Animica Model Context Protocol (MCP) server.

ONE MCP server that exposes Animica's READ + COMPUTE tools to MCP clients in BOTH
ecosystems that converged on MCP:

  * Claude Code / Anthropic — stdio transport (default):
        animica mcp serve

  * OpenAI Apps SDK / GPTs  — streamable-HTTP transport:
        animica mcp serve --transport http --host 0.0.0.0 --port 8765

The MCP SDK ships in the optional ``[mcp]`` extra to keep the base install light.
``serve`` imports it lazily and prints a clean ``pip install "animica[mcp]"`` hint
if it is absent. ``tools`` and ``info`` work without the SDK.

SECURITY: the exposed surface is READ + COMPUTE only — no signing, spending,
transfers, or private-key access.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

console = Console()

app = typer.Typer(
    name="mcp",
    help="Run the Animica MCP server (READ/COMPUTE tools for Claude + OpenAI).",
    no_args_is_help=True,
)


@app.command("serve")
def serve(
    transport: str = typer.Option(
        "stdio", "--transport", "-t",
        help="Transport: stdio (Claude Code) | http (OpenAI Apps SDK) | sse",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host (http/sse only)"),
    port: int = typer.Option(8765, "--port", help="Bind port (http/sse only)"),
) -> None:
    """Start the Animica MCP server."""
    from animica.mcp.server import McpNotInstalled, serve as _serve

    # stdio speaks MCP on stdout — keep it pristine (banner to stderr only).
    if transport.lower() != "stdio":
        console.print(
            f"[cyan]Animica MCP[/] → {transport} on http://{host}:{port}/mcp "
            f"({len(_tool_specs())} READ/COMPUTE tools)"
        )
    try:
        _serve(transport=transport, host=host, port=port)
    except McpNotInstalled as e:
        # markup=False so the literal "animica[mcp]" in the hint is not parsed as
        # rich markup (which would silently drop the "[mcp]").
        console.print(str(e), style="bold red", markup=False)
        raise typer.Exit(code=2)


@app.command("tools")
def tools(
    json_output: bool = typer.Option(False, "--json", help="Emit JSON instead of a table"),
) -> None:
    """List the READ/COMPUTE tools the server exposes (works without the SDK)."""
    specs = _tool_specs()
    if json_output:
        import json

        typer.echo(json.dumps(
            [{"name": s.name, "category": s.category, "summary": s.summary} for s in specs],
            indent=2,
        ))
        return
    table = Table(title=f"Animica MCP tools ({len(specs)}) — READ + COMPUTE only")
    table.add_column("Tool", style="cyan", no_wrap=True)
    table.add_column("Category", style="magenta")
    table.add_column("Summary")
    for s in specs:
        table.add_row(s.name, s.category, s.summary)
    console.print(table)


@app.command("info")
def info() -> None:
    """Show how to wire the server into Claude Code and OpenAI."""
    console.print("[bold cyan]Animica MCP server[/] — one server, two ecosystems (MCP).\n")
    console.print("[bold]Install:[/]  pip install \"animica\\[mcp]\"\n")
    console.print("[bold]Claude Code (stdio):[/]")
    console.print('  claude mcp add animica -- animica mcp serve')
    console.print("  or add the plugin from the animica plugin marketplace.\n")
    console.print("[bold]OpenAI Apps SDK / GPT (HTTP):[/]")
    console.print("  animica mcp serve --transport http --host 0.0.0.0 --port 8765")
    console.print("  then expose https://<host>/mcp to the Apps SDK; a custom GPT can")
    console.print("  alternatively call the OpenAI-compatible /v1 API directly.\n")
    console.print("[dim]Surface: READ + COMPUTE only — no signing, spending, or key access.[/]")


def _tool_specs():
    from animica.mcp.tools import TOOLS

    return TOOLS
