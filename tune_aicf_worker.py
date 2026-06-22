#!/usr/bin/env python3
"""Tune the AICF worker for ENA: code-specialized model, longer responses,
all CPU cores. Idempotent. Run, then it daemon-reloads + restarts the worker.

    sudo python3 /root/animica/tune_aicf_worker.py

Fixes the two ENA complaints:
  * "cut off"   -> ANIMICA_AICF_MAX_TOKENS 128 -> 1024 (responses complete)
  * "not smart" -> ANIMICA_AICF_MODEL = Qwen/Qwen2.5-Coder-1.5B-Instruct
                   (a code model, already cached; better for a coding agent than
                    the generic Qwen2.5-1.5B the 'standard' tier defaults to)
  * speed       -> OMP/MKL threads -> nproc (CPU-only box, no GPU)

Note: no GPU here, so inference is CPU-bound and still slow for long outputs.
For production-grade ENA latency, run an AICF worker on a GPU box (and a larger
coder model, e.g. Qwen2.5-Coder-7B-Instruct).
"""
import os
import re
import subprocess
import sys

UNIT = "/etc/systemd/system/animica-aicf-worker.service"
DESIRED = {
    "OMP_NUM_THREADS": str(os.cpu_count() or 4),
    "MKL_NUM_THREADS": str(os.cpu_count() or 4),
    "ANIMICA_AICF_MODEL": "Qwen/Qwen2.5-Coder-1.5B-Instruct",
    "ANIMICA_AICF_MAX_TOKENS": "1024",
}


def main() -> int:
    try:
        src = open(UNIT).read()
    except OSError as e:
        print(f"cannot read {UNIT}: {e} (run with sudo?)")
        return 2
    lines = src.splitlines()
    seen = set()
    out = []
    for ln in lines:
        m = re.match(r"^Environment=([A-Z_]+)=", ln)
        if m and m.group(1) in DESIRED:
            key = m.group(1)
            out.append(f"Environment={key}={DESIRED[key]}")
            seen.add(key)
        else:
            out.append(ln)
    # Append any desired vars not already present, after the [Service] section.
    missing = [k for k in DESIRED if k not in seen]
    if missing:
        # insert after the last existing Environment= line, else at end
        idx = max((i for i, l in enumerate(out) if l.startswith("Environment=")), default=len(out) - 1)
        for k in reversed(missing):
            out.insert(idx + 1, f"Environment={k}={DESIRED[k]}")
    new = "\n".join(out) + "\n"
    if new == src:
        print("already tuned — nothing to change; restarting anyway")
    else:
        open(UNIT, "w").write(new)
        print("unit updated:")
    for k in DESIRED:
        print(f"  {k}={DESIRED[k]}")
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "restart", "animica-aicf-worker.service"], check=True)
    r = subprocess.run(["systemctl", "is-active", "animica-aicf-worker.service"], capture_output=True, text=True)
    print("worker:", r.stdout.strip())
    print("done — first call reloads the model (cold), then it's warmer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
