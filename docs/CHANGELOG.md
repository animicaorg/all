# Animica — Changelog
All notable **user-facing** changes to this repository will be documented here.

This project adheres to **[Semantic Versioning](https://semver.org/)** and follows the **Keep a Changelog** format. Dates are `YYYY-MM-DD`.  
Module-scoped, low-level tweaks that don’t affect the user experience live in per-module CHANGELOGs or commit history.

> Tip: For upgrade steps, search for **Migration** blocks and **Breaking** notes.

---

## [7.1.7] - 2026-07-11
### Changed — AICF inference uses every GPU on a multi-GPU rig
Worker-side; non-consensus. Especially benefits agent/coding workloads, which get
the largest model the hardware can serve.

- `animica up` now pools the VRAM of **all** visible GPUs (CUDA via torch, or every
  line of `nvidia-smi`) when choosing which serving tiers to advertise, instead of
  reading only GPU 0. A multi-GPU rig therefore qualifies for the larger tiers and
  serves a bigger model — e.g. a 4×24 GB rig reaches the elite (32B) tier.
- The inference engine shards that model across every card via `device_map="auto"`
  (set `ANIMICA_AICF_DEVICE_MAP=balanced` to spread evenly), and the load log prints
  `gpus_used=N/M` so operators can confirm all GPUs are engaged.
- Honest gating: the pooled VRAM is discounted a small per-GPU overhead (multi-GPU
  only) so a rig of small cards can't advertise a tier it would only OOM or silently
  CPU-offload on; `nvidia-smi` parsing tolerates a bad per-card line, and CPU/disk
  offload is logged loudly instead of serving at 10-100× slowdown.

### Fixed — Node Docker image builds again (PQ backend self-test)
Node operators / CI only; non-consensus, no on-box or on-chain behaviour change.

- `ops/docker/scripts/pq_backend_selftest.py` failed the node image build at the
  final `RUN` step. It required a working **SPHINCS+-SHAKE-128s** keypair, but
  SPHINCS+ (scheme id 2) is a forgeable stub that is **disabled on mainnet**
  (`coretx.schemes`, ANM-C01/L06) and has no backend other than the *insecure*
  pure-Python fallback (gated behind `ANIMICA_ALLOW_PQ_PURE_FALLBACK=1`). A mainnet
  build leaves that flag unset, so the self-test raised `NotImplementedError` and
  `docker build` exited 1.
- The self-test now **requires `ml_dsa_65`** — scheme id 11, the only
  mainnet-enabled signature scheme, which the old test did not cover at all — with a
  keypair/sign/verify round-trip **plus tamper-rejection** (a modified message or
  signature must fail to verify), and **asserts SPHINCS+ is gated off** instead of
  demanding it mint forgeable keys. The disabled, forgeable `dilithium3` (id 1) is no
  longer exercised. No production node ever enables the insecure pure-Python fallback.

## [7.1.6] - 2026-07-11
### Fixed — AICF inference uses the Apple Metal (MPS) GPU, not the CPU
Non-consensus, worker-side.

- The inference engine (`aicf_inference._try_load`) only ever loaded models on
  `cuda` or `cpu`. On an Apple Silicon Mac (M1/M2/M3) `torch.cuda.is_available()`
  is False, so it silently ran **every model on the CPU cores** — the Metal GPU
  went unused. It now auto-detects the accelerator (CUDA → Apple Metal/MPS → CPU)
  and loads on MPS in fp16, so Mac miners actually serve on the GPU. Pin with
  `ANIMICA_AICF_DEVICE=cpu|cuda|mps` to override.

## [7.1.5] - 2026-07-11
### Fixed — `animica up` gates AICF serving tiers by memory
Non-consensus, worker-side.

- `animica up` previously advertised `standard,premium,elite` for **any** GPU box,
  so an Apple Silicon Mac (Metal counts as a GPU) would claim `elite` and try to
  fetch/serve **Coder-32B (~65 GB)** — which won't fit an M2 mini's unified memory,
  wasting a huge download and failing at serve time. It now picks tiers from the
  machine's actual memory (GPU VRAM / Apple unified memory; CPU boxes use system
  RAM and cap at `standard`, since larger models are impractically slow on CPU):
  `free` ≥3 GB, `standard` ≥7 GB, `premium` ≥15 GB, `elite` ≥72 GB (Coder-32B
  needs an 80 GB-class accelerator). So a typical M2 mini serves
  `standard`+`premium` and skips `elite`. Explicit `ANIMICA_AICF_TIERS` still
  overrides the pool-facing worker's tiers. The chosen tiers show in `animica up --plan`.

