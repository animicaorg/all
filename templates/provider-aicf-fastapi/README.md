# Provider Template — AICF (FastAPI)

This template scaffolds a production-grade **Animica AI Compute Fund (AICF) provider** written with **FastAPI**. It implements the core control-plane loops a provider needs to:

- Register & stake (out-of-band) with the AICF registry
- Poll the **AICF queue** for assignments (AI/Quantum jobs)
- Acquire/renew **leases**, execute workloads, and **produce results**
- Emit **proof references** (e.g., AIProof / QuantumProof) and job **digests**
- Report status & metrics, expose **health endpoints**, and integrate with observability

It includes switches for **TEE/QPU attestation**, **GPU backends**, and tunable **concurrency**. Use it to stand up a local dev provider or evolve it into your production worker.

---

## At a glance

- **Framework:** FastAPI + Uvicorn (async workers, graceful shutdown)
- **Job kinds:** `AI` and `Quantum` (matching AICF types)
- **Control loops:** queue poller, lease renewer, compute workers
- **Attestation:** SGX / SEV-SNP / CCA (pluggable), or disabled for dev
- **GPU:** optional CUDA / ROCm / OpenCL hooks
- **Observability:** `/metrics` (Prometheus), structured JSON logs
- **Security posture:** minimal surface; no end-user keys; rate-limit friendly

---

## Generating a project from the template

If you’re in the monorepo containing the template engine:

```bash
# Choose your output directory & variable values
OUTDIR=my-aicf-provider

python -m templates.engine.cli render \
  templates/provider-aicf-fastapi \
  --out "$OUTDIR" \
  --var project_name="Animica AICF Provider" \
  --var project_slug="animica-aicf-provider" \
  --var rpc_url="http://localhost:8545" \
  --var chain_id=1337 \
  --var aicf_queue_url="http://localhost:8787" \
  --var service_port=8080 \
  --var enable_attestation=true \
  --var attestation_kind=sgx \
  --var concurrency=4 \
  --var max_parallel_jobs=16 \
  --var use_gpu=false \
  --var gpu_backend=none

Variables are validated by templates/schemas/variables.schema.json. See all options in
templates/provider-aicf-fastapi/variables.json.

⸻

Project layout (generated)

{{project_slug}}/
  app/
    __init__.py
    main.py               # FastAPI app, routes: /healthz /readyz /metrics /version
    config.py             # env parsing & defaults (from variables.json → .env)
    models.py             # Pydantic models: JobSpecAI, JobSpecQuantum, Lease, Result
    queue.py              # AICF queue client: poll/ack/renew API
    worker.py             # Orchestrator: acquire leases, execute jobs, complete
    compute/
      __init__.py
      ai.py               # AI job runner (model dispatch, sandbox, timeouts)
      quantum.py          # Quantum job runner (trap handling, timeouts)
      attestation.py      # SGX/SEV/CCA verification hooks (pluggable)
      gpu.py              # GPU backend adapters (cuda/rocm/opencl/noop)
    proofs/
      __init__.py
      adapter.py          # Build proof references/digests → hand to AICF
    logging.py            # structlog/uvicorn logging configuration
    metrics.py            # Prometheus counters/histograms
    lifecycles.py         # startup/shutdown hooks, background tasks
  tests/
    test_smoke.py         # basic health/metrics checks
  Dockerfile
  pyproject.toml
  requirements.txt
  .env.example
  Makefile
  README.md               # (this file, adapted to your project)

Note: The exact file set may evolve. The template aims to keep the public surface stable.

⸻

Quickstart (local)
	1.	Create & activate a venv

python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt

	2.	Copy .env.example → .env and edit values to match your devnet:

cp .env.example .env
# Set RPC_URL, CHAIN_ID, AICF_QUEUE_URL, CONCURRENCY, GPU_BACKEND, etc.

	3.	Run the API server

uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"

	4.	Start the worker loop (poll queue, execute jobs, complete)

python -m app.worker

	5.	Check health & metrics

curl -s localhost:8080/healthz
curl -s localhost:8080/readyz
curl -s localhost:8080/metrics | head


⸻

Docker

Build & run:

IMAGE=${IMAGE:-ghcr.io/animica-labs/aicf-provider:dev}
docker build -t "$IMAGE" .
docker run --rm --env-file .env -p 8080:8080 "$IMAGE"

For multi-arch builds: see the repo’s ops/docker helpers or use docker buildx.

⸻

Configuration (environment)

These map 1:1 with template variables (see variables.json). Suggested defaults for dev are shown.

Variable	Description	Example
APP_NAME	Process/app name	animica-aicf-provider
PORT	HTTP port	8080
LOG_LEVEL	`DEBUG	INFO
RPC_URL	Animica node RPC URL	http://localhost:8545
CHAIN_ID	Chain ID	1337
AICF_QUEUE_URL	Queue/dispatcher base URL	http://localhost:8787
REGION	Provider region tag	local
ENABLE_ATTESTATION	Require attestation	true
ATTESTATION_KIND	`sgx	sev_snp
CONCURRENCY	Worker concurrency	4
MAX_PARALLEL_JOBS	Max inflight jobs	16
USE_GPU	Enable GPU	false
GPU_BACKEND	`none	cuda

