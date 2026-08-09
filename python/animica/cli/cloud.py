"""`animica cloud` — the Animica Python Cloud developer CLI (deploy Python to animica.dev).

Commands:
  login      store an API key at ~/.animica/cloud.json (mode 0600)
  whoami     show the authenticated account + plan
  init       scaffold a working starter project
  validate   run the platform's REAL pre-deploy validator locally (offline-capable)
  deploy     extract, validate, upload, anchor + activate a function file
  invoke     call a deployed function (yours by slug, anyone's by owner/slug)
  logs       recent executions and per-execution logs
  status     account overview, or one function's deploy/anchor state
  functions  list your deployed functions
  apps       list your marketplace apps
  earnings   your developer earnings (ANM)

Truth about the deployment model, kept consistent everywhere this CLI prints it: a deployment
is ANCHORED on Animica consensus (a DEPLOY transaction binding owner + source hashes — those
succeed and are consensus-carried) and EXECUTED off-chain in the Python Cloud sandbox.
Consensus never executes the Python itself.

main.py imports every CLI group eagerly at startup, so top-level imports here stay tiny;
animica.cloud (urllib/ast/inspect machinery) is imported inside commands only.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, List, Optional

import typer
from rich.console import Console

console = Console()
app = typer.Typer(help="Animica Python Cloud — deploy and run Python functions on animica.dev.")

DEPLOY_TERMINAL = {"ACTIVE", "FAILED", "ARCHIVED"}


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


@contextmanager
def _api_errors():
    """Convert SDK exceptions into red one-liners + exit 1 (never a raw traceback)."""
    from animica.cloud.errors import (
        ApiError,
        AuthError,
        CloudError,
        NetworkError,
        RateLimitedError,
    )

    try:
        yield
    except AuthError as exc:
        console.print(f"[bold red]Auth failed:[/bold red] {exc} — run `animica cloud login`")
        raise typer.Exit(code=1) from exc
    except RateLimitedError as exc:
        wait = f" (retry after {exc.retry_after}s)" if exc.retry_after else ""
        console.print(f"[bold red]Rate limited:[/bold red] {exc}{wait}")
        raise typer.Exit(code=1) from exc
    except NetworkError as exc:
        console.print(f"[bold red]Network error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except ApiError as exc:
        console.print(f"[bold red]API error:[/bold red] {exc}")
        if exc.details:
            console.print(f"  details: {json.dumps(exc.details, default=str)}")
        raise typer.Exit(code=1) from exc
    except CloudError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


def _client(url: Optional[str] = None, key: Optional[str] = None):
    from animica.cloud.client import CloudClient

    return CloudClient(api_key=key, base_url=url)


def _fmt_anm(nanm: Any) -> str:
    """Format an nANM value (server sends decimal strings for BigInt) as 'X.XXXX ANM'."""
    from animica.cloud.config import format_anm

    if nanm is None:
        return "-"
    try:
        return f"{format_anm(int(str(nanm)))} ANM"
    except (ValueError, TypeError):
        return str(nanm)


def _resolve_own_function(client, slug: str) -> dict:
    fn = client.find_function(slug)
    if fn is None:
        console.print(
            f"[bold red]No function named {slug!r} on this account.[/bold red] "
            f"List yours with `animica cloud functions`."
        )
        raise typer.Exit(code=1)
    return fn


# ---------------------------------------------------------------------------
# Local validation against the platform's real validator artifact
# ---------------------------------------------------------------------------


def _run_local_validator(source: str, entrypoint: str) -> Optional[dict]:
    """Run sandbox/validate.py (the exact file the server runs) in a subprocess.
    Returns the report dict, or None when the validator artifact isn't on this machine."""
    import subprocess

    from animica.cloud.config import MAX_SOURCE_BYTES, find_validator

    validator = find_validator()
    if validator is None:
        return None
    payload = json.dumps({"source": source, "entrypoint": entrypoint, "max_bytes": MAX_SOURCE_BYTES})
    proc = subprocess.run(
        [sys.executable, str(validator)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"validator exited {proc.returncode}: {proc.stderr[-500:]}")
    return json.loads(proc.stdout)


def _print_findings(report: dict) -> None:
    for f in report.get("findings", []):
        sev = f.get("severity", "error")
        color = "red" if sev == "error" else "yellow"
        loc = f"line {f.get('line', 0)}" + (f":{f['col']}" if f.get("col") else "")
        console.print(f"  [{color}]{sev:<7}[/{color}] {loc:<12} {f.get('code')}: {f.get('message')}")


def _validate_or_die(source: str, entrypoint: str, label: str) -> bool:
    """Local pre-flight validation. True when it actually ran (validator present)."""
    report = _run_local_validator(source, entrypoint)
    if report is None:
        console.print(
            "[yellow]local validator not found on this machine — skipping pre-flight; "
            "the platform validates server-side before anything deploys[/yellow]"
        )
        return False
    if not report.get("ok"):
        console.print(f"[bold red]validation failed[/bold red] for {label}:")
        _print_findings(report)
        raise typer.Exit(code=1)
    warnings = [f for f in report.get("findings", []) if f.get("severity") == "warning"]
    if warnings:
        _print_findings({"findings": warnings})
    return True


# ---------------------------------------------------------------------------
# login / whoami
# ---------------------------------------------------------------------------


@app.command()
def login(
    key: Optional[str] = typer.Option(None, "--key", help="API key (anm_mkt_...); prompted if omitted"),
    url: Optional[str] = typer.Option(None, "--url", help="Cloud base URL (default: ANIMICA_CLOUD_URL or https://animica.dev)"),
    no_verify: bool = typer.Option(False, "--no-verify", help="Store without checking the key against the API"),
):
    """Store your Python Cloud API key at ~/.animica/cloud.json (mode 0600).

    Create keys in the animica.dev developer console. The key is verified against the API
    before it is written, so a typo never silently poisons every later command."""
    from animica.cloud.config import API_KEY_PREFIX, save_credentials

    if not key:
        key = typer.prompt("API key", hide_input=True).strip()
    if not key:
        console.print("[bold red]No key given.[/bold red]")
        raise typer.Exit(code=1)
    if not key.startswith(API_KEY_PREFIX):
        console.print(
            f"[yellow]warning: Python Cloud keys start with {API_KEY_PREFIX!r}; "
            f"storing anyway[/yellow]"
        )

    if not no_verify:
        with _api_errors():
            me = _client(url=url, key=key).me()
        who = me.get("address") or me.get("accountId") or me.get("id") or "account"
        console.print(f"key verified: [green]{who}[/green]")

    path = save_credentials(key, base_url=url)
    console.print(f"credentials written : {path} (mode 0600)")


@app.command()
def whoami(
    url: Optional[str] = typer.Option(None, "--url", help="Cloud base URL override"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Show the account your stored/configured API key authenticates as."""
    with _api_errors():
        me = _client(url=url).me()
    if json_output:
        typer.echo(json.dumps(me, indent=2, default=str))
        return
    for label, keys in (
        ("account", ("accountId", "id")),
        ("address", ("address",)),
        ("plan", ("plan", "planKey")),
    ):
        for k in keys:
            if me.get(k) is not None:
                console.print(f"{label:<10}: {me[k]}")
                break
    if me.get("balanceNanm") is not None:
        console.print(f"{'balance':<10}: {_fmt_anm(me['balanceNanm'])}")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

_SCAFFOLD_HANDLER = '''"""Starter Animica Python Cloud project.

Deploy:   animica cloud deploy handler.py
Invoke:   animica cloud invoke hello --data \'{"name": "world"}\'
Logs:     animica cloud logs hello
"""

from animica.cloud import App

app = App("starter")


@app.function(memory_mb=128, timeout=15)
def hello(request):
    """Return a greeting. `request` is the JSON payload the caller sent."""
    name = request.get("name", "world") if isinstance(request, dict) else "world"
    return {"hello": name}


@app.function(memory_mb=256, timeout=60, capabilities=["AI_INFERENCE"])
def summarize(request, ctx):
    """Summarize text with Animica AI (metered per token, settled in ANM)."""
    text = (request or {}).get("text", "")
    if not text:
        return {"error": 'send {"text": "..."}'}
    summary = ctx.ai.infer(f"Summarize in one sentence:\\n\\n{text}", max_tokens=120)
    ctx.log("summarized", len(text), "chars")
    return {"summary": summary}
'''

_SCAFFOLD_README = """# Animica Python Cloud starter

Two functions live in `handler.py`:

* `hello` — pure Python, no capabilities.
* `summarize` — uses Animica AI inference via `ctx.ai.infer` (declares the
  `AI_INFERENCE` capability; usage is metered per token and settled in ANM).

## Workflow

    animica cloud login                # once: store your anm_mkt_ API key
    animica cloud validate handler.py  # offline pre-flight (same checks the platform runs)
    animica cloud deploy handler.py    # upload, anchor on-chain, activate
    animica cloud invoke hello --data '{"name": "world"}'
    animica cloud logs hello

## How it runs

Your deployment is anchored on Animica consensus (a DEPLOY transaction binding
your account and the source hash) and executed off-chain in the Python Cloud
sandbox — no network, read-only filesystem, hard memory/CPU limits. Inside the
sandbox `import animica` exposes the host API: `animica.ai.infer`,
`animica.chain`, `animica.wallet.pay`, `animica.state`, `animica.http.fetch`,
`animica.call`, `animica.log`, `animica.secret` — every privileged call is
checked against the capabilities you declared at deploy time.
"""


@app.command()
def init(
    directory: Path = typer.Argument(Path("."), help="Target directory (created if missing)"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
):
    """Scaffold a working starter project (handler.py + README.md)."""
    directory.mkdir(parents=True, exist_ok=True)
    files = {"handler.py": _SCAFFOLD_HANDLER, "README.md": _SCAFFOLD_README}
    existing = [n for n in files if (directory / n).exists()]
    if existing and not force:
        console.print(
            f"[bold red]Refusing to overwrite:[/bold red] {', '.join(existing)} "
            f"already exist in {directory} (pass --force to replace)"
        )
        raise typer.Exit(code=1)
    for name, content in files.items():
        (directory / name).write_text(content, encoding="utf-8")
        console.print(f"wrote {directory / name}")
    console.print("\nNext:  animica cloud validate " + str(directory / "handler.py"))


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


@app.command()
def validate(
    file: Path = typer.Argument(..., help="Python source file to validate"),
    entrypoint: str = typer.Option("main", "--entrypoint", help="Entrypoint for plain (non-SDK) files"),
    json_output: bool = typer.Option(False, "--json", help="Emit the raw validator report(s)"),
):
    """Run the platform's REAL pre-deploy validator locally (works offline).

    SDK files (using @app.function) are first stripped to the exact deployable source, then
    each registered entrypoint is validated — so what passes here is what the server sees."""
    from animica.cloud.app import extract
    from animica.cloud.errors import ExtractionError

    if not file.is_file():
        console.print(f"[bold red]No such file:[/bold red] {file}")
        raise typer.Exit(code=1)

    try:
        extraction = extract(file)
    except ExtractionError as exc:
        console.print(f"[bold red]Extraction failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    targets: List[tuple] = (
        [(f"{f.config['slug']} (entrypoint {f.entrypoint})", f.entrypoint) for f in extraction.functions]
        if not extraction.bare
        else [(f"{file.name} (entrypoint {entrypoint})", entrypoint)]
    )

    reports = []
    failed = False
    for label, ep in targets:
        report = _run_local_validator(extraction.source, ep)
        if report is None:
            console.print(
                "[bold red]Validator not found on this machine.[/bold red] Set "
                "ANIMICA_CLOUD_VALIDATOR to the platform's sandbox/validate.py, or rely on "
                "server-side validation at deploy time."
            )
            raise typer.Exit(code=2)
        reports.append({"target": label, **report})
        if json_output:
            continue
        if report.get("ok"):
            # The validator infers capabilities from the WHOLE module (shared source), not per
            # entrypoint — label accordingly so nobody thinks `hello` itself calls the AI.
            caps = report.get("capabilities") or []
            cap_note = f"  module capabilities: {', '.join(caps)}" if caps else ""
            console.print(f"[green]OK[/green]      {label}{cap_note}")
            warn = [x for x in report.get("findings", []) if x.get("severity") == "warning"]
            if warn:
                _print_findings({"findings": warn})
        else:
            failed = True
            console.print(f"[bold red]FAILED[/bold red]  {label}")
            _print_findings(report)

    if json_output:
        typer.echo(json.dumps(reports if len(reports) > 1 else reports[0], indent=2))
        failed = any(not r.get("ok") for r in reports)
    raise typer.Exit(code=1 if failed else 0)


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


def _wait_for_deployment(client, deployment: dict, label: str) -> dict:
    """Poll the deployment through VALIDATING -> ... -> ACTIVE/FAILED, echoing transitions."""
    import time

    dep_id = deployment.get("id")
    status = deployment.get("status", "?")
    console.print(f"  {label}: {status}")
    if not dep_id:
        return deployment
    deadline = time.time() + 300  # anchoring waits on real chain confirmations
    last = status
    while status not in DEPLOY_TERMINAL and time.time() < deadline:
        time.sleep(2)
        deployment = client.get_deployment(dep_id)
        status = deployment.get("status", "?")
        if status != last:
            console.print(f"  {label}: {status}")
            last = status
    return deployment


def _report_deployment(deployment: dict, label: str) -> bool:
    status = deployment.get("status")
    if status == "ACTIVE":
        console.print(f"[green]deployed[/green] {label}")
        if deployment.get("endpoint"):
            console.print(f"  endpoint : {deployment['endpoint']}")
        if deployment.get("anchorTxid"):
            console.print(
                f"  anchor   : {deployment['anchorTxid']} "
                f"(consensus-carried DEPLOY tx; execution stays off-chain in the sandbox)"
            )
        else:
            console.print("  anchor   : none (deployment active, unanchored)")
        return True
    if status == "FAILED":
        console.print(f"[bold red]deploy FAILED[/bold red] {label}: {deployment.get('error') or 'no error recorded'}")
        return False
    console.print(
        f"[yellow]{label}: still {status} — check later with `animica cloud status`[/yellow]"
    )
    return True


@app.command()
def deploy(
    file: Path = typer.Argument(Path("handler.py"), help="Python source file to deploy"),
    entrypoint: str = typer.Option("main", "--entrypoint", help="Entrypoint for plain (non-SDK) files"),
    name: Optional[str] = typer.Option(None, "--name", help="Function slug for plain files (default: file stem)"),
    app_slug: Optional[str] = typer.Option(None, "--app", help="Attach the function(s) to one of your apps (slug)"),
    url: Optional[str] = typer.Option(None, "--url", help="Cloud base URL override"),
    skip_validate: bool = typer.Option(False, "--skip-validate", help="Skip the local pre-flight (server still validates)"),
    no_wait: bool = typer.Option(False, "--no-wait", help="Don't poll the deployment to ACTIVE"),
):
    """Deploy a function file: extract, validate, upload a version, anchor + activate.

    SDK files deploy every @app.function they register; plain files deploy one function whose
    entrypoint defaults to `main`."""
    from animica.cloud.app import extract
    from animica.cloud.errors import ExtractionError

    if not file.is_file():
        console.print(f"[bold red]No such file:[/bold red] {file}")
        raise typer.Exit(code=1)

    try:
        extraction = extract(file)
    except ExtractionError as exc:
        console.print(f"[bold red]Extraction failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    if extraction.bare:
        from animica.cloud.app import _slugify, _validate_slug  # shared slug rules

        slug = _validate_slug(name or _slugify(file.stem), "function name")
        configs = [
            {
                "slug": slug,
                "name": slug,
                "entrypoint": entrypoint,
                "timeoutMs": 30_000,
                "memoryMb": 256,
                "capabilities": [],
                "description": "",
                "perCallNanm": "0",
                "requiresAuth": False,
            }
        ]
    else:
        configs = [f.config for f in extraction.functions]

    if not skip_validate:
        for cfg in configs:
            _validate_or_die(extraction.source, cfg["entrypoint"], cfg["slug"])

    with _api_errors():
        client = _client(url=url)

        app_id: Optional[str] = None
        if app_slug:
            match = next((a for a in client.list_apps() if a.get("slug") == app_slug), None)
            if match is None:
                console.print(f"[bold red]No app with slug {app_slug!r} on this account.[/bold red]")
                raise typer.Exit(code=1)
            app_id = match["id"]

        overall_ok = True
        for cfg in configs:
            slug = cfg["slug"]
            console.print(f"\n[bold]{slug}[/bold] (entrypoint {cfg['entrypoint']})")
            existing = client.find_function(slug)
            if existing is None:
                fn = client.create_function(
                    slug,
                    cfg["name"],
                    entrypoint=cfg["entrypoint"],
                    timeout_ms=cfg["timeoutMs"],
                    memory_mb=cfg["memoryMb"],
                    capabilities=cfg["capabilities"],
                    description=cfg["description"],
                    per_call_nanm=int(cfg["perCallNanm"]),
                    requires_auth=cfg["requiresAuth"],
                    app_id=app_id,
                )
                console.print(f"  created function {fn.get('id')}")
            else:
                fn = client.update_function(
                    existing["id"],
                    entrypoint=cfg["entrypoint"],
                    timeoutMs=cfg["timeoutMs"],
                    memoryMb=cfg["memoryMb"],
                    capabilities=cfg["capabilities"],
                    description=cfg["description"],
                    perCallNanm=int(cfg["perCallNanm"]),
                    requiresAuth=cfg["requiresAuth"],
                    **({"appId": app_id} if app_id else {}),
                )
                console.print(f"  updated function {fn.get('id')}")

            version = client.create_version(fn["id"], extraction.source, entrypoint=cfg["entrypoint"])
            console.print(
                f"  version {version.get('version', '?')} uploaded "
                f"({len(extraction.source.encode('utf-8'))} bytes, sha256 {extraction.source_sha256[:16]}…)"
            )
            deployment = client.deploy(fn["id"], version_id=version.get("id"))
            if not no_wait:
                deployment = _wait_for_deployment(client, deployment, slug)
            overall_ok = _report_deployment(deployment, slug) and overall_ok

        if not overall_ok:
            raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# invoke
# ---------------------------------------------------------------------------


def _load_payload(data: Optional[str], file: Optional[Path]) -> Any:
    if data is not None and file is not None:
        console.print("[bold red]Pass at most one of --data / --file.[/bold red]")
        raise typer.Exit(code=1)
    raw: Optional[str] = None
    if data is not None:
        raw = data
    elif file is not None:
        raw = sys.stdin.read() if str(file) == "-" else file.read_text(encoding="utf-8")
    if raw is None:
        return {}
    try:
        return json.loads(raw)
    except ValueError as exc:
        console.print(f"[bold red]Payload is not valid JSON:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc


@app.command()
def invoke(
    target: str = typer.Argument(..., help="Function slug (yours), or owner/slug for any public function"),
    data: Optional[str] = typer.Option(None, "--data", "-d", help="JSON request payload"),
    file: Optional[Path] = typer.Option(None, "--file", "-f", help="Read the JSON payload from a file ('-' = stdin)"),
    max_spend: Optional[str] = typer.Option(
        None, "--max-spend", help="Refuse if the quoted price exceeds this many ANM (e.g. 0.05)"
    ),
    url: Optional[str] = typer.Option(None, "--url", help="Cloud base URL override"),
    json_output: bool = typer.Option(False, "--json", help="Print the full invoke response"),
):
    """Invoke a deployed function and print the result + payment receipt.

    Metered executions cost real ANM — use --max-spend to cap what a single call may charge;
    the platform refuses (never clips) a call quoted above the cap."""
    from animica.cloud.config import anm_to_nanm

    payload = _load_payload(data, file)
    max_spend_nanm: Optional[int] = None
    if max_spend is not None:
        try:
            max_spend_nanm = anm_to_nanm(max_spend)
        except ValueError as exc:
            console.print(f"[bold red]Bad --max-spend:[/bold red] {exc}")
            raise typer.Exit(code=1) from exc

    with _api_errors():
        client = _client(url=url)
        if "/" in target:
            owner, _, slug = target.partition("/")
            res = client.invoke_public(owner, slug, payload, max_spend_nanm=max_spend_nanm)
        else:
            fn = _resolve_own_function(client, target)
            res = client.invoke(fn["id"], payload, max_spend_nanm=max_spend_nanm)

    if json_output:
        typer.echo(json.dumps(res, indent=2, default=str))
        raise typer.Exit(code=0 if res.get("status") == "succeeded" else 1)

    status = res.get("status", "?")
    color = "green" if status == "succeeded" else "red"
    console.print(f"status    : [{color}]{status}[/{color}]  ({res.get('durationMs', 0)} ms)")
    if res.get("requestId"):
        console.print(f"request   : {res['requestId']}")
    if status == "succeeded":
        console.print("result    :")
        typer.echo(json.dumps(res.get("result"), indent=2, default=str))
    else:
        console.print(f"error     : {res.get('error') or 'unknown'}")
        if res.get("traceback"):
            typer.echo(res["traceback"])
    receipt = res.get("receipt") or {}
    if receipt:
        console.print(
            f"price     : {_fmt_anm(receipt.get('grossNanm'))}"
            + ("  [dim](free tier)[/dim]" if receipt.get("freeTier") else "")
        )
        if receipt.get("developerNanm") not in (None, "0"):
            console.print(f"developer : {_fmt_anm(receipt.get('developerNanm'))}")
    for line in res.get("logs") or []:
        console.print(f"  [dim]{line.get('level', 'info')}[/dim] {line.get('message', '')}")
    if res.get("stdout"):
        console.print("[dim]--- stdout ---[/dim]")
        typer.echo(res["stdout"])
    raise typer.Exit(code=0 if status == "succeeded" else 1)


# ---------------------------------------------------------------------------
# logs / status / listings
# ---------------------------------------------------------------------------


@app.command()
def logs(
    function: Optional[str] = typer.Argument(None, help="Function slug (omit for all your executions)"),
    request_id: Optional[str] = typer.Option(None, "--request", help="Show one execution's log lines"),
    limit: int = typer.Option(20, "--limit", help="How many executions to list"),
    url: Optional[str] = typer.Option(None, "--url", help="Cloud base URL override"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Recent executions for a function, or (--request) one execution's logs."""
    with _api_errors():
        client = _client(url=url)

        if request_id:
            execution = client.get_execution(request_id)
            lines = client.get_logs(request_id)
            if json_output:
                typer.echo(json.dumps({"execution": execution, "logs": lines}, indent=2, default=str))
                return
            console.print(
                f"{request_id}  {execution.get('status')}  {execution.get('durationMs', 0)} ms  "
                f"{_fmt_anm(execution.get('priceNanm'))}"
            )
            if execution.get("error"):
                console.print(f"[red]error:[/red] {execution['error']}")
            for line in lines:
                console.print(f"  [dim]{line.get('ts', '')}[/dim] {line.get('level', 'info'):<5} {line.get('message', '')}")
            return

        function_id = None
        if function:
            function_id = _resolve_own_function(client, function)["id"]
        executions = client.list_executions(function_id=function_id, limit=limit)
        if json_output:
            typer.echo(json.dumps(executions, indent=2, default=str))
            return
        if not executions:
            console.print("no executions yet")
            return
        from rich.table import Table

        table = Table(box=None)
        for col in ("requestId", "status", "duration", "price", "when"):
            table.add_column(col)
        for e in executions:
            table.add_row(
                str(e.get("requestId", "")),
                str(e.get("status", "")),
                f"{e.get('durationMs', 0)} ms",
                _fmt_anm(e.get("priceNanm")),
                str(e.get("createdAt", "")),
            )
        console.print(table)
        console.print("[dim]per-execution logs: animica cloud logs --request <requestId>[/dim]")


@app.command()
def status(
    function: Optional[str] = typer.Argument(None, help="Function slug (omit for an account overview)"),
    url: Optional[str] = typer.Option(None, "--url", help="Cloud base URL override"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Account overview, or one function's deployment + anchor state."""
    with _api_errors():
        client = _client(url=url)

        if function:
            fn = _resolve_own_function(client, function)
            deployments = client.list_deployments(fn["id"], limit=1)
            latest = deployments[0] if deployments else None
            if json_output:
                typer.echo(json.dumps({"function": fn, "latestDeployment": latest}, indent=2, default=str))
                return
            console.print(f"function  : {fn.get('slug')}  (id {fn.get('id')})")
            console.print(f"status    : {fn.get('status')}  version {fn.get('currentVersion')}")
            console.print(f"limits    : {fn.get('memoryMb')} MB, {fn.get('timeoutMs')} ms")
            caps = fn.get("capabilities") or []
            console.print(f"capabilities: {', '.join(caps) if caps else 'none'}")
            console.print(f"executions: {fn.get('execCount', 0)}   revenue: {_fmt_anm(fn.get('revenueNanm'))}")
            if latest:
                console.print(f"deployment: {latest.get('status')}")
                if latest.get("endpoint"):
                    console.print(f"endpoint  : {latest['endpoint']}")
                if latest.get("anchorTxid"):
                    console.print(
                        f"anchor    : {latest['anchorTxid']} at height {latest.get('anchorHeight')} "
                        f"({latest.get('anchorConfirms', 0)} confirms) — on-chain anchor, off-chain execution"
                    )
                else:
                    console.print("anchor    : none")
            return

        me = client.me()
        functions = client.list_functions()
        if json_output:
            typer.echo(json.dumps({"me": me, "functions": functions}, indent=2, default=str))
            return
        who = me.get("address") or me.get("accountId") or me.get("id")
        console.print(f"account   : {who}")
        if me.get("plan") or me.get("planKey"):
            console.print(f"plan      : {me.get('plan') or me.get('planKey')}")
        console.print(f"functions : {len(functions)}")
        active = [f for f in functions if f.get("status") == "PUBLISHED"]
        if active:
            console.print(f"published : {', '.join(str(f.get('slug')) for f in active)}")


@app.command()
def functions(
    url: Optional[str] = typer.Option(None, "--url", help="Cloud base URL override"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """List your deployed functions."""
    with _api_errors():
        rows = _client(url=url).list_functions()
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        console.print("no functions yet — start with `animica cloud init`")
        return
    from rich.table import Table

    table = Table(box=None)
    for col in ("slug", "status", "version", "memory", "timeout", "executions", "revenue"):
        table.add_column(col)
    for f in rows:
        table.add_row(
            str(f.get("slug", "")),
            str(f.get("status", "")),
            str(f.get("currentVersion", 0)),
            f"{f.get('memoryMb', '')} MB",
            f"{f.get('timeoutMs', '')} ms",
            str(f.get("execCount", 0)),
            _fmt_anm(f.get("revenueNanm")),
        )
    console.print(table)


@app.command()
def apps(
    url: Optional[str] = typer.Option(None, "--url", help="Cloud base URL override"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """List your marketplace apps."""
    with _api_errors():
        rows = _client(url=url).list_apps()
    if json_output:
        typer.echo(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        console.print("no apps yet")
        return
    from rich.table import Table

    table = Table(box=None)
    for col in ("slug", "name", "status", "installs", "executions", "revenue"):
        table.add_column(col)
    for a in rows:
        table.add_row(
            str(a.get("slug", "")),
            str(a.get("name", "")),
            str(a.get("status", "")),
            str(a.get("installCount", 0)),
            str(a.get("execCount", 0)),
            _fmt_anm(a.get("revenueNanm")),
        )
    console.print(table)


@app.command()
def earnings(
    url: Optional[str] = typer.Option(None, "--url", help="Cloud base URL override"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Your developer earnings, as settled by the platform ledger."""
    with _api_errors():
        data = _client(url=url).earnings()
    if json_output:
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    if not isinstance(data, dict):
        typer.echo(json.dumps(data, indent=2, default=str))
        return
    printed = False
    for key, value in data.items():
        if key.endswith("Nanm"):
            console.print(f"{key[:-4]:<16}: {_fmt_anm(value)}")
            printed = True
        elif isinstance(value, (str, int, float, bool)):
            console.print(f"{key:<16}: {value}")
            printed = True
    for key in ("byFunction", "byApp"):
        rows = data.get(key)
        if isinstance(rows, list) and rows:
            from rich.table import Table

            table = Table(title=key, box=None)
            for col in ("slug", "executions", "earned"):
                table.add_column(col)
            for r in rows:
                table.add_row(
                    str(r.get("slug", r.get("name", ""))),
                    str(r.get("executions", r.get("execCount", 0))),
                    _fmt_anm(r.get("earnedNanm", r.get("developerNanm"))),
                )
            console.print(table)
            printed = True
    if not printed:
        typer.echo(json.dumps(data, indent=2, default=str))
