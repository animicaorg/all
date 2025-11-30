from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


class Exit(SystemExit):
    def __init__(self, code: int = 0) -> None:
        super().__init__(code)
        self.exit_code = code


class BadParameter(Exception):
    pass


class OptionInfo:
    def __init__(self, default: Any, envvar: Optional[str] = None) -> None:
        self.default = default
        self.envvar = envvar


def Option(default: Any = None, *_, envvar: Optional[str] = None, **__) -> OptionInfo:  # type: ignore[override]
    return OptionInfo(default, envvar=envvar)


def echo(message: str, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    stream.write(str(message) + "\n")


class Context:
    def __init__(self) -> None:
        self.obj: Dict[str, Any] = {}


_context_stack: List[Context] = []


def get_current_context(silent: bool = False) -> Optional[Context]:
    if not _context_stack:
        if silent:
            return None
        raise RuntimeError("No active Typer context")
    return _context_stack[-1]


def get_app_dir(_: str) -> str:
    return str(Path.home())


class Typer:
    def __init__(self, *, help: str | None = None) -> None:  # noqa: A002
        self.help = help or ""
        self._commands: Dict[str, Callable[..., Any]] = {}
        self._callback: Optional[Callable[..., Any]] = None

    def command(self, name: Optional[str] = None):
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            cmd_name = name or func.__name__
            self._commands[cmd_name] = func
            return func

        return decorator

    def callback(self):
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._callback = func
            return func

        return decorator

    def _parse_options(
        self, func: Callable[..., Any], args: List[str], ctx: Optional[Context] = None
    ) -> tuple[Dict[str, Any], List[str]]:
        sig = inspect.signature(func)
        params = list(sig.parameters.values())
        idx_map = {p.name.replace("-", "_"): p for p in params}
        remaining = []
        provided: Dict[str, str] = {}
        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith("--"):
                key = arg[2:].replace("-", "_")
                if key not in idx_map:
                    raise BadParameter(f"Unknown option {arg}")
                param = idx_map[key]
                default = param.default
                default_val = (
                    default.default if isinstance(default, OptionInfo) else default
                )
                next_is_value = i + 1 < len(args) and not args[i + 1].startswith("--")
                if next_is_value:
                    provided[key] = args[i + 1]
                    i += 2
                elif isinstance(default_val, bool):
                    provided[key] = "true"
                    i += 1
                else:
                    raise BadParameter(f"Missing value for {arg}")
            else:
                remaining.append(arg)
                i += 1
        values: Dict[str, Any] = {}
        for position, param in enumerate(params):
            if (
                ctx is not None
                and position == 0
                and (
                    param.annotation == Context
                    or str(param.annotation) in {"Context", "typer.Context"}
                )
            ):
                values[param.name] = ctx
                continue
            raw = provided.get(param.name)
            default = param.default
            if isinstance(default, OptionInfo):
                if raw is None and default.envvar:
                    raw = os.environ.get(default.envvar)
                default_val = default.default
            else:
                default_val = default
            if raw is None:
                if default is inspect._empty:
                    raise BadParameter(f"Missing required option --{param.name}")
                values[param.name] = default_val
                continue
            if param.annotation is int:
                values[param.name] = int(raw)
            elif param.annotation is Path or str(param.annotation) in {
                "Path",
                "pathlib.Path",
            }:
                values[param.name] = Path(raw)
            elif isinstance(default_val, bool):
                values[param.name] = str(raw).lower() not in {"", "0", "false", "none"}
            else:
                values[param.name] = raw
        return values, remaining

    def __call__(self, args: Optional[List[str]] = None) -> None:
        argv = list(args) if args is not None else sys.argv[1:]
        ctx = Context()
        _context_stack.append(ctx)
        try:
            if self._callback:
                cb_args: List[str] = []
                remaining: List[str] = []
                i = 0
                while i < len(argv):
                    if argv[i].startswith("--"):
                        cb_args.append(argv[i])
                        if i + 1 < len(argv):
                            cb_args.append(argv[i + 1])
                            i += 2
                        else:
                            i += 1
                    else:
                        remaining = argv[i:]
                        break
                cb_kwargs, _ = self._parse_options(self._callback, cb_args, ctx)
                self._callback(**cb_kwargs)
                argv = remaining
            if not argv:
                if self.help:
                    echo(self.help)
                return
            cmd_name = argv[0]
            cmd = self._commands.get(cmd_name)
            if cmd is None:
                raise BadParameter(f"Unknown command {cmd_name}")
            cmd_kwargs, _ = self._parse_options(cmd, argv[1:])
            result = cmd(**cmd_kwargs)
            if inspect.iscoroutine(result):
                asyncio.run(result)
        except Exit as exc:  # pragma: no cover - handled by runner
            raise exc
        finally:
            _context_stack.pop()


__all__ = [
    "Typer",
    "Option",
    "Context",
    "Exit",
    "BadParameter",
    "echo",
    "get_current_context",
    "get_app_dir",
]
