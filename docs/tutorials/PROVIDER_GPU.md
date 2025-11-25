# AICF Provider — Stand Up a GPU Worker

This tutorial shows how to bring up a **GPU-backed AICF provider** that can accept AI jobs from the Animica AI Compute Fund (AICF), execute them on NVIDIA GPUs, and return verifiable outputs (digests) that the on-chain flow references in **AIProof** claims.

> You will:
> - Register & stake a provider identity
> - Launch a GPU worker (Docker or systemd) that polls the AICF queue
> - Meet SLA requirements (heartbeats, latency, quality)
> - Expose metrics & logs, and harden your environment

---

## 0) Prerequisites

- **Animica node / RPC** endpoint reachable (devnet/testnet OK)
- **Python 3.10+** and the repo/packaging checked out (for the AICF CLIs)
- **NVIDIA GPU** with recent driver and **CUDA** toolkit/runtime
- Optional: **Docker** with `nvidia-container-toolkit` installed

Check your GPU stack:

```bash
nvidia-smi
nvcc --version || true

If using Docker:

# Ubuntu example:
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure
sudo systemctl restart docker


⸻

1) Provider Identity, Registration & Stake

AICF requires a provider ID, endpoint metadata, and minimum stake to be eligible.

1.1 Generate a provider key (if you don’t already have one)

You can reuse your Animica wallet key or generate a dedicated one for the provider. Keep secrets offline; only export what’s needed for CI/ops.

1.2 Register the provider

# RPC points at your node (or public testnet)
export RPC_URL="http://127.0.0.1:8545"
export CHAIN_ID=1

# Provider identity strings (example)
export PROVIDER_ID="prov_01ce5b7f"
export PROVIDER_NAME="ZetaGPU Labs"
export PROVIDER_REGION="us-west-2"
export PROVIDER_ENDPOINT="https://provider.zetagpu.example/api"

# Capability flags (AI, Quantum). Here we stand up an AI GPU provider.
python -m aicf.cli.provider_register \
  --rpc $RPC_URL --chain-id $CHAIN_ID \
  --id "$PROVIDER_ID" \
  --name "$PROVIDER_NAME" \
  --caps AI \
  --endpoint "$PROVIDER_ENDPOINT" \
  --region "$PROVIDER_REGION"

Behind the scenes this writes a provider record in AICF’s registry tables (or submits a signed request to the registry service), including optional attestation metadata.

1.3 Stake funds

# stake amount units depend on network config (see aicf/config.py or policy docs)
python -m aicf.cli.provider_stake \
  --rpc $RPC_URL --chain-id $CHAIN_ID \
  --id "$PROVIDER_ID" \
  --amount 100000000   # example

Check status:

python -m aicf.cli.provider_heartbeat \
  --rpc $RPC_URL --chain-id $CHAIN_ID \
  --id "$PROVIDER_ID" --print


⸻

2) Worker Overview

The GPU worker pulls jobs from the AICF queue, runs them (e.g., text↦embeddings, small generative tasks, vector ops), and uploads minimal result digests (and any required receipts). The on-chain AIProof references these digests; SLA metrics (latency, QoS, traps if enabled) determine payout and slashing risk.

Core components:
	•	Queue client: polls aicf/queue for lease ⇒ job run ⇒ result submit
	•	Model runtime: CUDA-accelerated inference (PyTorch / TensorRT / ONNX)
	•	Result builder: content-addressed digests (SHA3-512), metadata
	•	Heartbeat: periodic liveness pings to registry
	•	Metrics: Prometheus exporter

⸻

3) Configuration (ENV)

Create provider.env:

# Chain / node
RPC_URL=http://127.0.0.1:8545
CHAIN_ID=1

# AICF endpoints (adjust for your deployment)
AICF_QUEUE_URL=http://127.0.0.1:8700
AICF_RPC_WS=ws://127.0.0.1:8700/ws

# Provider identity & credentials (store securely)
PROVIDER_ID=prov_01ce5b7f
PROVIDER_SECRET=__redacted__             # API token or signed macaroon, if enabled

# GPU & runtime
GPU_BACKEND=cuda                         # cuda | rocm (experimental) | cpu (fallback)
CUDA_VISIBLE_DEVICES=0                   # e.g., "0,1" for multi-GPU
MAX_CONCURRENCY=2                        # jobs in parallel (per box)
BATCH_SIZE=8                             # model-dependent
MPS_ENABLE=0                             # 1 to enable CUDA MPS (advanced)

# Models & routing
MODEL_ALLOWLIST=text-embed-001,clip-ViT-B/32
MODEL_DIR=/opt/models                    # HF/ONNX caches
DISK_CACHE_DIR=/var/cache/aicf-provider

# SLA & timeouts
JOB_LEASE_SEC=120
JOB_MAX_RUNTIME_SEC=90
HEARTBEAT_SEC=15

# Telemetry
METRICS_PORT=9108
LOG_LEVEL=info

Keep secrets (e.g., PROVIDER_SECRET) in a vault or CI secret store. Never commit them.

⸻

4) Run with Docker (recommended)

4.1 docker-compose.yml

version: "3.9"
services:
  aicf-gpu-worker:
    image: ghcr.io/animica/aicf-gpu-worker:latest   # placeholder image name
    restart: unless-stopped
    env_file: ./provider.env
    ports:
      - "9108:9108"         # Prometheus metrics
    volumes:
      - ./models:/opt/models
      - ./cache:/var/cache/aicf-provider
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: ["gpu"]
    runtime: nvidia
    # For Compose v2+ on modern Docker, instead of 'runtime' you can use:
    # deploy: { resources: { reservations: { devices: [ { driver: "nvidia", capabilities: ["gpu"] } ] } } }

Bring it up:

docker compose up -d
docker compose logs -f aicf-gpu-worker

You should see:
	•	Heartbeats every HEARTBEAT_SEC
	•	Lease events when jobs are assigned
	•	Completed job metrics and result digests
	•	/metrics exposed on :9108/metrics

⸻

5) Run with systemd (bare-metal)

/etc/systemd/system/aicf-gpu-worker.service:

[Unit]
Description=AICF GPU Worker
After=network-online.target docker.service
Wants=network-online.target

[Service]
EnvironmentFile=/etc/aicf/provider.env
# If packaged as a Python entrypoint:
ExecStart=/usr/local/bin/aicf-gpu-worker
# Or Docker:
# ExecStart=/usr/bin/docker run --rm --gpus all --env-file /etc/aicf/provider.env \
#   -p 9108:9108 -v /opt/models:/opt/models -v /var/cache/aicf-provider:/var/cache/aicf-provider \
#   ghcr.io/animica/aicf-gpu-worker:latest
Restart=always
RestartSec=2
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target

sudo systemctl daemon-reload
sudo systemctl enable --now aicf-gpu-worker
journalctl -u aicf-gpu-worker -f


⸻

6) Smoke Test
	1.	Heartbeat:

curl -s http://localhost:9108/metrics | grep aicf
python -m aicf.cli.queue_list --rpc $RPC_URL --chain-id $CHAIN_ID | head

	2.	Test job submission (dev helper):

python -m aicf.cli.queue_submit \
  --rpc $RPC_URL --chain-id $CHAIN_ID \
  --kind AI \
  --model text-embed-001 \
  --input '["Animica brings useful work to consensus"]'

Watch worker logs for Assigned → Running → Completed and a result digest ID. Payouts settle at epoch boundaries; verify via:

python -m aicf.cli.payouts_inspect --rpc $RPC_URL --chain-id $CHAIN_ID --provider $PROVIDER_ID


⸻

7) SLA: Quality & Latency

AICF enforces SLAs (see docs/aicf/SLA.md):
	•	Latency: Jobs must finish within JOB_MAX_RUNTIME_SEC. Configure MAX_CONCURRENCY so the GPU is not oversubscribed.
	•	Quality: Depending on model/class of job, trap prompts or reference evaluations may apply.
	•	Heartbeats: Missed heartbeats degrade provider health and may suspend assignments.
	•	Slashing: Repeated failures or poor QoS can trigger penalties.

Tuning tips:
	•	Use TensorRT/ONNX exports for stable latency.
	•	Pin model weights on SSD; avoid cold downloads.
	•	Consider CUDA MPS for multi-tenant scheduling (advanced).
	•	Set per-model batch sizes in a routing table.

⸻

8) Security & Hardening
	•	Least-privilege: separate runtime user; mount only model/cache dirs.
	•	Network: firewall inbound (only metrics if needed); egress to AICF endpoints & model mirrors.
	•	Secrets: load from env files managed by your secret store; rotate regularly.
	•	Immutability: use pinned images and checksums; update through CI.
	•	Observability: ship logs to a central system; alert on error rates and SLA breaches.
	•	Isolation: if multi-tenant, consider MIG partitions on A100/H100 (NVIDIA MIG).

⸻

9) Scaling Out
	•	Horizontal: run N workers per region with a shared registry identity (or per-node IDs in the same org).
	•	Vertical: multiple GPUs per host; set CUDA_VISIBLE_DEVICES per instance.
	•	Auto-scaling: drive replicas by queue depth and p95 latency.

⸻

10) Troubleshooting
	•	No jobs assigned: insufficient stake, allowlist not met, region mismatch, or provider health low.
	•	CUDA errors: driver/runtime mismatch; verify nvidia-smi inside the container.
	•	Time-outs: reduce batch size; ensure disk cache; check PCIe power states.
	•	SLA fails: inspect aicf/sla/metrics.py outputs; compare against policy thresholds.

⸻

11) Reference CLI (quick sheet)

# List providers
python -m aicf.cli.queue_list --rpc $RPC_URL --chain-id $CHAIN_ID --providers

# Show current leases
python -m aicf.cli.provider_heartbeat --rpc $RPC_URL --chain-id $CHAIN_ID --id $PROVIDER_ID --print

# Force settlement (devnet)
python -m aicf.cli.settle_epoch --rpc $RPC_URL --chain-id $CHAIN_ID


⸻

12) What Counts as “Verifiable”

Workers must emit result digests (content-addressed, e.g., SHA3-512) with structured metadata:
	•	Model ID + version (pinned),
	•	Prompt/input hash,
	•	Output summary hash (or commitment if output is large),
	•	Timing/QoS counters.

AICF bridges convert these into AIProof references that the chain can price; audits and traps test for consistency and honesty.

⸻

Appendix: Example Prometheus Scrape

- job_name: "aicf_provider"
  metrics_path: /metrics
  static_configs:
    - targets: ["gpu-node-01:9108"]

Happy mining—useful work!