## [7.1.4] - 2026-07-11
### Added — miners pre-install their models; `animica up` is ready-to-serve
Non-consensus, worker-side.

- **`animica up` (and `miner start --aicf` / `aicf-worker start`) now pre-download
  the models for the tiers the miner advertises**, in the background, so the first
  inference job serves immediately instead of stalling minutes on a multi-GB fetch.
  Idempotent and best-effort (a failure just falls back to lazy download). Disable
  with `ANIMICA_AICF_PREFETCH=0`.
- **New `animica miner install [--tiers …]`** to pre-fetch the tier models up front
  with visible progress (defaults to `ANIMICA_AICF_TIERS`, else all tiers).

## [7.1.3] - 2026-07-11
### Improved — much better AI coding, served by miners
Non-consensus. All changes live in the miner-served AICF worker and the edge
gateway; no chain fork, no node changes. A miner picks these up simply by
running `animica up` or `animica miner aicf-worker start` on 7.1.3.

- **Dynamic, capability-based token cap.** The old fixed 128-token cap truncated
  almost every code file mid-function. The worker now sizes each job to *what it
  can actually handle* — derived from the loaded model's context window and the
  device class (CPU vs CUDA/MPS), recomputed per job. "As large as the network
  can handle at any given time." Pin a hard ceiling with `ANIMICA_AICF_MAX_TOKENS`.
- **Coder-tuned tier defaults.** `standard`→`Qwen2.5-Coder-3B`, `premium`→
  `Qwen2.5-Coder-7B`, `elite`→`Qwen2.5-Coder-32B` (was general Qwen 1.5B/3B/7B).
  `free` stays a small general model. Auto-detection still upgrades to any larger
  cached/bundled model; per-tier overrides (`ANIMICA_AICF_MODEL_<TIER>`) unchanged.
- **Code-aware prompting.** Generic coding requests get a lean coding system
  prompt (and skip Animica RAG injection, which was derailing small models);
  Animica-specific questions keep their doc grounding. Override with
  `ANIMICA_AICF_CODING_PROMPT`.
- **Edge gateway + agents** default to a high output budget so the *worker's*
  dynamic cap is the real limit, and the animica.dev coding agents run on any
  serving tier (best available) instead of a single pinned model.

## [7.1.1] - 2026-07-10
### Added — the Verifiable Inference Engine (VIE)
A **non-consensus** AI-engine upgrade. No chain fork, no genesis, the node never
halts; everything here lives in the ENA coordinator + the `animica ai serve`
gateway, never in block validation. All new behavior is additive and backward
compatible — set the flags below to opt in.

- **Proof-of-Inference receipts.** Every completion from `/v1/chat/completions`
  and `/v1/completions` can carry an `animica_receipt`: a content-hashed
  (SHA3-256), post-quantum **ML-DSA-65-signed** (FIPS-204, domain
  `animica.ai.proof-of-inference.v1`) record of `(model, provider, prompt-hash,
  output-hash, tokens, seed, nonce)`, plus an `X-Animica-Receipt` response
  header. Controlled by `ANIMICA_AI_RECEIPTS=off|hash|signed` (default `signed`,
  degrades to `hash` when no signing key/PQ is available). `off` is byte-identical
  to 7.1.0. The gateway signs on a threadpool so the ~ms PQ signature never blocks
  the event loop. Signed by a **dedicated inference key** that controls no funds.
- **Quantum-seeded sampling.** Opt in per request with `{"animica":{"quantum_seed":
  true}}` (or `ANIMICA_AI_QUANTUM_SEED=1`): the RNG seed is derived from the node's
  quantum randomness beacon (falling back, honestly labelled, to the node CSPRNG
  then a local seed), and its provenance is recorded in the receipt.
- **Offline replay + verification.** `animica ai verify <receipt.json>` recomputes
  the hash and checks the signature with no node/model; `animica ai replay
  <receipt.json> --prompt "…"` re-runs seed-honoring local backends and checks the
  output hash. Replay is honest: `verified` only for reproducible local backends,
  `best_effort` for remote ones. New: `animica ai receipt show/verify`, and gateway
  routes `POST /v1/verify`, `GET /v1/signer`.
