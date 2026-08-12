"""Animica Python Cloud — in-container execution runner.

This file runs INSIDE the sandbox container, as an unprivileged user, with no network and a
read-only root filesystem. It is deliberately self-contained: it imports nothing from Animica,
because the sandbox image must not contain Animica code, credentials or wallet material.

Its job:
  1. take over the real stdout/stderr so USER CODE CANNOT FORGE PROTOCOL MESSAGES,
  2. load the user's module and resolve the entrypoint,
  3. expose a capability-mediated `animica` host API (every privileged operation is an RPC to
     the host broker, which is the only thing that can actually perform it),
  4. run the function under a wall-clock alarm and hard rlimits,
  5. emit exactly one RESULT or ERROR frame.

PROTOCOL (line-oriented JSON over a private fd pair, never fd 0/1/2 once we are set up):
    host -> runner   one BOOT line, then one REPLY line per host call
    runner -> host   CALL / LOG frames, then exactly one RESULT or ERROR frame

Why the fd juggling matters: if user code could write to the protocol stream it could forge a
reply to its own host call (e.g. "yes, the payment succeeded"). So before any user code is
imported we move the protocol to private descriptors and point fd 0/1/2 at a null device and a
capture file. From user code's perspective print() works normally and goes to its logs.
"""

from __future__ import annotations

import base64
import builtins
import io
import json
import os
import resource
import signal
import sys
import time
import traceback

PROTO_VERSION = 1

# --- take the protocol private BEFORE anything else can touch fd 0/1/2 ---------------------

_PROTO_OUT = os.dup(1)
_PROTO_IN = os.dup(0)

_CAPTURE_PATH = os.environ.get("ANM_CAPTURE_PATH", "/tmp/.anm-stdout")
_capture = open(_CAPTURE_PATH, "w+b", buffering=0)
os.dup2(_capture.fileno(), 1)
os.dup2(_capture.fileno(), 2)
_devnull = open(os.devnull, "rb")
os.dup2(_devnull.fileno(), 0)

sys.stdout = io.TextIOWrapper(open(1, "wb", buffering=0), encoding="utf-8", errors="replace", write_through=True)
sys.stderr = io.TextIOWrapper(open(2, "wb", buffering=0), encoding="utf-8", errors="replace", write_through=True)
sys.stdin = io.StringIO("")

_proto_w = open(_PROTO_OUT, "w", encoding="utf-8", buffering=1)
_proto_r = open(_PROTO_IN, "r", encoding="utf-8")

_TOKEN = os.environ.get("ANM_PROTO_TOKEN", "")


def _send(kind: str, payload: dict) -> None:
    _proto_w.write(f"@@ANM:{kind}:{_TOKEN}@@ " + json.dumps(payload, default=str) + "\n")
    _proto_w.flush()


def _recv() -> dict:
    line = _proto_r.readline()
    if not line:
        raise HostUnavailable("host closed the control channel")
    try:
        return json.loads(line)
    except ValueError as exc:  # pragma: no cover - host always sends valid JSON
        raise HostUnavailable(f"bad host frame: {exc}") from exc


# --- errors ---------------------------------------------------------------------------------


class AnimicaError(Exception):
    """Base class for errors raised by the Animica host API."""


class CapabilityDenied(AnimicaError):
    """The function requested something its deployment is not authorized to do."""


class BudgetExceeded(AnimicaError):
    """The call would exceed the caller's authorized spend or resource budget."""


class HostUnavailable(AnimicaError):
    """The host broker went away mid-execution."""


class Timeout(AnimicaError):
    """The function exceeded its wall-clock budget."""


# --- the host-call bridge -------------------------------------------------------------------

_call_seq = 0


def _host_call(op: str, args: dict) -> object:
    """Ask the host broker to perform a privileged operation.

    The host is the authority: it checks the capability grant, the spend budget, the call
    depth and the remaining resource allowance, performs the operation, meters it, and
    replies. The runner never has credentials, network or keys of its own.
    """
    global _call_seq
    _call_seq += 1
    _send("CALL", {"id": _call_seq, "op": op, "args": args})
    reply = _recv()
    if reply.get("id") != _call_seq:
        raise HostUnavailable("host reply out of order")
    if reply.get("ok"):
        return reply.get("result")
    code = reply.get("code") or "host_error"
    message = reply.get("error") or "host call failed"
    if code == "capability_denied":
        raise CapabilityDenied(message)
    if code in ("budget_exceeded", "insufficient_funds", "depth_exceeded", "quota_exceeded"):
        raise BudgetExceeded(message)
    raise AnimicaError(message)


# --- the `animica` module handed to user code ------------------------------------------------


