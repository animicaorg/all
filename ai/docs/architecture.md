# Animica AI architecture

## Two tiers, three providers

```
┌──────────────────────────────────────────────────────────────────┐
│  user                                                            │
│                                                                  │
│       animica chat                                               │
│       │                                                          │
│       ▼                                                          │
│  agent_runtime.cli.chat (REPL)                                   │
│       │                                                          │
│       ▼                                                          │
│  agent_runtime.providers.ProviderCascade                         │
│       │                                                          │
│       ▼                                                          │
│   ┌── distributed-aicf ──────────────────────────────────┐       │
│   │  agent_runtime.aicf_client  (JSON-RPC)               │       │
│   │   ──> AICF protocol over animica node RPC            │       │
│   │   ──> miners (running flagship model)                │       │
│   │   <── streamed tokens                                │       │
│   │   wallet pays per turn from agent_runtime.wallet     │       │
│   └──────────────────────────────────────────────────────┘       │
│   │                                                              │
│   │ fallback if distributed unavailable / wallet empty           │
│   ▼                                                              │
│   ┌── local-flagship ────────────────────────────────────┐       │
│   │  flagship_agent.inference.LocalBundleRunner          │       │
│   │   loads ai/flagship_agent/models/export/latest/      │       │
│   │   honors manifest.json::available_for_real_inference │       │
│   └──────────────────────────────────────────────────────┘       │
│   │                                                              │
│   │ fallback if no bundle on disk                                │
│   ▼                                                              │
│   ┌── offline ───────────────────────────────────────────┐       │
│   │  static templates; explains why deeper modes failed  │       │
│   └──────────────────────────────────────────────────────┘       │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## Mining side (additive — chain consensus unchanged)

```
┌──────────────────────────────────────────────────────────────────┐
│  miner host                                                      │
│                                                                  │
│       animica miner cpu --address ...   (existing PoW; unchanged)│
│       │                                                          │
│       └─ contributes to consensus / earns block rewards          │
│                                                                  │
│       animica miner aicf-worker start --address ...   (NEW)      │
│       │                                                          │
│       ▼                                                          │
│  agent_runtime.aicf_worker.AICFWorker                            │
│       │                                                          │
│       ├─ register: tiers, hardware, GPU/CPU/VRAM/RAM             │
│       ├─ claim job from AICF queue                               │
│       ├─ run inference via flagship bundle                       │
│       └─ submit result + attestation; earn AICF credits          │
│                                                                  │
│       animica miner pool-client connect <addr>   (NEW)           │
│       │                                                          │
│       └─ persist pool connection details for the existing        │
│          mining commands (cpu / pool) to read.                   │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

## Local training pipeline (produces the flagship bundle)

```
ai/flagship_agent/scripts/train_flagship_agent.sh
  │
  └─> flagship_agent.driver (subprocess orchestrator)
       │
       └─> stage scripts, each a separate subprocess:
           inventory_repo, build_corpora, clean_and_dedupe,
           build_graph, build_indices, build_datasets,
           check_contamination, train_cpt, train_sft,
           evaluate_model, run_critique, run_benchmark,
           export_bundle, smoke_test_bundle

  outputs:
    runs/<run_id>/_pipeline/    status, manifests, per-stage logs
    runs/<run_id>/{inventory,corpora,graph,index,datasets,training,eval}/
    models/export/<run_id>/      manifest.json + inference.json + model/
    models/export/latest         symlink to most recent successful export
```

## Mode + honesty truth-table

| FLAGSHIP_MODE | base model load | training | bundle ships | `available_for_real_inference` |
|---|---|---|---|---|
| `simulate` | no | no | stub manifest only | false |
| `lite` | tokenizer only | a few fake steps | stub + tokenizer | false |
| `full` | yes (strict) | real CPT + SFT | weights + tokenizer | true (gated on eval) |

The strict modes guarantee no bundle quietly claims "real" when it isn't:

1. `training.yaml::base_model.placeholders_reject` rejects placeholder ids
   like `hf-internal-testing/tiny-random-*` in full mode.
2. `modes.resolve_mode` raises if full is requested but no accelerator is
   available (unless `FLAGSHIP_ALLOW_CPU_REAL=1`).
3. `smoke_test_bundle.py` rejects a bundle whose declared mode is `full`
   but whose loaded model id ≠ requested.
4. `eval.yaml::gates.available_for_real_inference` requires effective full
   mode AND score ≥ threshold AND not contaminated.

## Wire format

- **Wallet payment**: `agent_runtime.wallet.sign_payment` delegates to
  the existing `animica.wallet.sign_payment_tx` — there's no new chain
  signer here. The chain treats AICF payments as ordinary transactions.
- **AICF protocol**: pure JSON-RPC over the animica node endpoint.
  Methods used: `aicf.estimateJobCost`, `aicf.submitInferenceJob`,
  `aicf.streamJob` (with `aicf.jobStatus` polling fallback),
  `aicf.settleJob`, plus worker-side `aicf.workerRegister`,
  `aicf.workerStatus`, `aicf.workerClaimNextJob`,
  `aicf.workerSubmitResult`.
- **IPFS bundle propagation**: tarball pushed via local IPFS daemon
  (`ipfs.api_endpoint`); CID + sha256 published in manifest; miners pull
  via configured gateways and verify sha256 before installing.

## Where each thing lives

| Concern | Source path |
|---|---|
| All YAML configs (shared) | `ai/configs/` |
| Chat REPL + provider cascade + wallet + AICF client | `ai/agent_runtime/src/agent_runtime/` |
| Training pipeline (scripts + driver) | `ai/flagship_agent/{scripts,src}/` |
| Mining CLI hooks (pool-client, aicf-worker) | `ai/agent_runtime/src/agent_runtime/cli/miner.py` |
| Chat subcommand registration | `python/animica/cli/chat.py` (thin re-export) |
| All sibling packages bundled into wheel | `python/pyproject.toml::tool.hatch.build.targets.wheel.force-include` |
