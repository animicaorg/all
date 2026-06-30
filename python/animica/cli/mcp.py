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


def _server_command() -> tuple[str, list[str]]:
    """The command a client should run to launch the Animica MCP server.

    Prefer the installed `animica` console script; fall back to the current
    interpreter's `-m animica` so it also works from a source checkout / venv.
    """
    import shutil
    import sys
    exe = shutil.which("animica")
    if exe:
        return exe, ["mcp", "serve"]
    return sys.executable, ["-m", "animica", "mcp", "serve"]


def _client_target(client: str):
    """Return (config_path, servers_key, entry_builder) for a known MCP client."""
    import os
    import sys
    from pathlib import Path

    cmd, args = _server_command()
    home = Path.home()
    # Claude Code & Cursor use {"mcpServers": {name: {command, args}}}.
    if client == "claude":
        return home / ".claude.json", "mcpServers", {"command": cmd, "args": args}
    if client == "cursor":
        return home / ".cursor" / "mcp.json", "mcpServers", {"command": cmd, "args": args}
    if client == "vscode":
        # VS Code native MCP uses {"servers": {name: {type, command, args}}}.
        if sys.platform == "darwin":
            base = home / "Library" / "Application Support" / "Code" / "User"
        elif os.name == "nt":
            base = Path(os.environ.get("APPDATA", home)) / "Code" / "User"
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "Code" / "User"
        return base / "mcp.json", "servers", {"type": "stdio", "command": cmd, "args": args}
    raise typer.BadParameter(f"unknown client {client!r}. Choose: claude | cursor | vscode")


@app.command("install")
def install(
    client: str = typer.Argument(..., help="MCP client: claude | cursor | vscode"),
    name: str = typer.Option("animica", "--name", help="Server name to register."),
    config_path: str = typer.Option(None, "--config-path",
        help="Override the client config file path."),
    print_only: bool = typer.Option(False, "--print",
        help="Print the JSON snippet instead of writing it."),
) -> None:
    """Wire the Animica MCP server into a client's config (merges, never clobbers)."""
    import json
    from pathlib import Path

    client = client.lower().strip()
    default_path, key, entry = _client_target(client)
    path = Path(config_path).expanduser() if config_path else default_path

    if print_only:
        console.print_json(json.dumps({key: {name: entry}}))
        console.print(f"\n[dim]would merge into: {path}[/]")
        return

    # Load existing config (preserve other servers + unrelated keys).
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8")) or {}
        except json.JSONDecodeError:
            console.print(f"[red]✘ {path} is not valid JSON[/] — fix or use --print.", markup=False)
            raise typer.Exit(code=1)
    servers = data.get(key)
    if not isinstance(servers, dict):
        servers = {}
    existed = name in servers
    servers[name] = entry
    data[key] = servers

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    verb = "updated" if existed else "added"
    console.print(f"[green]✔ {verb}[/] MCP server [bold]{name}[/] in {path}")
    console.print(f"  command: {entry['command']} {' '.join(entry['args'])}")
    if client in ("claude", "cursor"):
        console.print("  restart the client to pick it up.")
    console.print("[dim]Surface: READ + COMPUTE only — no signing, spending, or key access.[/]")


def _tool_specs():
    from animica.mcp.tools import TOOLS

    return TOOLS