You can also surface rate limits, task timeouts, and artifact paths (if your workloads read models/circuits from disk) via additional variables or a config section in app/config.py.

⸻

Endpoints
	•	GET /healthz — quick check: process up, main dependencies loaded
	•	GET /readyz — confirms queue connectivity and worker loop readiness
	•	GET /version — semver + git describe (if enabled)
	•	GET /metrics — Prometheus counters/histograms (HTTP, queue, jobs, compute)

Optional internal endpoints (guarded or disabled by default):
	•	POST /admin/reload — re-scan models/circuits; refresh cache
	•	GET /admin/leases — current leases & expiries (redacted)

Example:

curl -s localhost:8080/version | jq
curl -s localhost:8080/metrics | grep aicf_provider_jobs_total


⸻

AICF job lifecycle

Queue.poll() → Assignment → Lease(start, ttl)
  → Compute (AI|Quantum)
     → Attestation (if enabled), GPU (if enabled)
     → Produce outputs + digests
     → Build proof references (AIProof|QuantumProof) via proofs.adapter
  → Complete (ack + result record)
  → (Background) SLA metrics, retries/backoff on failure

AI jobs
	•	Pull model id & prompt/payload
	•	Execute deterministically with configured runners; record QoS metrics (latency, success)
	•	Produce digest of outputs (e.g., SHA3-512) and optional artifact pointers
	•	Provide attestation evidence if TEE is required (SGX/SEV/CCA)

Quantum jobs
	•	Receive trap parameters; execute circuit with configured provider
	•	Verify trap ratios, produce per-run metrics
	•	Return result bundles suitable for QuantumProof construction

Proof Adapter
This module normalizes computation outputs into the shape expected by the on-chain proofs/ layer (hashes, attest bundles, trap outcomes). On devnet you can operate in a mock mode to focus on flow correctness before wiring real hardware.

⸻

Attestation

Set ENABLE_ATTESTATION=true and choose ATTESTATION_KIND:
	•	sgx: Provide PCK chains / QE identity; validate quotes in compute/attestation.py
	•	sev_snp: Validate SNP report & TCB level
	•	cca: Validate CCA Realm attestation token (COSE)
	•	none: Skip validation (DEV ONLY)

The template ships with placeholders and integration points. Use the repo’s proofs/attestations/* and proofs/quantum_attest/* modules as references for how evidence maps into proof metrics.

⸻

GPU backends

Enable via:

USE_GPU=true
GPU_BACKEND=cuda|rocm|opencl

The compute/gpu.py adapter handles detection and capability flags, and routes supported workloads accordingly. Fallback to CPU is automatic if detection fails.

⸻

Observability

Metrics (Prometheus):
	•	aicf_provider_jobs_total{kind,status} — completed jobs by kind & status
	•	aicf_provider_job_duration_seconds{kind} — compute latency histogram
	•	aicf_provider_queue_poll_interval_seconds — poll cadence
	•	aicf_provider_lease_renew_total — renew operations
	•	aicf_provider_errors_total{stage} — failures by stage (poll, compute, complete)

Logging: Structured JSON via app.logging with request IDs & lease IDs.
Tracing (optional): OTLP export stubs are provided; hook Tempo/Jaeger if desired.

⸻

Running against the devnet
	1.	Bring up the devnet stack (node + AICF + services). From the repo root:

make -C ops devnet-up


	2.	Register your provider (stake & capabilities). See aicf/cli/provider_register.py and aicf/policy/example.yaml for expected flags.
	3.	Start your provider API + worker (as above). Watch /metrics and the AICF dashboards (Grafana) for assignments and completions.

⸻

Make targets (suggested)

The generated project includes a minimal Makefile:
	•	make dev — run API & worker in dev mode (auto-reload where safe)
	•	make test — run unit tests
	•	make fmt / make lint — formatting & static checks
	•	make docker-build / make docker-run — container build/run
	•	make smoke — ping /healthz and poll queue once

⸻

Security notes
	•	Keep attestation validation enabled in staging/prod
	•	Do not accept untrusted control-plane calls; your provider should only talk to the AICF queue and local hardware/services
	•	Avoid writing raw job inputs/outputs to logs
	•	Enforce sane timeouts and resource limits; use MAX_PARALLEL_JOBS to prevent overload

⸻

Troubleshooting
	•	No assignments received: verify registry status, stake, allowlists, region filters, and AICF_QUEUE_URL
	•	Lease timeouts: check clock skew, renew cadence, and long-running job timeouts
	•	Attestation failures: confirm root chains, firmware/TCB versions, and measurement binding
	•	GPU unrecognized: confirm driver/runtime versions and that the container has device access

⸻

License

This template is provided under the selected license (default: Apache-2.0). See the generated project’s LICENSE file.

⸻

Appendix: Example .env

APP_NAME=animica-aicf-provider
PORT=8080
LOG_LEVEL=INFO

RPC_URL=http://localhost:8545
CHAIN_ID=1337
AICF_QUEUE_URL=http://localhost:8787
REGION=local

ENABLE_ATTESTATION=true
ATTESTATION_KIND=sgx

CONCURRENCY=4
MAX_PARALLEL_JOBS=16

USE_GPU=false
GPU_BACKEND=none

Happy computing!
