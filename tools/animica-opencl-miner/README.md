# Local GPU Miner for Animica

A standalone Stratum-aware GPU miner that runs OpenCL hashing on your local
device (e.g., Intel Arc B580) and submits shares/blocks to an Animica node or
pool.

## Features

- **GPU-powered mining** with PyOpenCL (Intel Arc, AMD RDNA, NVIDIA, etc.).
- **Stratum client** with subscribe/authorize/notify support.
- **Continuous scanning** tuned for large nonce batches.
- **Configurable scan size** (iterations, max shares per batch).
- **Detailed logging** (difficulty updates, share submissions, hash rates).

## Prerequisites

1. Python 3.10+ with pip.
2. GPU drivers/OpenCL runtime for your hardware.

## Installation

1. Install the package (editable mode recommended while developing):

   ```powershell
   pip install -e .
   ```

   This exposes the `opencl_miner` module and the `opencl-miner` console script
   to your environment.
2. Confirm the CLI is on your `$PATH`:

   ```powershell
   opencl-miner --help
   ```

## Usage

### Run the Stratum-based OpenCL miner

> Ensure the Stratum server is reachable; the miner will hold a persistent
> connection and stream jobs as they arrive.

**Windows (PowerShell):**

```powershell
opencl-miner `
  --host rpc.animica.org `
  --port 23454 `
  --worker arc-rig `
  --address anim1zqpd3myua8uyas7mwj0dxu7g9eaj0xyde7apx0s673xam0vay576xgg8z52dr `
  --iterations 50000000 `
  --max-found 4
```

**Linux / macOS (Bash):**

```bash
opencl-miner \
  --host rpc.animica.org \
  --port 23454 \
  --worker arc-rig \
  --address anim1zqpd3myua8uyas7mwj0dxu7g9eaj0xyde7apx0s673xam0vay576xgg8z52dr \
  --iterations 50000000 \
  --max-found 4
```

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `127.0.0.1` | Stratum hostname/IP |
| `--port` | `23454` | Stratum TCP port |
| `--worker` | `opencl.worker` | Worker name reported to the pool |
| `--address` | *(required)* | Payout address (Bech32 `anim1...` or `0x...`) |
| `--iterations` | `50000000` | Nonces per GPU dispatch |
| `--max-found` | `4` | Max candidate shares returned per scan batch |
| `--platform` | *(auto)* | OpenCL platform index override |
| `--device` | *(auto)* | OpenCL device index override |
| `--log-level` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

## Performance

**Intel Arc B580 (OpenCL):** ~50-100M hashes/sec (80-90% GPU utilization) with
`--iterations 50000000`.

## How it works

1. **Stratum session** – connects, subscribes, and authorizes the worker/address.
2. **GPU worker thread** – keeps the OpenCL backend warmed up.
3. **Job handling** – applies `mining.notify` payloads to the worker thread.
4. **Scanning** – iterates nonce windows and compares draws vs. share target.
5. **Submission** – accepted shares go back via `mining.submit` immediately.
6. **Stats/logs** – periodic hash-rate summaries plus share/block outcomes.

## Troubleshooting

### "Device not found" / "OpenCL not available"
- `pip list | findstr pyopencl` (PowerShell) or `pip list | grep pyopencl`.
- Confirm GPU drivers expose OpenCL. On Windows, check Device Manager.
- Quick test: `python -c "import pyopencl as cl; print([d.name for p in cl.get_platforms() for d in p.get_devices()])"`.

### Low GPU usage
- Increase `--iterations` (e.g., `100000000`) to keep the kernel saturated.
- Ensure your Stratum endpoint is emitting jobs; idle pools yield idle GPUs.

### Stratum connection errors
- Double-check `--host`/`--port` firewalls and NAT rules.
- Some pools require TLS termination or stunnel; adapt accordingly.
- Run with `--log-level DEBUG` to watch reconnect loops and payloads.