class _AI:
    """Animica AI inference. Metered per token and billed to the execution."""

    def infer(self, prompt=None, *, messages=None, model=None, max_tokens=None, temperature=None) -> str:
        if messages is None:
            if prompt is None:
                raise ValueError("infer() needs prompt= or messages=")
            messages = [{"role": "user", "content": str(prompt)}]
        res = _host_call(
            "ai.infer",
            {
                "messages": messages,
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        return res.get("text", "") if isinstance(res, dict) else str(res)

    # Convenience alias matching the OpenAI-ish shape people expect.
    def chat(self, messages, *, model=None, max_tokens=None, temperature=None) -> str:
        return self.infer(messages=messages, model=model, max_tokens=max_tokens, temperature=temperature)


class _Chain:
    """Read-only Animica chain access."""

    def head(self) -> dict:
        return _host_call("chain.head", {})

    def balance(self, address: str) -> int:
        res = _host_call("chain.balance", {"address": address})
        return int(res.get("nanm", 0)) if isinstance(res, dict) else int(res)


class _Wallet:
    """Capability-gated ANM spending.

    User code can never touch a private key. It asks the host to pay an approved recipient an
    amount inside the authorized budget; the host verifies permission, per-call/per-execution/
    daily caps and the recipient allowlist before anything moves.
    """

    def pay(self, to: str, amount_nanm: int, memo: str = "") -> dict:
        return _host_call("wallet.pay", {"to": to, "amount_nanm": int(amount_nanm), "memo": str(memo)[:200]})

    def balance(self) -> int:
        res = _host_call("wallet.balance", {})
        return int(res.get("nanm", 0)) if isinstance(res, dict) else int(res)


class _State:
    """Small per-function persistent key/value store."""

    def get(self, key: str, default=None):
        res = _host_call("state.get", {"key": str(key)})
        if isinstance(res, dict) and "value" in res:
            return res["value"] if res["value"] is not None else default
        return default

    def set(self, key: str, value) -> None:
        _host_call("state.set", {"key": str(key), "value": value})

    def delete(self, key: str) -> None:
        _host_call("state.delete", {"key": str(key)})


class _Http:
    """Mediated outbound HTTP. The host performs the request (the sandbox has no network) and
    enforces the SSRF allowlist, size caps and timeouts."""

    def fetch(self, url: str, *, method: str = "GET", headers=None, body=None, timeout: int = 10) -> dict:
        return _host_call(
            "http.fetch",
            {"url": url, "method": method, "headers": headers or {}, "body": body, "timeout": int(timeout)},
        )


class _Animica:
    def __init__(self) -> None:
        self.ai = _AI()
        self.chain = _Chain()
        self.wallet = _Wallet()
        self.state = _State()
        self.http = _Http()
        self.context: dict = {}

    def call(self, target: str, payload=None, *, timeout=None):
        """Call another deployed Animica function or app (agent-to-agent economy).

        The host enforces max call depth, the per-execution call budget and the caller's
        downstream spend authorization, and records the nested call in the execution trace.
        """
        return _host_call("call", {"target": target, "payload": payload, "timeout": timeout})

    def log(self, *args, level: str = "info") -> None:
        msg = " ".join(str(a) for a in args)
        _send("LOG", {"level": level, "message": msg[:4000]})

    def secret(self, name: str, default=None):
        """Read a secret injected into THIS execution. Never leaves the sandbox."""
        return os.environ.get(f"ANM_SECRET_{name}", default)


animica = _Animica()

# Expose it as an importable module so `import animica` works inside user code.
_mod = type(sys)("animica")
for _name in ("ai", "chain", "wallet", "state", "http", "context"):
    setattr(_mod, _name, getattr(animica, _name))
_mod.call = animica.call
_mod.log = animica.log
_mod.secret = animica.secret
_mod.AnimicaError = AnimicaError
_mod.CapabilityDenied = CapabilityDenied
_mod.BudgetExceeded = BudgetExceeded
sys.modules["animica"] = _mod


# --- request context object -------------------------------------------------------------------


class Context:
    """Second argument passed to handlers that accept one."""

    def __init__(self, meta: dict) -> None:
        self.request_id = meta.get("request_id", "")
        self.function = meta.get("function", "")
        self.version = meta.get("version", 0)
        self.owner = meta.get("owner", "")
        self.caller = meta.get("caller")
        self.deadline_ms = meta.get("deadline_ms", 0)
        self.ai = animica.ai
        self.chain = animica.chain
        self.wallet = animica.wallet
        self.state = animica.state
        self.http = animica.http

    def call(self, target: str, payload=None, **kw):
        return animica.call(target, payload, **kw)

    def log(self, *a, **kw):
        return animica.log(*a, **kw)

    def secret(self, name, default=None):
        return animica.secret(name, default)


# --- limits ------------------------------------------------------------------------------------


def _apply_rlimits(cfg: dict) -> None:
    """Defense in depth. The container already caps memory/pids/CPU via cgroups; these rlimits
    make a runaway function fail fast and predictably instead of being OOM-killed."""
    mem_bytes = int(cfg.get("memory_mb", 256)) * 1024 * 1024
    wall_s = max(1, int(cfg.get("timeout_ms", 30000) / 1000))
    try:
        # Address space a little above the cgroup limit so the cgroup is what actually bites.
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes * 2, mem_bytes * 2))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (wall_s + 2, wall_s + 3))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (int(cfg.get("max_pids", 64)), int(cfg.get("max_pids", 64))))
    except (ValueError, OSError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        pass


def _on_alarm(signum, frame):  # noqa: ARG001
    raise Timeout("function exceeded its time budget")


def _drain_capture(max_bytes: int) -> str:
    try:
        _capture.flush()
        size = _capture.tell()
        start = max(0, size - max_bytes)
        _capture.seek(start)
        data = _capture.read()
        _capture.seek(size)
        return data.decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def _load_entrypoint(cfg: dict):
    code_dir = cfg.get("code_dir", "/app/code")
    module_name = cfg.get("module", "handler")
    entrypoint = cfg.get("entrypoint", "main")
    if code_dir not in sys.path:
        sys.path.insert(0, code_dir)
    mod = __import__(module_name)
    fn = getattr(mod, entrypoint, None)
    if fn is None:
        raise AttributeError(f"entrypoint '{entrypoint}' not found in {module_name}.py")
    # Unwrap an @app.function-style decorator if the developer used the SDK.
    if hasattr(fn, "__animica_function__"):
        fn = getattr(fn, "fn", fn)
    if not callable(fn):
        raise TypeError(f"entrypoint '{entrypoint}' is not callable")
    return fn


def _arity(fn) -> int:
    try:
        import inspect

        sig = inspect.signature(fn)
        required = 0
        for p in sig.parameters.values():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                continue
            required += 1
        return required
    except Exception:  # noqa: BLE001
        return 1


def main() -> int:
    boot = _recv()
    cfg = boot.get("config") or {}
    request = boot.get("request")
    meta = boot.get("meta") or {}
    animica.context = dict(meta)
    _mod.context = animica.context

    # Defense in depth: drop the protocol token from the environment now that we have it, so
    # user code cannot read it out of os.environ. This is hardening, not a security boundary —
    # frames are authorized host-side from server-held context, so a function that managed to
    # write its own frames could only choose its own return value, which it can do anyway.
    os.environ.pop("ANM_PROTO_TOKEN", None)

    max_output = int(cfg.get("max_output_bytes", 1024 * 1024))
    started = time.time()

    _apply_rlimits(cfg)

    timeout_ms = int(cfg.get("timeout_ms", 30000))
    signal.signal(signal.SIGALRM, _on_alarm)
    # Ceiling of seconds, minimum 1 — the host also kills the container independently.
    signal.alarm(max(1, (timeout_ms + 999) // 1000))

    status = "ok"
    payload: dict = {}
    try:
        fn = _load_entrypoint(cfg)
        ctx = Context(meta)
        result = fn(request, ctx) if _arity(fn) >= 2 else fn(request)
        # Result must be JSON-serializable — say so clearly rather than failing opaquely later.
        try:
            json.dumps(result, default=str)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"return value is not JSON-serializable: {exc}") from exc
        payload = {"result": result}
    except Timeout as exc:
        status = "timeout"
        payload = {"error": str(exc), "type": "Timeout"}
    except BaseException as exc:  # noqa: BLE001 - user code may raise anything, incl. SystemExit
        status = "error"
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        # Trim our own frames so the developer sees their file, not the runner's plumbing.
        payload = {
            "error": str(exc) or exc.__class__.__name__,
            "type": exc.__class__.__name__,
            "traceback": tb[-8000:],
        }
    finally:
        signal.alarm(0)

    usage = resource.getrusage(resource.RUSAGE_SELF)
    frame = {
        "version": PROTO_VERSION,
        "status": status,
        **payload,
        "stdout": _drain_capture(max_output),
        "usage": {
            "wall_ms": int((time.time() - started) * 1000),
            "cpu_ms": int((usage.ru_utime + usage.ru_stime) * 1000),
            "max_rss_kb": int(usage.ru_maxrss),
        },
    }
    body = base64.b64encode(json.dumps(frame, default=str).encode("utf-8")).decode("ascii")
    _send("RESULT", {"b64": body})
    return 0


if __name__ == "__main__":
    try:
        _rc = main()
    except HostUnavailable:
        os._exit(3)
    except BaseException:  # noqa: BLE001 - never let a crash look like a clean exit
        try:
            _send("ERROR", {"error": traceback.format_exc()[-4000:]})
        except Exception:  # noqa: BLE001
            pass
        os._exit(4)
    # os._exit rather than sys.exit: user code may have left non-daemon threads or atexit
    # handlers behind, and the result frame is already delivered. Nothing after this point may
    # run guest code.
    os._exit(_rc)
