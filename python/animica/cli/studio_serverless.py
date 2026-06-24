"""`animica studio run|deploy|serve|fn` — the serverless-compute CLI.

These commands are attached to the existing ``animica studio`` Typer app by
``register(app)`` (called from cli/studio.py), so they live alongside the Studio
Services lifecycle commands (``up``/``down``/``status``/``logs``/``config``)
without disturbing them.

    animica studio run app.py::train 42 --gpu A100      # run once on the fleet
    animica studio run app.py::train --kw seed=42        # keyword form
    animica studio deploy app.py                          # register the app's functions
    animica studio serve app.py                           # dev loop (schedules + reload)
    animica studio fn list                                # what's deployed
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, List, Optional

import typer


def _coerce(token: str) -> Any:
    """Turn a CLI token into a Python value: int, float, json, or str."""
    for cast in (int, float):
        try:
            return cast(token)
        except ValueError:
            pass
    try:
        return json.loads(token)
    except (ValueError, TypeError):
        return token


def _load_module(path_part: str):
    """Import a module from a file path or dotted module name."""
    if path_part.endswith(".py") or "/" in path_part or os.path.sep in path_part:
        file = Path(path_part).resolve()
        if not file.exists():
            raise typer.BadParameter(f"file not found: {file}")
        # Make the file's dir importable so its sibling imports resolve.
        sys.path.insert(0, str(file.parent))
        spec = importlib.util.spec_from_file_location(file.stem, file)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[file.stem] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        return mod
    return importlib.import_module(path_part)


def _resolve_target(target: str):
    """Resolve ``file.py::func`` or ``module:func`` to a Studio Function."""
    if "::" in target:
        path_part, _, fn_name = target.partition("::")
    elif ":" in target and not target.endswith(".py"):
        path_part, _, fn_name = target.partition(":")
    else:
        path_part, fn_name = target, None
    mod = _load_module(path_part)

    if fn_name:
        obj = getattr(mod, fn_name, None)
        if obj is None:
            raise typer.BadParameter(f"{fn_name!r} not found in {path_part}")
        return obj, mod
    # No function named: find the single Studio function in the module.
    from animica.studio.app import Function
    fns = [v for v in vars(mod).values() if isinstance(v, Function)]
    if len(fns) == 1:
        return fns[0], mod
    raise typer.BadParameter(
        f"specify a function: {path_part}::<name> (found {len(fns)} functions)"
    )


def _find_app(mod):
    from animica.studio.app import App
    apps = [v for v in vars(mod).values() if isinstance(v, App)]
    if not apps:
        raise typer.BadParameter("no studio.App found in module")
    return apps[0]


def register(app: "typer.Typer") -> None:
    """Attach the serverless commands to the given Studio Typer app."""

    fn_app = typer.Typer(help="Inspect deployed Studio functions (aicf.fn.*).")

    @app.command("run")
    def run(
        target: str = typer.Argument(..., help="file.py::func or module:func"),
        args: Optional[List[str]] = typer.Argument(None, help="positional args"),
        kw: List[str] = typer.Option([], "--kw", help="keyword arg key=value (repeatable)"),
        mode: Optional[str] = typer.Option(None, "--mode", help="local|remote|auto"),
        gpu: Optional[str] = typer.Option(None, "--gpu", help="override GPU, e.g. A100"),
        as_json: bool = typer.Option(False, "--json", help="print the result as JSON"),
    ):
        """Run a Studio function once and print its result."""
        from animica.studio.app import Function

        fn, _mod = _resolve_target(target)
        if not isinstance(fn, Function):
            raise typer.BadParameter(f"{target} is not a @app.function")
        if mode:
            os.environ["ANIMICA_STUDIO_MODE"] = mode
            fn.app.config.mode = mode
        if gpu:
            fn.gpu = gpu

        pos = [_coerce(a) for a in (args or [])]
        kwargs = {}
        for item in kw:
            if "=" not in item:
                raise typer.BadParameter(f"--kw expects key=value, got {item!r}")
            k, _, v = item.partition("=")
            kwargs[k] = _coerce(v)

        typer.secho(f"→ running {fn.name} (mode={fn.app.config.mode})", fg=typer.colors.CYAN, err=True)
        result = fn.remote(*pos, **kwargs)
        if as_json:
            typer.echo(json.dumps(result, default=str))
        else:
            typer.echo(repr(result))

    @app.command("deploy")
    def deploy(
        target: str = typer.Argument(..., help="file.py containing a studio.App"),
    ):
        """Register an app's functions with the aicf.fn.* registry."""
        mod = _load_module(target)
        studio_app = _find_app(mod)
        typer.secho(f"→ deploying app {studio_app.name!r} ({len(studio_app.functions)} functions)",
                    fg=typer.colors.CYAN, err=True)
        res = studio_app.deploy()
        typer.echo(json.dumps(res, indent=2, default=str))

    @app.command("serve")
    def serve(
        target: str = typer.Argument(..., help="file.py containing a studio.App"),
    ):
        """Dev loop: keep the app's functions live and tick any schedules."""
        import time

        mod = _load_module(target)
        studio_app = _find_app(mod)
        scheduled = [f for f in studio_app.functions.values() if f.schedule]
        typer.secho(
            f"serving {studio_app.name!r}: {len(studio_app.functions)} functions, "
            f"{len(scheduled)} scheduled. Ctrl-C to stop.",
            fg=typer.colors.GREEN,
        )
        for f in studio_app.functions.values():
            sched = f" [{f.schedule.to_spec()}]" if f.schedule else ""
            typer.echo(f"  • {f.name}  image={f.image.ref}{sched}")
        if not scheduled:
            typer.secho("no scheduled functions; nothing to tick (this is a no-op loop).",
                        fg=typer.colors.YELLOW)
        try:
            while True:
                time.sleep(2.0)
        except KeyboardInterrupt:
            typer.echo("\nstopped.")

    @fn_app.command("list")
    def fn_list():
        """List functions registered with the broker (aicf.fn.list)."""
        from animica.studio.client import BrokerClient
        from animica.studio.config import StudioConfig

        cfg = StudioConfig.from_env()
        client = BrokerClient(cfg.rpc_url, timeout=10.0)
        try:
            items = client.fn_list()
            typer.echo(json.dumps(items, indent=2, default=str))
        except Exception as exc:  # noqa: BLE001
            typer.secho(f"registry unavailable ({exc}). Is a node running at {cfg.rpc_url}?",
                        fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(1)
        finally:
            client.close()

    @fn_app.command("get")
    def fn_get(name: str = typer.Argument(...)):
        """Show a deployed function's metadata (aicf.fn.get)."""
        from animica.studio.client import BrokerClient
        from animica.studio.config import StudioConfig

        cfg = StudioConfig.from_env()
        client = BrokerClient(cfg.rpc_url, timeout=10.0)
        try:
            typer.echo(json.dumps(client.fn_get(name), indent=2, default=str))
        finally:
            client.close()

    @app.command("worker")
    def worker(
        address: Optional[str] = typer.Option(None, "--address", help="ANM payout address (anim1...)"),
        tiers: str = typer.Option("function_compute", "--tiers", help="comma-separated job classes to serve"),
        poll: float = typer.Option(2.0, "--poll", help="claim poll interval (seconds)"),
        gpu: bool = typer.Option(False, "--gpu", help="advertise GPU capability"),
    ):
        """Serve Studio function_compute jobs from the fleet (the provider side).

        Polls the AICF broker, runs each claimed function in a local sandbox, and
        submits the result — earning ANM. `animica up` launches this automatically
        alongside mining and training.
        """
        from animica.studio.config import StudioConfig
        from animica.studio.worker import StudioWorker

        cfg = StudioConfig.from_env()
        addr = (
            address
            or os.environ.get("ANIMICA_PAYOUT_ADDRESS")
            or os.environ.get("ANIMICA_MINER_ADDRESS")
            or cfg.wallet
        )
        if not addr:
            raise typer.BadParameter(
                "no payout address: pass --address or set ANIMICA_PAYOUT_ADDRESS"
            )
        w = StudioWorker(
            cfg, address=addr,
            tiers=[t.strip() for t in tiers.split(",") if t.strip()],
            poll_interval=poll, gpu=gpu,
        )
        try:
            w.run_forever()
        except KeyboardInterrupt:
            typer.echo("\nstudio worker stopped.")

    app.add_typer(fn_app, name="fn")