- **Provider mesh + intelligent router.** First-class **Claude** (Opus 4.8, Sonnet 5,
  Haiku 4.5, Fable 5), **Chutes/Bittensor**, and **ENA-served-checkpoint** backends,
  behind `providers.register_adapter`. A policy router adds ordered fallback chains,
  EWMA-latency telemetry, and a SQLite-backed circuit breaker (`GET
  /v1/router/status`). With no routing policy configured, routing is a pure
  pass-through — unchanged behavior. `animica ai chat --provider anthropic` works
  out of the box with `ANTHROPIC_API_KEY`.

### Changed
- `ModelAdapter.generate(...)` gains a keyword-only `seed=`, honored by
  deterministic/OpenAI-compatible/Ollama backends (and forwarded by the mesh);
  default `None` keeps every existing output unchanged.
- Gateway version string → `7.1.1`; `/health` now reports `receipts` mode and the
  (public-only) signer identity.

### Notes / boundaries (honest)
- The receipt **signature** is post-quantum (ML-DSA-65); the beacon **attestation**
  is classical (Ed25519 software self-signer unless a hardware QRNG is attached).
  The two are kept distinct in the receipt schema.
- On-chain anchoring degrades to a local envelope (`pending_node_rpc`) — the node
  `aicf.anchorReceipt` RPC does not exist yet.
- Heavy deps (fastapi/uvicorn/torch/etc.) remain lazy; stdlib-only paths are the default.

## [5.3.4] - 2026-07-03
### Fixed
- **CRITICAL — GPU miners' useful-work AI generation crashed and took the PoW
  loop down with it (mainnet chain halt).** `stratum_pool/aicf_inference.py`
  loaded the model onto `cuda:0` (via `device_map="auto"` when the resolved
  device was `"cuda"`) but at generation time only moved `input_ids` to GPU
  when `self._device == "cuda"` — and `self._device` is usually `None` (device
  auto-resolves at load), so inputs stayed on **cpu** while weights sat on
  **cuda:0**. `model.generate()` then raised *"Expected all tensors to be on
  the same device … index is on cpu, different from cuda:0"*, the useful-work
  worker errored, and the `animica-cli` miners stopped hashing. On mainnet the
  whole GPU rig farm decayed from ~2.7 GH/s to 0 over an hour and blocks
  stalled. Inputs are now placed on the model's **actual** device
  (`next(model.parameters()).device`), and the load path uses
  `device.startswith("cuda")` so `cuda:0`/`cuda:N` also GPU-load. Update rigs
  with `pip install -U animica` and restart mining to restore hashrate.

---

## [5.3.3] - 2026-07-03
### Changed
- **ENA AI training stack is now part of the BASE install.** Every ENA training
  shard was failing with `train_failed: "python_transformers backend needs
  transformers + datasets (+ torch)"` because the AI stack lived only in the
  `ai`/`gpu` extras — so a GPU rig that ran a plain `pip install animica` had no
  torch/transformers/datasets and earned nothing. `torch`, `transformers`,
  `datasets`, `accelerate`, `peft`, `trl`, `sentence-transformers`,
  `safetensors`, `sentencepiece`, `protobuf`, `scipy`, `einops`, and
  Linux-gated `bitsandbytes` (QLoRA) are now base dependencies, so `pip install
  -U animica` on a rig can train out of the box. **Note:** this makes the base
  install heavy (torch is ~GB). On Linux the default torch wheel is CUDA-enabled;
  the `ai`/`gpu` extras remain as aliases.

---

## [5.3.2] - 2026-07-03
### Fixed
- **Snapshot restore could silently import PARTIAL state and still advance the
  head — producing permanent, undetectable too-low account balances.**
  `core/db/snapshot.py::import_snapshot` imported state chunks, `_import_state_chunk`
  silently `continue`d past any dropped/corrupt entry, and the head was then set to
  the checkpoint unconditionally — while `manifest.accounts_count` /
  `code_contracts_count` / `storage_keys_count` were parsed but never checked. A
  truncated or lossy state restore therefore dropped accounts, advanced the head,
  and forward block application layered valid deltas onto the incomplete base, so
  those accounts read too-low forever (this chain commits no state root in its
  headers, so nothing else catches the divergence). Import now counts imported
  entries per type, and a new completeness gate (`_verify_state_import_complete`)
  **aborts before `set_head`** on any dropped entry or any per-type shortfall vs
  the manifest. Backward compatible: pre-count legacy snapshots still import.
  This is the class of bug behind an exchange node reporting balances far below
  the authoritative on-chain values while sitting at the correct head height.
