"""animica-serve — a torch-free AICF inference worker for phones and small boxes.

The whole worker is this one stdlib-only module. Inference is delegated to
llama.cpp, in one of three ways (first that works wins):

  1. An OpenAI-compatible endpoint you already run (``--openai-url``, e.g. a
     llama-server you started yourself, or Ollama's ``http://127.0.0.1:11434/v1``).
  2. A ``llama-server`` binary on PATH — spawned and managed for you
     (Termux: ``pkg install llama-cpp``).
  3. ``llama-cpp-python`` imported in-process (``pip install animica-serve[python-backend]``).

Protocol (JSON-RPC 2.0 against https://rpc.animica.org/rpc — the same loop the
pool.animica.org/serve browser worker runs, live-verified 2026-08-23):

    aicf.workerRegister     {address, tiers, hardware}
    aicf.workerClaimNextJob {address, tiers} -> null | {job_id, prompt,
                             max_output_tokens, temperature, top_p, claim_expires_at}
    aicf.workerSubmitResult {address, job_id, text}
    aicf.workerEarnings     {address}

Jobs are K-way raced server-side: the first good answer wins and is credited its
full estimated cost in ANM on the worker ledger. Losing a race to a faster
desktop GPU is normal; you win whenever you are the fastest (or only) worker.
No keys ever touch this program — the address is only where earnings credit.

Termux quickstart:
    pkg install python llama-cpp
    pip install animica-serve
    animica-serve --address anim1yourwallet
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import random
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

__version__ = "0.1.1"

DEFAULT_RPC = "https://rpc.animica.org/rpc"
TIERS = ["free", "standard"]
MAX_OUTPUT_CAP = 768
PROMPT_CLAMP_CHARS = 13000        # real flattened prompts measure ~10 KB; ctx is 4k tokens
CLAIM_MIN_S, CLAIM_MAX_S = 2.5, 15.0
REGISTER_EVERY_S = 300.0
EARNINGS_EVERY_S = 45.0
BATTERY_EVERY_S = 30.0
CTX_TOKENS = 4096

MODELS: Dict[str, Dict[str, Any]] = {
    "qwen2.5-1.5b": {
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "approx_gb": 1.1,
        "note": "default — best answers, needs ~2 GB free RAM",
    },
    "qwen2.5-0.5b": {
        "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "file": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "approx_gb": 0.5,
        "note": "light — for older / low-RAM phones",
    },
}
DEFAULT_MODEL = "qwen2.5-1.5b"


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ── JSON-RPC ─────────────────────────────────────────────────────────────────

def rpc(url: str, method: str, params: Dict[str, Any], timeout: float = 30.0) -> Any:
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) % 10**9,
        "method": method,
        "params": params,
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": f"animica-serve/{__version__}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 - operator-set https URL
        j = json.loads(r.read().decode())
    if j.get("error"):
        raise RuntimeError(f"{method}: {j['error'].get('message', 'rpc error')}")
    return j.get("result")


# ── Model download (resumable) ───────────────────────────────────────────────

def model_dir() -> Path:
    d = Path(os.environ.get("ANIMICA_SERVE_HOME", str(Path.home() / ".animica-serve"))) / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download(url: str, dest: Path, *, max_bytes: Optional[int] = None) -> bool:
    """Resumable download (HTTP Range). Returns True when the file is complete.
    ``max_bytes`` bounds THIS call's transfer (testing / metered connections)."""
    part = dest.with_suffix(dest.suffix + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"User-Agent": f"animica-serve/{__version__}"}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=60)  # nosec B310
    except urllib.error.HTTPError as e:
        if e.code == 416 and have:  # already fully downloaded
            part.rename(dest)
            return True
        raise
    total = None
    cr = resp.headers.get("Content-Range")
    if cr and "/" in cr:
        total = int(cr.split("/")[-1])
    elif resp.headers.get("Content-Length"):
        total = have + int(resp.headers["Content-Length"])
    if resp.status == 200 and have:
        # server ignored Range — start over
        have = 0
        part.unlink(missing_ok=True)
    moved = 0
    t0 = time.time()
    with open(part, "ab" if have else "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            moved += len(chunk)
            if total and (moved % (16 << 20) < (1 << 20)):
                done = have + moved
                mbs = moved / max(0.1, time.time() - t0) / 1e6
                print(f"\r  {done / 1e9:.2f} / {total / 1e9:.2f} GB  ({mbs:.1f} MB/s)   ",
                      end="", flush=True)
            if max_bytes is not None and moved >= max_bytes:
                resp.close()
                print()
                return False
    print()
    size = part.stat().st_size
    if total and size < total:
        return False
    part.rename(dest)
    return True


def resolve_model(spec: str) -> Path:
    """A registry key ('qwen2.5-1.5b'), a local .gguf path, or a direct URL."""
    p = Path(spec).expanduser()
    if p.suffix == ".gguf" and p.exists():
        return p
    if spec.startswith(("http://", "https://")):
        dest = model_dir() / spec.rsplit("/", 1)[-1]
        url = spec
    else:
        m = MODELS.get(spec)
        if not m:
            raise SystemExit(f"unknown model {spec!r} — one of {', '.join(MODELS)} or a .gguf path/URL")
        dest = model_dir() / m["file"]
        url = m["url"]
    if dest.exists():
        return dest
    log(f"downloading model → {dest}  (resumable; ctrl-C and rerun to continue)")
    while not download(url, dest):
        log("  …resuming")
    log("model ready")
    return dest


# ── Inference backends ───────────────────────────────────────────────────────

class Backend:
    name = "?"

    def generate(self, prompt: str, max_tokens: int, temperature: float, top_p: float,
                 timeout: float) -> str:
        raise NotImplementedError

    def close(self) -> None:
        pass


class OpenAIBackend(Backend):
    """Any OpenAI-compatible /chat/completions endpoint (llama-server, Ollama, …)."""

    def __init__(self, base_url: str, model: str = "local"):
        base = base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            self.url = base
        else:
            if not base.endswith("/v1"):
                base += "/v1"
            self.url = base + "/chat/completions"
        self.model = model
        self.name = f"openai:{self.url}"

    def generate(self, prompt: str, max_tokens: int, temperature: float, top_p: float,
                 timeout: float) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stream": False,
        }).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
            j = json.loads(r.read().decode())
        return str(((j.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


class SpawnedLlamaServer(OpenAIBackend):
    """Finds `llama-server` on PATH (Termux: pkg install llama-cpp), spawns it on a
    free localhost port with the GGUF model, waits for /health, and cleans up on exit."""

    def __init__(self, model_path: Path, threads: int):
        exe = shutil.which("llama-server")
        if not exe:
            raise FileNotFoundError("llama-server not on PATH")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cmd = [exe, "-m", str(model_path), "--host", "127.0.0.1", "--port", str(port),
               "-c", str(CTX_TOKENS), "-t", str(threads), "--no-webui"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        atexit.register(self.close)
        deadline = time.time() + 180
        health = f"http://127.0.0.1:{port}/health"
        log(f"starting llama-server (pid {self.proc.pid}) — loading the model…")
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"llama-server exited early (code {self.proc.returncode}) — "
                                   "run it by hand to see why, or use --openai-url")
            try:
                with urllib.request.urlopen(health, timeout=3) as r:  # nosec B310
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(1.0)
        else:
            self.close()
            raise RuntimeError("llama-server did not become healthy within 180s")
        super().__init__(f"http://127.0.0.1:{port}/v1")
        self.name = f"llama-server(pid {self.proc.pid})"

    def close(self) -> None:
        p = getattr(self, "proc", None)
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


class LlamaCppPython(Backend):
    """In-process llama-cpp-python (pip install animica-serve[python-backend])."""

    def __init__(self, model_path: Path, threads: int):
        from llama_cpp import Llama  # type: ignore
        self.llm = Llama(model_path=str(model_path), n_ctx=CTX_TOKENS,
                         n_threads=threads, verbose=False)
        self.name = "llama-cpp-python"

    def generate(self, prompt: str, max_tokens: int, temperature: float, top_p: float,
                 timeout: float) -> str:
        out = self.llm.create_chat_completion(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens, temperature=temperature, top_p=top_p,
        )
        return str(((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


def pick_backend(args) -> Backend:
    if args.openai_url:
        b = OpenAIBackend(args.openai_url, model=args.openai_model)
        log(f"backend: existing endpoint {b.url}")
        return b
    model_path = resolve_model(args.model)
    threads = args.threads or max(1, (os.cpu_count() or 4) - 1)
    if shutil.which("llama-server"):
        b = SpawnedLlamaServer(model_path, threads)
        log(f"backend: {b.name} · {model_path.name} · {threads} threads")
        return b
    try:
        b = LlamaCppPython(model_path, threads)
        log(f"backend: llama-cpp-python · {model_path.name} · {threads} threads")
        return b
    except ImportError:
        raise SystemExit(
            "No inference backend found. Install ONE of:\n"
            "  Termux :  pkg install llama-cpp          (recommended)\n"
            "  anywhere: pip install 'animica-serve[python-backend]'\n"
            "  or point --openai-url at a llama-server / Ollama you already run"
        )


# ── Battery gating (Termux) ──────────────────────────────────────────────────

def battery_status() -> Optional[Dict[str, Any]]:
    """Termux:API's termux-battery-status, when available. None elsewhere."""
    exe = shutil.which("termux-battery-status")
    if not exe:
        return None
    try:
        out = subprocess.run([exe], capture_output=True, timeout=10)
        return json.loads(out.stdout.decode() or "{}")
    except Exception:
        return None


def is_charging(st: Optional[Dict[str, Any]]) -> bool:
    if not st:
        return True
    return str(st.get("status", "")).upper() in ("CHARGING", "FULL") or \
        str(st.get("plugged", "")).upper().startswith("PLUGGED")


# ── Helpers shared with the browser worker ───────────────────────────────────

def clamp_prompt(p: str, max_chars: int = PROMPT_CLAMP_CHARS) -> str:
    """Keep the head (instructions) and the tail (recent history + the question)."""
    if len(p) <= max_chars:
        return p
    head = int(max_chars * 0.3)
    tail = max_chars - head
    return p[:head] + "\n…\n" + p[len(p) - tail:]


def hardware_info(backend_name: str, model: str) -> Dict[str, Any]:
    return {
        "engine": "animica-serve",
        "backend": backend_name[:80],
        "model": model[:80],
        "platform": f"{sys.platform}/{os.uname().machine if hasattr(os, 'uname') else '?'}",
        "cores": os.cpu_count() or 0,
        "termux": bool(os.environ.get("TERMUX_VERSION")),
        "version": __version__,
    }


# ── The serve loop ───────────────────────────────────────────────────────────

def serve(args) -> int:
    address = args.address.strip()
    if not address:
        raise SystemExit("--address is required (the anim1… wallet your earnings credit)")
    if not address.startswith("anim1"):
        log(f"WARNING: {address!r} does not look like an anim1… wallet address; "
            "earnings credit whatever ID you register — an unknown ID can never be paid out.")

    backend = pick_backend(args)
    hw = hardware_info(backend.name, args.model)
    stop = {"flag": False}

    def _sig(_s, _f):
        if stop["flag"]:
            raise KeyboardInterrupt
        stop["flag"] = True
        log("stopping after the current job… (ctrl-C again to force)")
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    rpc(args.rpc, "aicf.workerRegister", {"address": address, "tiers": TIERS, "hardware": hw})
    log(f"registered {address[:14]}… tiers={','.join(TIERS)} — waiting for jobs "
        f"(jobs race across workers; losses to faster GPUs are normal)")

    charge_gate = args.charge_only and battery_status() is not None
    if args.charge_only and not charge_gate:
        log("note: --charge-only needs the Termux:API app + `pkg install termux-api`; "
            "battery state not readable here, serving regardless")

    delay = CLAIM_MIN_S
    last_register = time.time()
    last_earnings = 0.0
    last_battery = 0.0
    charging = True
    won = lost = 0
    while not stop["flag"]:
        now = time.time()
        if charge_gate and now - last_battery > BATTERY_EVERY_S:
            last_battery = now
            was = charging
            charging = is_charging(battery_status())
            if was and not charging:
                log("paused — plug the phone in to keep serving")
            elif charging and not was:
                log("charging again — serving resumed")
        if charge_gate and not charging:
            time.sleep(2.0)
            continue
        if now - last_register > REGISTER_EVERY_S:
            last_register = now
            try:
                rpc(args.rpc, "aicf.workerRegister", {"address": address, "tiers": TIERS, "hardware": hw})
            except Exception:
                pass
        if now - last_earnings > EARNINGS_EVERY_S:
            last_earnings = now
            try:
                e = rpc(args.rpc, "aicf.workerEarnings", {"address": address})
                log(f"ledger: {e.get('jobs_completed', 0)} jobs · "
                    f"{e.get('earnings_pending_animica', 0):.6f} ANM pending "
                    f"(session: {won} won / {lost} lost)")
            except Exception:
                pass
        try:
            job = rpc(args.rpc, "aicf.workerClaimNextJob", {"address": address, "tiers": TIERS})
        except Exception as e:
            log(f"queue unreachable ({str(e)[:60]}) — retrying")
            delay = min(CLAIM_MAX_S, delay * 1.6)
            time.sleep(delay)
            continue
        if not job or not job.get("job_id"):
            delay = min(CLAIM_MAX_S, delay * 1.35)
            time.sleep(delay * (0.7 + random.random() * 0.6))
            continue
        delay = CLAIM_MIN_S
        prompt = clamp_prompt(str(job.get("prompt") or ""))
        if not prompt.strip():
            log(f"claimed {job['job_id'][:10]}… but it carried no prompt — skipped")
            continue
        max_tok = max(16, min(int(job.get("max_output_tokens") or 512), args.max_tokens))
        expires = float(job.get("claim_expires_at") or 0)
        budget = max(10.0, (expires - time.time()) - 4.0) if expires else 120.0
        log(f"claimed {job['job_id'][:10]}… tier={job.get('tier')} "
            f"({len(prompt)} chars in, ≤{max_tok} tokens out, {budget:.0f}s budget)")
        t0 = time.time()
        try:
            text = backend.generate(
                prompt, max_tok,
                max(0.0, min(float(job.get("temperature") or 0.3), 1.2)),
                max(0.05, min(float(job.get("top_p") or 0.9), 1.0)),
                timeout=min(budget, 300.0),
            )
        except Exception as e:
            log(f"generation failed: {str(e)[:90]}")
            continue
        dt = time.time() - t0
        if not text.strip():
            log(f"no text produced for {job['job_id'][:10]}… — nothing submitted")
            continue
        try:
            r = rpc(args.rpc, "aicf.workerSubmitResult",
                    {"address": address, "job_id": job["job_id"], "text": text[:32000]})
            if r and r.get("accepted") is not False:
                won += 1
                log(f"WON {job['job_id'][:10]}… · {len(text)} chars in {dt:.1f}s")
            else:
                lost += 1
                log(f"lost the race on {job['job_id'][:10]}… "
                    f"({(r or {}).get('reason', 'another worker was faster')})")
        except Exception as e:
            log(f"submit failed: {str(e)[:90]}")
    backend.close()
    log(f"stopped · session: {won} won / {lost} lost · pending earnings stay on {address[:14]}…")
    return 0


def show_earnings(args) -> int:
    e = rpc(args.rpc, "aicf.workerEarnings", {"address": args.address})
    print(json.dumps(e, indent=2))
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def cli(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="animica-serve",
        description="Serve Animica AICF inference jobs from anything with a CPU — "
                    "phones under Termux included. Earnings credit your anim1… address per job won.",
        epilog="Models: " + "; ".join(f"{k} ({v['note']})" for k, v in MODELS.items()) +
               ". Termux: pkg install python llama-cpp && pip install animica-serve",
    )
    ap.add_argument("--address", "-a", default=os.environ.get("ANIMICA_SERVE_ADDRESS", ""),
                    help="anim1… wallet that earnings credit (env ANIMICA_SERVE_ADDRESS)")
    ap.add_argument("--model", "-m", default=os.environ.get("ANIMICA_SERVE_MODEL", DEFAULT_MODEL),
                    help=f"registry key, .gguf path, or URL (default {DEFAULT_MODEL})")
    ap.add_argument("--openai-url", default=os.environ.get("ANIMICA_SERVE_OPENAI_URL", ""),
                    help="use an OpenAI-compatible endpoint you already run "
                         "(e.g. http://127.0.0.1:8080/v1 or Ollama's http://127.0.0.1:11434/v1)")
    ap.add_argument("--openai-model", default=os.environ.get("ANIMICA_SERVE_OPENAI_MODEL", "local"),
                    help="model name to send to --openai-url (Ollama needs the real tag)")
    ap.add_argument("--threads", "-t", type=int, default=0, help="inference threads (default: cores-1)")
    ap.add_argument("--max-tokens", type=int, default=MAX_OUTPUT_CAP,
                    help=f"hard output cap (default {MAX_OUTPUT_CAP})")
    ap.add_argument("--charge-only", action="store_true",
                    help="pause while unplugged (Termux:API required)")
    ap.add_argument("--rpc", default=os.environ.get("ANIMICA_SERVE_RPC", DEFAULT_RPC),
                    help=f"node RPC url (default {DEFAULT_RPC})")
    ap.add_argument("--version", action="version", version=f"animica-serve {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    run_p = sub.add_parser("run", help="serve jobs (default)")
    earn_p = sub.add_parser("earnings", help="print the worker ledger for --address and exit")
    # Accept the shared options on either side of the subcommand:
    # `animica-serve earnings --address X` is what people naturally type.
    for sp in (run_p, earn_p):
        sp.add_argument("--address", "-a", dest="address", default=argparse.SUPPRESS)
        sp.add_argument("--rpc", dest="rpc", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "earnings":
            if not args.address:
                raise SystemExit("--address is required")
            return show_earnings(args)
        return serve(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(cli())
