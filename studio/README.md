# Animica Studio — serverless compute for the post-quantum chain

> Write a Python function, add a decorator, run it on Animica's decentralized
> GPU/CPU fleet, and pay in ANM. Modal-style developer experience, settled
> on-chain.

```python
import animica.studio as studio

app = studio.App("hello")

@app.function(image=studio.Image.debian_slim().pip_install("numpy"), gpu="A100")
def train(seed: int) -> float:
    import numpy as np
    rng = np.random.default_rng(seed)
    return float(rng.standard_normal(1_000_000).mean())

if __name__ == "__main__":
    with app.run():
        print(train.remote(42))          # one call, on the fleet
        print(list(train.map(range(8)))) # fan-out
```

```bash
animica studio run app.py::train --seed 42     # run once on the fleet
animica studio deploy app.py                    # publish a named, versioned app
animica studio serve app.py                     # hot-reload dev loop
animica studio fn list                          # what's deployed
```

## What this is

Animica Studio is a **Modal.com-style Functions-as-a-Service layer** that rides
on Animica's *already-live* compute rails instead of reinventing them:

| Layer | Reuses |
|---|---|
| Job dispatch / lease / result / settle | the live **AICF broker** (`aicf.*` JSON-RPC) |
| Worker fleet | the **provider-daemon** + the unified `animica up` miner/trainer rigs |
| Execution body | the **sandbox-runner** (`/execute`) |
| Payment | the **proven treasury-escrow leg** (sign transfer → `payment_tx_hash` → submit) |
| Payout math | **AICF economics** (`split_for_kind`, settlement, slashing) |

Studio adds the thin glue Modal users expect: a decorator SDK, an image spec, a
`function_compute` job class, a function registry, and the `.remote()` / `.map()`
ergonomics — about six new modules of glue over four production subsystems.

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the full wire trace and
[`ROADMAP.md`](./ROADMAP.md) for what's built vs. staged.

## Layout

```
studio/
  README.md           ← you are here
  ARCHITECTURE.md     ← the end-to-end wire, reuse-vs-new decisions
  ROADMAP.md          ← build status, the genuine build-outs (sandbox hardening, on-chain settle)
  examples/           ← runnable example apps
  web/                ← studio.animica.org frontend (landing + dashboard)
  deploy/             ← nginx + systemd + cutover runbook for studio.animica.org

# the importable SDK ships *inside* the animica package so `pip install animica` delivers it:
python/animica/studio/   ← App, @function, Image, .remote/.map, runners, billing
python/animica/cli/studio.py  ← `animica studio run|deploy|serve|fn`
rpc/methods/fn.py        ← aicf.fn.* function registry (sidecar to the broker)
provider-daemon/src/adapters/function.ts  ← the worker that executes function_compute jobs
```

The Studio brand lives at **studio.animica.org**.
