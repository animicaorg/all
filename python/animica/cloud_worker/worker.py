"""Animica Python Cloud provider worker — claim / execute / submit loop.

Stdlib-only (urllib for HTTP, subprocess for Docker) so ``pip install animica`` on a bare
box is enough. The execution side deliberately mirrors the gateway's own sandbox host
(apps/animica-marketplace/lib/cloud/sandbox.ts + sandbox/runner.py):

  * one ``docker run`` per job: --network none, --read-only, --cap-drop ALL,
    --security-opt no-new-privileges, unprivileged uid, cgroup memory/CPU caps, pid cap,
    small noexec tmpfs — the job cannot reach this machine's filesystem, network or creds;
  * the runner protocol (@@ANM:KIND:token@@ frames over private stdio) with a per-job
    random token, so guest code cannot forge frames;
  * fleet jobs are pure compute: every host-capability CALL is answered
    ``capability_denied`` (AI/chain/wallet/secrets are brokered only by the gateway and
    never reach providers);
  * the host wall-clock timer kills the container — the guest's own timeout is never
    trusted.

SAFETY RULE: if Docker or the runtime image is missing the worker REFUSES to run.
There is no unsandboxed fallback.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets as _secrets
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_GATEWAY = os.environ.get("ANIMICA_CLOUD_GATEWAY", "https://animica.dev")
RUNTIME_IMAGE = os.environ.get("ANIMICA_CLOUD_IMAGE", "anm-pycloud-runtime:1")
DOCKER_BIN = os.environ.get("ANIMICA_DOCKER_BIN", "docker")

_STATE_DIR = os.path.expanduser("~/.animica")
_STATE_FILE = os.path.join(_STATE_DIR, "cloud-worker.json")

_FRAME_RE = re.compile(r"^@@ANM:([A-Z]+):([a-f0-9]*)@@ (.*)$")

API = "/api/cloud/v1/providers"


class SandboxUnavailable(RuntimeError):
    """Docker or the runtime image is missing — the worker must not run."""


class GatewayError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"gateway HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


# ---------------------------------------------------------------------------
# Gateway client (stdlib urllib; bearer token auth)
# ---------------------------------------------------------------------------


class GatewayClient:
    def __init__(self, gateway: str, token: str = ""):
        self.gateway = gateway.rstrip("/")
        self.token = token

    def _request(self, method: str, path: str, body: Optional[dict] = None, timeout: int = 60):
        url = self.gateway + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("content-type", "application/json")
        req.add_header("user-agent", "animica-cloud-worker/1.0")
        if self.token:
            req.add_header("authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.status == 204 or not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise GatewayError(exc.code, exc.read().decode("utf-8", "replace")) from exc

    # -- provider API --------------------------------------------------------

    def register(self, *, address: str, name: str, capabilities: List[str], cpu_cores: int,
                 memory_mb: int, gpu: Optional[str], token: str) -> dict:
        return self._request("POST", f"{API}/register", {
            "address": address,
            "token": token,
            "name": name,
            "capabilities": capabilities,
            "cpu_cores": cpu_cores,
            "memory_mb": memory_mb,
            "gpu": gpu,
        }) or {}

    def claim(self) -> Optional[dict]:
        res = self._request("POST", f"{API}/claim", {})
        return (res or {}).get("job") if res else None

    def heartbeat(self, job_id: str) -> None:
        self._request("POST", f"{API}/heartbeat", {"job_id": job_id}, timeout=30)

    def result(self, payload: dict) -> dict:
        return self._request("POST", f"{API}/result", payload, timeout=120) or {}

    def fail(self, job_id: str, error: str) -> dict:
        return self._request("POST", f"{API}/fail", {"job_id": job_id, "error": error[:1000]}) or {}

    def runtime_bundle(self) -> dict:
        return self._request("GET", f"{API}/runtime") or {}


# ---------------------------------------------------------------------------
# Environment checks — the worker refuses to run without a working sandbox
# ---------------------------------------------------------------------------


def check_docker(docker_bin: str = DOCKER_BIN) -> str:
    """Return the Docker server version, or raise SandboxUnavailable."""
    if not shutil.which(docker_bin):
        raise SandboxUnavailable(
            f"'{docker_bin}' not found. The worker runs UNTRUSTED code and requires Docker "
            "for isolation — install Docker and try again. There is no unsandboxed mode."
        )
    try:
        out = subprocess.run(
            [docker_bin, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=20,
        )
    except Exception as exc:  # noqa: BLE001
        raise SandboxUnavailable(f"docker is installed but not responding: {exc}") from exc
    if out.returncode != 0:
        raise SandboxUnavailable(
            "docker is installed but the daemon is unreachable "
            f"({(out.stderr or out.stdout).strip()[:200]}). Start it (and check permissions) first."
        )
    return out.stdout.strip()


def check_image(image: str = RUNTIME_IMAGE, docker_bin: str = DOCKER_BIN) -> bool:
    out = subprocess.run([docker_bin, "image", "inspect", image], capture_output=True, timeout=30)
    return out.returncode == 0


def require_sandbox(image: str = RUNTIME_IMAGE, docker_bin: str = DOCKER_BIN) -> str:
    """Fail-closed startup gate: Docker reachable AND the runtime image present."""
    version = check_docker(docker_bin)
    if not check_image(image, docker_bin):
        raise SandboxUnavailable(
            f"runtime image '{image}' not found. Build the exact image the gateway runs with:\n"
            f"  python -m animica.cloud_worker build-image --gateway {DEFAULT_GATEWAY}\n"
            "The worker will not run jobs without it."
        )
    return version


def build_image(gateway: str, image: str = RUNTIME_IMAGE, docker_bin: str = DOCKER_BIN) -> str:
    """Fetch the sandbox build context from the gateway, verify digests, docker build."""
    check_docker(docker_bin)
    client = GatewayClient(gateway)
    bundle = client.runtime_bundle()
    files = bundle.get("files") or {}
    shas = bundle.get("sha3") or {}
    if "Dockerfile" not in files or "runner.py" not in files:
        raise RuntimeError("gateway did not return a complete build context")
    for name, content in files.items():
        digest = hashlib.sha3_256(content.encode("utf-8")).hexdigest()
        if shas.get(name) and shas[name] != digest:
            raise RuntimeError(f"digest mismatch for {name}: got {digest}, expected {shas[name]}")
    tag = bundle.get("image") or image
    with tempfile.TemporaryDirectory(prefix="anm-pycloud-build-") as tmp:
        for name, content in files.items():
            with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                fh.write(content)
        print(f"[worker] building {tag} from gateway-served context ({len(files)} files, digests verified)")
        proc = subprocess.run([docker_bin, "build", "-t", tag, tmp])
        if proc.returncode != 0:
            raise RuntimeError(f"docker build failed with exit code {proc.returncode}")
    return tag


# ---------------------------------------------------------------------------
# Hardware probe (advertised at registration; the gateway clamps everything again)
# ---------------------------------------------------------------------------


def _total_memory_mb() -> int:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 1024


def _detect_gpu() -> Optional[str]:
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        out = subprocess.run([smi, "--query-gpu=name", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10)
        name = (out.stdout or "").strip().splitlines()
        return name[0][:64] if out.returncode == 0 and name else None
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Sandboxed execution — host side of the runner protocol
# ---------------------------------------------------------------------------

_DENY_REPLY = "fleet executions are pure compute; host capabilities are only brokered by the gateway"


def run_job_in_sandbox(job: dict, *, image: str = RUNTIME_IMAGE, docker_bin: str = DOCKER_BIN,
                       cpus: str = "1") -> dict:
    """Run one claimed job to completion inside the hardened container.

    Returns a report dict shaped for POST /providers/result:
      { status: ok|error|timeout|crashed, result, error, error_type, traceback,
        stdout, logs, wall_ms, reported_cpu_ms, max_rss_kb }
    ``crashed`` means an infrastructure failure this worker should report via /fail
    (requeue) rather than /result (terminal).
    """
    timeout_ms = max(1000, int(job.get("timeout_ms") or 30000))
    memory_mb = max(64, min(int(job.get("memory_mb") or 256), 4096))
    token = _secrets.token_hex(16)
    name = "anm-pyw-" + re.sub(r"[^a-zA-Z0-9]", "", str(job.get("id", "")))[:24] + "-" + _secrets.token_hex(4)

    workdir = tempfile.mkdtemp(prefix="anm-pyw-")
    code_dir = os.path.join(workdir, "code")
    os.makedirs(code_dir, exist_ok=True)
    handler = os.path.join(code_dir, "handler.py")
    with open(handler, "w", encoding="utf-8") as fh:
        fh.write(str(job.get("source") or ""))
    os.chmod(handler, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    # The container user (10001) must traverse the bind-mounted dirs.
    os.chmod(code_dir, 0o755)
    os.chmod(workdir, 0o755)

    args = [
        docker_bin, "run", "--rm", "-i", "--name", name,
        "--network", "none",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", "10001:10001",
        "--memory", f"{memory_mb}m",
        "--memory-swap", f"{memory_mb}m",
        "--cpus", cpus,
        "--pids-limit", "128",
        "--ulimit", "nofile=256:256",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
        "-v", f"{code_dir}:/app/code:ro",
        "--label", "animica.pycloudworker=1",
        "-e", f"ANM_PROTO_TOKEN={token}",
        "-e", "ANM_CAPTURE_PATH=/tmp/.anm-stdout",
        image,
    ]

    started = time.time()
    logs: List[Dict[str, Any]] = []
    state: Dict[str, Any] = {"result_frame": None, "proto_error": None, "timed_out": False}

    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, bufsize=1)

    def _write(obj: dict) -> None:
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.write(json.dumps(obj) + "\n")
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass

    # BOOT frame — identical shape to the gateway host.
    _write({
        "config": {
            "code_dir": "/app/code",
            "module": "handler",
            "entrypoint": job.get("entrypoint") or "main",
            "timeout_ms": timeout_ms,
            "memory_mb": memory_mb,
            "max_pids": 128,
            "max_output_bytes": 1024 * 1024,
        },
        "request": job.get("request") or {},
        "meta": job.get("meta") or {},
    })

    def _reader() -> None:
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                m = _FRAME_RE.match(line.rstrip("\n"))
                if not m:
                    continue
                kind, frame_token, body = m.group(1), m.group(2), m.group(3)
                if frame_token != token:
                    continue  # forged or stale frame
                if kind == "RESULT":
                    try:
                        outer = json.loads(body)
                        state["result_frame"] = json.loads(base64.b64decode(outer["b64"]).decode("utf-8"))
                    except Exception as exc:  # noqa: BLE001
                        state["proto_error"] = f"bad result frame: {exc}"
                elif kind == "ERROR":
                    try:
                        state["proto_error"] = json.loads(body).get("error") or "runner error"
                    except Exception:  # noqa: BLE001
                        state["proto_error"] = "runner error"
                elif kind == "LOG":
                    try:
                        entry = json.loads(body)
                        if len(logs) < 500:
                            logs.append({
                                "level": str(entry.get("level", "info"))[:16],
                                "message": str(entry.get("message", ""))[:2000],
                            })
                    except Exception:  # noqa: BLE001
                        pass
                elif kind == "CALL":
                    # Pure-compute contract: every capability call is denied, with the reply
                    # the runner expects so user code gets a clean CapabilityDenied.
                    try:
                        call = json.loads(body)
                        _write({"id": call.get("id"), "ok": False,
                                "code": "capability_denied", "error": _DENY_REPLY})
                    except Exception:  # noqa: BLE001
                        pass
        except (OSError, ValueError):
            pass

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    deadline = timeout_ms / 1000.0 + 5.0
    try:
        proc.wait(timeout=deadline)
    except subprocess.TimeoutExpired:
        state["timed_out"] = True
        subprocess.run([docker_bin, "kill", name], capture_output=True, timeout=30)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
    reader.join(timeout=5)
    stderr_tail = ""
    try:
        if proc.stderr:
            stderr_tail = proc.stderr.read()[-500:]
    except (OSError, ValueError):
        pass
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
    except (OSError, ValueError):
        pass
    shutil.rmtree(workdir, ignore_errors=True)

    wall_ms = int((time.time() - started) * 1000)
    exit_code = proc.returncode
    frame = state["result_frame"]
    oom_killed = exit_code == 137 and not state["timed_out"]

    if state["timed_out"]:
        return {"status": "timeout", "error": f"execution exceeded its {timeout_ms}ms budget",
                "error_type": "Timeout", "stdout": "", "logs": logs, "wall_ms": wall_ms,
                "reported_cpu_ms": 0, "max_rss_kb": 0}

    if not frame:
        if oom_killed:
            # The guest exceeded its own memory cap: that is user code's fault — terminal.
            return {"status": "error", "error": f"execution exceeded its {memory_mb}MB memory limit",
                    "error_type": "MemoryLimit", "stdout": "", "logs": logs, "wall_ms": wall_ms,
                    "reported_cpu_ms": 0, "max_rss_kb": 0}
        detail = state["proto_error"] or stderr_tail.strip() or f"exit {exit_code}"
        return {"status": "crashed", "error": f"sandbox produced no result ({str(detail)[:300]})",
                "stdout": "", "logs": logs, "wall_ms": wall_ms,
                "reported_cpu_ms": 0, "max_rss_kb": 0}

    status = frame.get("status")
    if status not in ("ok", "timeout", "error"):
        status = "error"
    usage = frame.get("usage") or {}
    return {
        "status": status,
        "result": frame.get("result"),
        "error": frame.get("error"),
        "error_type": frame.get("type"),
        "traceback": frame.get("traceback"),
        "stdout": str(frame.get("stdout") or "")[: 1024 * 1024],
        "logs": logs,
        "wall_ms": wall_ms,
        "reported_cpu_ms": int(usage.get("cpu_ms") or 0),
        "max_rss_kb": int(usage.get("max_rss_kb") or 0),
    }


# ---------------------------------------------------------------------------
# State (token persistence)
# ---------------------------------------------------------------------------


def load_state() -> dict:
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    os.makedirs(_STATE_DIR, exist_ok=True)
    tmp = _STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, _STATE_FILE)


# ---------------------------------------------------------------------------
# The worker loop
# ---------------------------------------------------------------------------


class Worker:
    def __init__(self, *, gateway: str, address: str, name: str = "", gpu: Optional[str] = None,
                 image: str = RUNTIME_IMAGE, docker_bin: str = DOCKER_BIN,
                 poll_seconds: float = 5.0, cpus: str = "1"):
        self.gateway = gateway.rstrip("/")
        self.address = address
        self.name = name
        self.gpu = gpu if gpu is not None else _detect_gpu()
        self.image = image
        self.docker_bin = docker_bin
        self.poll_seconds = poll_seconds
        self.cpus = cpus
        self.client = GatewayClient(self.gateway)
        self.provider_id: Optional[str] = None
        self._stop = threading.Event()

    # -- lifecycle -----------------------------------------------------------

    def register(self) -> dict:
        state = load_state()
        token = state.get("token") if state.get("gateway") == self.gateway else None
        if not token:
            token = "anm_prov_" + _secrets.token_urlsafe(32)
        self.client.token = token
        caps = ["python3.12"] + (["gpu"] if self.gpu else [])
        info = self.client.register(
            address=self.address,
            name=self.name,
            capabilities=caps,
            cpu_cores=os.cpu_count() or 1,
            memory_mb=_total_memory_mb(),
            gpu=self.gpu,
            token=token,
        )
        self.provider_id = info.get("provider_id")
        save_state({"gateway": self.gateway, "token": token,
                    "provider_id": self.provider_id, "address": self.address})
        return info

    def stop(self) -> None:
        self._stop.set()

    # -- one job -------------------------------------------------------------

    def process_one(self) -> bool:
        """Claim and fully process at most one job. Returns True when a job was served."""
        job = self.client.claim()
        if not job:
            return False
        job_id = job["id"]
        print(f"[worker] claimed job {job_id} ({job.get('request_id')}) "
              f"timeout={job.get('timeout_ms')}ms mem={job.get('memory_mb')}MB attempt={job.get('attempts')}")

        # Lease heartbeat while the job runs (every 1/4 of the lease window).
        lease = max(30, int(job.get("lease_seconds") or 300))
        hb_stop = threading.Event()

        def _hb() -> None:
            while not hb_stop.wait(lease / 4):
                try:
                    self.client.heartbeat(job_id)
                except Exception as exc:  # noqa: BLE001
                    print(f"[worker] heartbeat failed: {exc}")

        hb = threading.Thread(target=_hb, daemon=True)
        hb.start()
        try:
            report = run_job_in_sandbox(job, image=self.image, docker_bin=self.docker_bin, cpus=self.cpus)
        except Exception as exc:  # noqa: BLE001
            report = {"status": "crashed", "error": f"worker exception: {exc}"}
        finally:
            hb_stop.set()
            hb.join(timeout=5)

        if report["status"] == "crashed":
            # Infrastructure failure on THIS machine: report via /fail so the job requeues
            # for another provider instead of terminally failing the customer's execution.
            print(f"[worker] job {job_id} crashed locally: {report.get('error')}")
            try:
                r = self.client.fail(job_id, str(report.get("error") or "worker crash"))
                print(f"[worker] reported failure -> {r.get('terminal')}")
            except Exception as exc:  # noqa: BLE001
                print(f"[worker] could not report failure: {exc}")
            return True

        payload = {
            "job_id": job_id,
            "status": report["status"],
            "result": report.get("result"),
            "error": report.get("error"),
            "error_type": report.get("error_type"),
            "stdout": report.get("stdout", ""),
            "logs": report.get("logs", []),
            "wall_ms": report.get("wall_ms", 0),
            "reported_cpu_ms": report.get("reported_cpu_ms", 0),
            "max_rss_kb": report.get("max_rss_kb", 0),
        }
        try:
            res = self.client.result(payload)
            payout = res.get("payout_nanm", "0")
            print(f"[worker] job {job_id} -> {res.get('execution_status')} "
                  f"(price {res.get('price_nanm')} nANM, payout {payout} nANM, settled={res.get('settled')})")
        except GatewayError as exc:
            print(f"[worker] result rejected: {exc}")
        return True

    # -- main loop -----------------------------------------------------------

    def run(self, once: bool = False) -> None:
        version = require_sandbox(self.image, self.docker_bin)  # fail-closed: no sandbox, no worker
        print(f"[worker] docker {version}, image {self.image} present — sandbox ready")
        info = self.register()
        print(f"[worker] registered provider {self.provider_id} "
              f"(payout {self.address}, caps {info.get('capabilities')}, "
              f"earned so far {info.get('earned_nanm', '0')} nANM)")
        idle_announced = False
        while not self._stop.is_set():
            try:
                served = self.process_one()
            except GatewayError as exc:
                if exc.status in (401, 403):
                    print(f"[worker] gateway refused us ({exc}); re-registering in 30s")
                    time.sleep(30)
                    try:
                        self.register()
                    except Exception as rexc:  # noqa: BLE001
                        print(f"[worker] re-register failed: {rexc}")
                    continue
                print(f"[worker] gateway error: {exc}")
                served = False
            except (urllib.error.URLError, OSError) as exc:
                print(f"[worker] network error: {exc}; retrying in {self.poll_seconds * 3:.0f}s")
                time.sleep(self.poll_seconds * 3)
                continue

            if once and served:
                return
            if not served:
                if not idle_announced:
                    print(f"[worker] queue empty — polling every {self.poll_seconds:.0f}s")
                    idle_announced = True
                self._stop.wait(self.poll_seconds)
            else:
                idle_announced = False