### Changed
- **Pool vardiff bootstrap now anchors at `start_difficulty` instead of the
  min_difficulty floor.** Avoids a burst of floor-difficulty share spam on a fresh
  pool while it converges (the deadlock-at-block-difficulty fix from 5.3.1 stands).

---

## [5.3.1] - 2026-07-03
### Fixed
- **Pool served every miner the full block target as its share target
  (`shareTarget=1.0`), so miners were credited only when they found a whole
  block.** The real root cause of the "shares only credited at full difficulty"
  report was pool-side, not just miner-side: on a fresh pool the global vardiff
  bootstrapped `_current_share_threshold_micro` from the *template's*
  block-difficulty share (`_resolve_share_target`,
  `python/animica/stratum_pool/stratum_server.py`). A block-difficulty share is
  unsubmittable, so no samples ever reached the vardiff, which only steps *down*
  on a low-difficulty-reject streak — the target stayed pinned at the block
  target forever. New pools now bootstrap at an achievable **`start_difficulty`**
  (default `0.01`, env `ANIMICA_STRATUM_START_DIFFICULTY`, clamped into
  `[min_difficulty, max_difficulty]`; new field in `stratum_pool/config.py`) and
  the vardiff ratchets *up* from real accept-rate feedback. A miner's explicitly
  easier request is still honored. Combined with the 5.3.0 miner-side fallback,
  miners now submit sub-block shares immediately and earn steady PPS credit.
- **Explorer address pages hung ~37 s and rendered a blank ("—") balance.**
  `explorer2/api` ran a synchronous 250-block live scan for tx history before
  returning, so a sparse address (e.g. an exchange cold wallet) timed out in the
  browser and the balance never rendered — misread as a missing/wrong balance.
  The balance itself was always correct (fetched live from `state.getBalance`).
  The scan now runs under a wall-clock budget (`EXPLORER_ADDRESS_SCAN_MS`,
  default 3500 ms) so the balance/head return in a couple seconds and deeper
  history paginates via `nextCursor`. (Explorer is deployed separately from the
  pip package.)

---

## [5.3.0] - 2026-07-03
### Fixed
- **Miner only earned credit at full block difficulty.** Both scan drivers
  (`mining/internal_cpu_miner.py`, `mining/hash_search.py`) promoted an
  *undelivered* share target to the FULL block target Θ via
  `if share_ratio <= 0: share_ratio = 1.0`, so the miner searched at Θ and only
  found/submitted full-difficulty blocks — earning no per-share credit. Now falls
  back to an easy sub-Θ ratio (`ANIMICA_MINER_FALLBACK_SHARE_RATIO`, default 0.25)
  and clamps to (0, 1], so it scans at the pool's real share target. (The pool
  already credits every accepted PPS share correctly — verified against the live
  ledger — so this was purely miner-side.)
- **Multi-GPU rigs only used one GPU.** Device selection collapsed to a single
  torch device (`cuda` == ordinal 0). Added `solo_devices_available()` to
  enumerate every connected GPU (`cuda:0..N`, or Metal), and the miner now fans
  the header-template scan out across **all** detected GPUs each tick over
  disjoint nonce sub-bands (an N-GPU rig does ~N× the work; no two GPUs grind the
  same nonces), with graceful CPU fallback only if *every* GPU errors.
- **Pool silently orphaned slow miners ("timeout issue").** On a send that
  couldn't drain within the timeout, `mining/stratum_server.py::_drop_session`
  removed the session but left the TCP socket half-open — the miner got no EOF,
  never reconnected, and stale-ground a dead job forever. `_drop_session` now
  aborts/closes the socket so the miner reconnects, and the send-timeout default
  is raised 15s → 60s (`ANIMICA_STRATUM_SEND_TIMEOUT`).
- **ENA training worker timed out mid-step.** The worker→coordinator HTTP client
  used a flat 30s timeout on every call, including `/jobs/{id}/run`, which can
  block on a real training/generation step for minutes → "timeout on the train
  loop." The default is now a generous 600s, tunable via
  `ANIMICA_ENA_WORKER_HTTP_TIMEOUT`.

