# Animica — Changelog
All notable **user-facing** changes to this repository will be documented here.

This project adheres to **[Semantic Versioning](https://semver.org/)** and follows the **Keep a Changelog** format. Dates are `YYYY-MM-DD`.  
Module-scoped, low-level tweaks that don’t affect the user experience live in per-module CHANGELOGs or commit history.

> Tip: For upgrade steps, search for **Migration** blocks and **Breaking** notes.

---

## [6.0.3] - 2026-07-07
P2P peering + ENA trainer reliability fixes. **No consensus change** — nothing here touches
block, state, or transaction validation, so it is fully compatible with 6.0.0/6.0.1/6.0.2
and needs no coordinated upgrade.

- **P2P: nodes behind Docker/NAT no longer blackhole inbound peers.** When a node runs
  behind `docker-proxy` (or any SNAT), every external peer arrives under a single private
  bridge-gateway IP. The per-IP inbound-connection cap and the per-IP handshake-rate limiter
  then throttled *the entire internet* as one host and, once exhausted, closed the socket
  before writing any handshake byte — so a dialing peer saw `HandshakeError: 0 bytes read on
  a total of 18 expected bytes` and never connected (symptom: "live peers stay 0").
  - New `_is_nat_collapsed_host()` exempts private, non-loopback/link-local sources from the
    **per-IP** connection and handshake-rate caps (they fall through to the global cap only);
    genuine distinct public IPs stay rate-limited as before.
  - Configured seed/verifier hosts are now plumbed into the transport and peer registry as
    **trusted hosts** and are cap-exempt.
  - The global inbound cap default is raised (`ANIMICA_P2P_INBOUND_CONN_GLOBAL_PER_MIN`
    40 → 300) so a healthy seed can sustain real peer volume.
  - On rejection the node now sends a best-effort **handshake reject frame**
    (`ANIMICA/TCP/RJ/V0`), and the dialer reports a clear reason ("peer rejected connection:
    inbound_capped" / "peer closed before handshake (connection refused or inbound-capped)")
    instead of an opaque byte count. Fully backward-compatible with older peers.
- **ENA: fix the trainer crash on newer Python / HuggingFace `datasets`.** `Dataset.from_list`
  fingerprint-hashing raised `Pickler._batch_setitems() takes 2 positional arguments but 3
  were given` on Python 3.13+ with an old `datasets` (its `_dill.Pickler` used the pre-3.13
  signature). Fixed by raising the `datasets` floor to `>=3.0.0` (and pinning `dill>=0.3.8`)
  **and** installing an idempotent runtime compat-shim in `_require_transformers()` so already
  provisioned environments with a transitively-pinned old `datasets` also work.
- **ENA: graceful CUDA-fault handling.** `trainer.train()` is now wrapped so a
  `CUDA error: an illegal memory access`/device-side assert synchronizes, clears the cache,
  and fails just that round (`TrainingError`) instead of killing the whole worker. Opt-in
  `ANIMICA_ENA_CUDA_DEBUG=1` sets `CUDA_LAUNCH_BLOCKING`/`TORCH_USE_CUDA_DSA` for accurate
  diagnosis.
- **ENA: worker survives transient coordinator/RPC 503s.** The remote client now retries
  only transient failures (429/502/503/504, connection/timeout) with bounded jittered
  backoff (`ANIMICA_ENA_WORKER_HTTP_RETRIES`, `ANIMICA_ENA_WORKER_HTTP_BACKOFF`), failing
  fast on real 4xx.

## [6.0.2] - 2026-07-07
Node liveness + trainer stability fixes. **No consensus change** — nothing here touches
block, state, or transaction validation, so it is fully compatible with 6.0.0/6.0.1 and
needs no coordinated upgrade.

- **P2P: fix an event-loop wedge that could stall block production.** Two independent DoS
  vectors let peer traffic starve the node's single async event loop (silently — the node
  keeps a stale head while `getblock*`/`getbalance` time out):
  - **GET_HEADERS anchor scan was O(chain height).** `_locate_anchor` walked *every* block
    height from the head down to genesis doing one synchronous DB read each, so a peer
    sending a header locator full of unknown/foreign-chain hashes forced tens of thousands
    of blocking reads per request (worsening as the chain grows). It now walks the peer's
    (bounded) locator instead — at most one lookup + one canonical check per entry — with
    identical semantics.
  - **Peer banning was disabled.** Provably-incompatible peers (`wrong_genesis`/`wrong_chain`)
    were penalised and dropped but never banned, so they reconnected in a hot loop and
    re-ran the CPU-heavy pure-Python AEAD handshake forever. Banning is now enabled
    (env-gated, default on: `ANIMICA_P2P_BAN_ENABLED=0` to disable), and the TCP transport
    now rejects flooding hosts with a pre-handshake sliding-window rate limit (global +
    per-host; tunable via `ANIMICA_P2P_INBOUND_CONN_GLOBAL_PER_MIN` /
    `ANIMICA_P2P_INBOUND_CONN_PER_MIN`) so a connection flood can't saturate the loop.
- **ENA training: fix `CUDA error: an illegal memory access` on GPU train shards.** SFT now
  (1) resizes token embeddings to cover the tokenizer so an out-of-range id can't index off
  the embedding table, (2) clamps `max_seq_len` to the model's `max_position_embeddings` so
  a long row can't overrun the position table, and (3) drops any row carrying an out-of-vocab
  token id as a final guard.

## [6.0.1] - 2026-07-05
Sets the mainnet consensus-activation height to the coordinated value **40,000** (6.0.0
shipped a placeholder 100,000). At this height upgraded nodes begin enforcing the 6.0.0
consensus rules (ml_dsa_65 signature verification, txsRoot/proofsRoot commitment,
deterministic emission). **Every node must run 6.0.1 (or set
`ANIMICA_FORK_PQ_HARDENING_HEIGHT` / `ANIMICA_FORK_ROOT_COMMITMENT_HEIGHT=40000`) before
height 40,000** — the activation height is a network-wide consensus parameter and must be
identical everywhere. Normal empty/coinbase-only zero-root blocks pass the gates at
activation, so honest miners are not forked; only rule-violating blocks are rejected. The
change is P2P-transparent (the mainnet params-hash is pinned), so 6.0.1, 6.0.0, and legacy
nodes all continue to peer during the runway.

## [6.0.0] - 2026-07-04
Security & consensus hardening release. Closes the findings in the internal
Security & Consensus Findings Report. **Every consensus change is forward-only and
height-gated** (mainnet activation H=37000, tunable via `ANIMICA_FORK_*_HEIGHT`),
grandfathering all existing history — **no genesis reset, no hard fork**, and no
legitimately-mined historical block is rejected. Node-local hardening (RPC, P2P,
snapshot, wallet, mempool) is always-on and independent of the activation height.

### Migration
- **Nodes and the pool/miner must upgrade before H=37000.** At that height,
  upgraded nodes begin enforcing mandatory transaction-signature verification
  (ml_dsa_65 / alg 0x1003 only), txsRoot/proofsRoot commitment, and deterministic
  emission. A node that upgrades while the network does not — or that still accepts
  stub-scheme (0x1001/0x1002) transactions — will orphan non-compliant blocks.
- **RPC:** sensitive methods can now require a bearer token
  (`ANIMICA_RPC_AUTH_TOKEN`) and be denylisted (`ANIMICA_RPC_RESTRICT_SENSITIVE`);
  CORS-credentials no longer defaults open.
- **Wallet:** `wallets.json` is encrypted at rest when a passphrase is provided
  (`animica wallet create --password`, `ANIMICA_WALLET_PASSPHRASE[_FILE]`, or
  `animica wallet encrypt`); plaintext stores still load (with a warning).
- **Mempool** now enforces a min-fee floor, per-sender caps, funded-sender and
  mandatory signature verification on mainnet (all env-tunable).

### Fixed — Critical
- **Forgeable post-quantum signature schemes (ANM-C01).** Schemes 1 (dilithium3)
  and 2 (sphincs) verified via a public hash with no secret, so anyone with a
  public key could forge a valid signature and — because accounts are keyed by
  `sha3_256(pubkey)` with the alg id stripped — drain the victim's real account.
  Only the FIPS-204 scheme ml_dsa_65 (0x1003) is now accepted; the stubs are
  rejected at both signature stacks.
- **Block import applied balances with no signature verification (ANM-C02).** A
  hostile miner could include unsigned/forged transfers. Import now verifies every
  transaction signature (height-gated, fail-closed).
- **Contract `exec()` remote code execution + unbounded-loop DoS (ANM-C05/C06).**
  The raw-`exec()` contract path is fail-closed by default; contracts REVERT.
- **Header roots never validated on import (ANM-C03).** A PoW-valid header could
  carry a different transaction or proof set and diverge nodes silently. txsRoot
  and proofsRoot are now verified on import (self-gating, grandfathered); the
  post-execution state root is computed and shadow-logged for a later enforcement.
- **Plaintext wallet private keys (ANM-C07).** At-rest AES-256-GCM encryption.
- **Unauthenticated RPC + snapshot import path traversal / silent divergence
  (ANM-C08/C10/C11).** Opt-in RPC auth; snapshot path validation, caps, and a
  completeness gate before head is set.

### Fixed — High / Medium / Low (selected)
- Deterministic difficulty (θ) warmup independent of node uptime (ANM-H01).
- Exact-integer block emission — proven value-preserving — removing the float
  determinism hazard; reward computation fails closed (ANM-H08/M04).
- Mempool: fee floor, per-sender caps, nonce-aware eviction, mandatory
  signatures, block-mineability bounds (ANM-H07/H10/M08/M09).
- Canonical transaction id + strict-minimal CBOR (gated) closing txid
  malleability (ANM-H09/M10/M05/L07).
- P2P: bounded decompression + per-peer rate limiting; refuse insecure AEAD
  downgrade (ANM-H03/H04/L01).
- Deterministic error codes in consensus-hashed logs (ANM-L02); secure wallet
  scheme defaults + mainnet insecure-keygen guard (ANM-M07); genesis alloc-cap
  enforced for new networks (ANM-M04).

### Known limitations (tracked, not shipped in 6.0.0)
- Full PoIES useful-work verification (ANM-C04) requires building the proof
  verifier package and activating PoIES as a coordinated protocol upgrade; only
  the proofsRoot commitment is enforced here.
- P2P handshake authentication (ANM-C09/C10) is deferred to an opt-in negotiated
  migration to avoid orphaning current peers.

---

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
