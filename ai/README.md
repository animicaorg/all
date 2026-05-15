# Animica AI Stack

Two-tier system that powers `animica chat` and the Animica-native coding agent.

## Tiers

### `agent_runtime/` — distributed AICF client (primary)

Submits inference jobs to the AICF (AI Compute Fund) protocol; pays from a configured
wallet; routes work to miners running the flagship model. This is what `animica chat`
uses by default when the wallet has funds and the AICF network is reachable.

### `flagship_agent/` — local training pipeline + offline fallback bundle

Trains the Animica-native flagship coding model from the monorepo. Exports a
downloadable bundle (weights + tokenizer + manifest) that miners install to serve
AICF inference jobs. Also acts as the offline fallback when distributed mode is
unavailable.

Base model: **DeepSeek-Coder-V2-Lite-Instruct** (16B MoE, 2.4B active).
Bundle hosting: **IPFS**; manifest CID published on the Animica chain.

## Provider selection

`agent_runtime` exports two provider descriptors:

| Provider          | Source                                          | Requires                |
|-------------------|-------------------------------------------------|-------------------------|
| `distributed-aicf`| AICF protocol, miners run flagship              | wallet w/ ANIMICA, network |
| `local-flagship`  | Local exported bundle in `flagship_agent/models/export/<run_id>/` | exported bundle on disk |

`animica chat` prefers `distributed-aicf`, falls back to `local-flagship`, and only
then returns to existing offline behavior in `animica-agent`.

## Mining integration

**The Animica chain and PoW mining continue to operate exactly as before.** This
work is purely additive: miners optionally also register as AICF compute workers
to serve `animica chat` queries and contribute to flagship training (Phase 7b,
software-layer only, opt-out flag `--no-aicf`). No consensus changes.

`docs/rfcs/0001-proof-of-useful-work.md` exists as a long-term design exploration
only; it is **not** on a near-term implementation roadmap and **does not** affect
the current chain.

## Layout

```
ai/
  agent_runtime/         distributed AICF client + animica chat CLI
    src/agent_runtime/
    tests/
    pyproject.toml
  flagship_agent/        local training pipeline (fallback bundle)
    src/flagship_agent/
    scripts/             train_flagship_agent.sh, stage scripts
    configs/             stage-specific overrides
    data/  manifests/  runs/  models/  evals/
    tests/
    pyproject.toml
    requirements.txt
  configs/               shared cross-tier configs (12 YAMLs)
  docs/
    rfcs/                design documents
  scripts/               cross-tier scripts
  tests/                 cross-tier integration tests
```

## One-command operator goals

| Goal | Command |
|---|---|
| Open AICF-paid chat REPL | `animica chat` |
| Train local fallback bundle | `bash ai/flagship_agent/scripts/train_flagship_agent.sh` |
| Smoke-test exported bundle | `python ai/flagship_agent/scripts/smoke_test_bundle.py` |
| Strict full-mode training | `FLAGSHIP_MODE=full bash ai/flagship_agent/scripts/train_flagship_agent.sh` |
| CPU real training (explicit) | `FLAGSHIP_MODE=full FLAGSHIP_ALLOW_CPU_REAL=1 bash ai/flagship_agent/scripts/train_flagship_agent.sh` |

## Honesty principles

Every artifact carries `requested_mode` and `effective_mode`. Bundles built in
`lite` or `simulate` modes never declare themselves as `real`. Inference availability
is a separate flag (`available_for_real_inference`) the integration layer reads
before routing real queries to the local bundle.