## [5.2.8] - 2026-07-03
### Fixed
- **ENA training pools could wedge on a round for days ("rounds / checkpoints
  not advancing").** A pool's round counter would freeze (e.g. round 61 for ~6
  days) because the coordinator's background sweep could never reach the
  aggregate step, and even when it did, a no-adapter round *held forever*:
  - **Sweep readiness** (`pool.py` `sweep`): the `ready` gate required either all
    shards submitted or a submission-time stall — but replicate-mode reshard
    growth keeps spawning open shards, a single drip trainer keeps the stall
    timer fresh, and reclaim churn keeps a live claim present, so neither ever
    fires. Added a **reclaim-churn-immune escape** based on *round age*
    (`now − min(shard.created_at)`, which reclaim/drip cannot reset) gated by a
    submission quorum, so a stuck-but-quorate round aggregates. Tunable per pool
    via `round_quorum_frac` (0.5) and `round_overrun_factor` (2.0).
  - **No-adapter hold is now bounded** (`_aggregate_locked`): if no trainer
    uploads a real adapter, holding is capped at the hard round-timeout; past it
    the round is **reject-and-advanced** (served checkpoint preserved — no
    regression to base — round counter advances, flywheel keeps turning).
- **Payout leak closed.** `payout()` blocked unservable rounds via the display
  list `rejected_rounds`, which is truncated to the last 20 — so advancing a new
  rejected round silently evicted an old one, making its unpaid contributions
  payable. Payout safety now uses a separate **durable, uncapped
  `unservable_rounds` set** (migrating existing entries), independent of the
  display cap. Also hardened a possible `int(None)` crash when
  `round_aggregate_timeout_secs` is explicitly null.
### Notes
- This fixes the **coordinator deadlock and the payout accounting** only. A
  pool's *served* checkpoint still advances only when trainers upload real
  `adapter_model.safetensors` weights that pass the eval gate; a pool whose GPU
  trainers have left will keep cycling rounds (served checkpoint held at the last
  finite round) until adapter-uploading trainers rejoin.

## [5.2.7] - 2026-07-02
### Fixed
- **Nodes could wedge on a local “instant-block tower” and appear to randomly
  reset.** No-PoW *instant* blocks (nonce=0) advance the local head but never
  propagate (peers require valid PoW), so on a networked node they fork the head
  *above* the real chain. The head then reads higher than the network, the node
  believes it is ahead, stops syncing down, and the sync watchdog churns — which
  looks like the head “went backwards / started a new sync and got stuck.” The
  gap equals `head − canonicalHeight` (the non-instant counter). Two sources of
  those blocks are now closed:
  - **Mining:** a non-empty mempool no longer silently downgrades to a no-PoW
    “instant” block that bypassed the sync/offline mining gate — mempool traffic
    mines a **real PoW block** (which propagates and is network-canonical). The
    explicit `instant_block=True` path is unchanged.
  - **`tx send` force-chain** (`ANIMICA_TX_SEND_FORCE_CHAIN=1`): on a node **with
    connected peers**, `tx send` no longer mints a local no-PoW block to “persist”
    the tx (which built the tower one send at a time). The tx is relayed and
    included in a real block instead; the response reports it as pending in the
    mempool. Isolated / single-node setups (no peers) keep the immediate-persist
    behaviour.
### Notes
- **Recovery of an already-wedged node** (head far above `canonicalHeight`) is a
  one-time operation — restore from a current snapshot (or roll the head back to
  the last non-instant height) and let it re-sync the real chain; upgrading alone
  stops the tower from growing but does not abandon an existing one.
- Fully excluding instant blocks from the canonical/fork-choice chain (rather
  than merely not creating them) is a consensus change and must ship behind a
  coordinated, versioned fork — it is intentionally **not** done here, because a
  patch that keyed fork choice on the (grindable) instant-block marker would be
  chain-split-unsafe.

## [Unreleased]
### Added
- **`animica ai` command namespace** (pip `animica` 5.2.0): a first-class AI
  surface — `doctor` (14 readiness checks + fix hints), `setup` (writes
  `~/.animica/config.toml`), `models`, `chat` (REPL + one-shot), `serve` (an
  OpenAI-compatible gateway: `/v1/chat/completions` with streaming, `/completions`,
  `/embeddings`, `/models`, bearer auth, OpenAI error envelope), `embed`, `rag`
  (local index + grounded answers), `job` (estimate/submit/status/result/list on
  the AICF marketplace), `provider` (register/status/start), `earnings`,
  `balance` (`--watch`), and `benchmark`; plus `--no-color` and graceful
  `--json` everywhere. Base install stays light; `serve` lazily needs `animica[backend]`.
  Spending is safe by default — `ai job submit` quotes first and never spends
  without a confirmation or `--yes`, honoring an optional `max_spend_anm` cap. See
  [docs/ai.md](ai.md).
- **`animica up` component selection** — `--profile {all,miner,ai,provider}`,
  repeatable `--only`/`--without`, `--serve-port`, and a richer `--plan` table.
  Fully additive: existing `animica up` invocations are unchanged.
- **`animica mcp install <claude|cursor|vscode>`** — wires the Animica MCP server
  into a client's config (merges, never clobbers; `--print` to preview).
- New **`animica[provider]`** install extra (gateway + on-chain SDK for providers).
- Draft **L2 bundle mode** in Studio Web (behind flag).
- Optional **light client** proof checks in Explorer (feature-gated).

### Fixed (ops)
- **P2P snapshot fast-sync**: snapshot chunks were split at 128 MiB while the P2P
  wire caps a single message at 8 MiB, so a node could never serve its own snapshot
  chunks (`GET_SNAPSHOT_CHUNK` → `payload too large` → silently returned *not found*).
  This broke snapshot-based bootstrap network-wide and left freshly-wiped peers
  unable to fast-sync, wedged at a low height. Lowered `DEFAULT_CHUNK_SIZE` to 7 MiB
  (provably wire-safe: the split rotates before the bound and chunks are compressed)
  and regenerated snapshots. Also fixed the post-create snapshot verifier (a
  `str`-path `/` `TypeError` plus a tuple-vs-dict return mismatch) and corrected the
  stale built-in mainnet block-0 checkpoint to the current genesis.
- **Stratum pool payouts**: per-payout mempool-aware nonce resolution + a unique
  per-payout fee so identical (address, amount) payouts no longer collide to the
  same tx hash and no-op on-chain (the nonce-less execution model meant repeats
  were silently dropped). Recognizes `nonce_gap` for retry.
- **Dependency pin**: `starlette<0.47` (and `sse-starlette<3` in the `mcp` extra)
  to stop a transitive upgrade from breaking every FastAPI service on restart.

### Changed
- Increased default RPC request timeout from **10s → 15s** in SDKs.
- Minor copy and accessibility improvements on website.

### Fixed
- Wallet: resilient reconnect to WS `newHeads` after system sleep.

---

## [0.10.0] — 2025-10-01 — “Install & Ship”
### Added
- **Installers** for Wallet (macOS/Windows/Linux) and **Explorer Desktop (Tauri)**.
- **Auto-update appcasts** (stable/beta) and signing scripts.
- CI pipelines for all desktop targets (notarize/codesign/staple where applicable).

### Changed
- Website **Downloads** page wired to CI artifacts and appcasts.

### Migration
- Mac: ensure Apple API key (App Store Connect) is configured per `installers/signing/macos/*`.
- Windows: import organization code-signing cert and TSA endpoints per docs.

---

## [0.9.0] — 2025-09-10 — “Studio”
### Added
- **Studio Web** (edit → simulate → deploy → verify) with WASM Python VM.
- **Studio Services** (deploy/verify/faucet/artifacts) with strict CORS & rate limits.
- End-to-end examples and Playwright tests.

### Changed
- Shared **chain metadata** now feeds Website + Studio via unified `/chains/`.

### Migration
- Set `PUBLIC_STUDIO_URL`/`PUBLIC_EXPLORER_URL` in `website/.env` for correct deep links.

---

## [0.8.0] — 2025-08-18 — “ZK Arrives”
### Added
- **ZK subsystem**:
  - Verifiers: **Groth16 (BN254)**, **PLONK+KZG (BN254)**, **Poseidon**, **Ate pairing**, **KZG opening**, tiny **STARK/FRI** (toy).
  - **Adapters** for `snarkjs`, `plonkjs`, STARK JSON; **envelope** format; **policy** and **registry** with VK cache.
  - Optional **Rust native** fast paths via `animica_zk_native` (pyo3).
- **Benchmarks** and **tests** for verifiers and VK cache integrity.

### Migration
- Pin VKs in `zk/registry/vk_cache.json` and update signatures when adding circuits.

---

## [0.7.0] — 2025-07-20 — “Wallets”
### Added
- **Browser wallet (MV3)** with PQ keys (Dilithium3/SPHINCS+), connect/send/call.
- **RPC subscriptions** surfaced in wallet UI; simulation helpers.

### Changed
- Address codec consolidated across SDKs and wallet.

### Breaking
- Address bech32m HRP standardized to **`anim`**; previous `animica` HRP deprecated.

---

## [0.6.0] — 2025-06-25 — “Peers & Gossip”
### Added
- **P2P**: PQ handshake (Kyber768 + HKDF), gossip topics (blocks/txs/shares/blobs), header/blocks sync.
- **DoS** protections: per-peer token buckets and scoring.

### Fixed
- Deterministic tie-break in fork-choice under equal weight.

---

## [0.5.0] — 2025-05-30 — “Useful Compute”
### Added
- **AICF** (AI Compute Fund): provider registry, staking, SLA, payouts/slashing.
- **Capabilities**: contract syscalls for `ai_enqueue`, `quantum_enqueue`, `blob_pin`, `zk_verify` (pluggable).
- End-to-end proofs→claims→payouts path.

### Migration
- Configure provider stakes and allowlists before enabling jobs on public nets.

---

## [0.4.0] — 2025-05-05 — “Data Availability”
### Added
- **DA module**: Namespaced Merkle Trees, Reed-Solomon erasure, DAS sampling, retrieval API.
- **Header integration**: DA roots into block headers; light-client verification helpers.

### Changed
- Blob cost/size checks hooked into execution adapters (feature-gated).

---

## [0.3.0] — 2025-04-12 — “Contracts (Py VM)”
### Added
- **Deterministic Python VM**: validator, compiler, IR, runtime, stdlib, gas model.
- **Counter** and **Escrow** examples with tests.
- SDK **contract clients** and **codegen** (TS/Py/Rust).

### Breaking
- ABI encoding switched to canonical length-prefixed form; update custom tools.

---

## [0.2.0] — 2025-03-20 — “RPC + SDK”
### Added
- **JSON-RPC & WS** (FastAPI): params/head/blocks/tx/state/receipts.
- **SDKs**: Python, TypeScript, Rust with wallet/signing, tx build/send, events, DA/Randomness clients.

### Fixed
- Canonical CBOR map ordering in core encoder.

---

## [0.1.0] — 2025-02-28 — “Genesis”
### Added
- **Core**: headers/blocks/state DB; genesis loader; deterministic state root.
- **Consensus**: PoIES scorer & Θ retarget; fork choice.
- **Proofs**: HashShare, AI/Quantum/Storage/VDF skeletons + vectors.
- **Mining**: CPU hash searcher; Stratum & WS getwork.
- **Randomness**: commit→reveal→VDF beacon prototype.
- **Website** scaffold and initial docs.

---

## Changelog Conventions

- **Added / Changed / Fixed / Removed / Security / Deprecated / Breaking / Migration** headings.
- When applicable, include **config keys** and **expected impacts**.
- Prefer **human-oriented** summaries; implementation detail belongs in PRs.

## Release Process (summary)
1. Land changes with tests and docs.
2. Update `docs/TOC.md` if new docs/surfaces appear.
3. Bump versions across touched packages.
4. Tag `vX.Y.Z` and generate release notes from this file.
5. Publish installers/appcasts if desktop apps are affected.

---

[Unreleased]: https://github.com/animica-labs/animica/compare/v0.10.0...HEAD
[0.10.0]: https://github.com/animica-labs/animica/releases/tag/v0.10.0
[0.9.0]: https://github.com/animica-labs/animica/releases/tag/v0.9.0
[0.8.0]: https://github.com/animica-labs/animica/releases/tag/v0.8.0
[0.7.0]: https://github.com/animica-labs/animica/releases/tag/v0.7.0
[0.6.0]: https://github.com/animica-labs/animica/releases/tag/v0.6.0
[0.5.0]: https://github.com/animica-labs/animica/releases/tag/v0.5.0
[0.4.0]: https://github.com/animica-labs/animica/releases/tag/v0.4.0
[0.3.0]: https://github.com/animica-labs/animica/releases/tag/v0.3.0
[0.2.0]: https://github.com/animica-labs/animica/releases/tag/v0.2.0
[0.1.0]: https://github.com/animica-labs/animica/releases/tag/v0.1.0
