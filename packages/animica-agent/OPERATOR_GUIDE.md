# Animica useful-work miner — operator guide

This guide covers running the hardened useful-work subsystem of
`animica-agent`. Skim the **rollout checklist** at the bottom before you
point the daemon at a funded wallet.

## Subsystems

| Subsystem | Purpose | Module |
| --- | --- | --- |
| Job state machine | 11-state lifecycle, JSONL-persisted, restart-recoverable | `job-state.ts` |
| Miner runtime | Polls, runs, retries, idempotent submissions | `miner-runtime.ts` |
| Settlement engine | Submit→confirming→confirmed→paid with depth checks | `settlement-engine.ts` |
| Coordinator (hardened) | Auth, retry classification, offline queue, replay | `coordinator-hardened.ts` |
| Payout policy | Caps, duplicate defense, reserve, audited rejections | `payout-policy.ts` |
| Hybrid scheduler | 5 modes (chain/useful/balanced/miner-priority/useful-priority) | `hybrid-scheduler.ts` |
| Metrics | Counters + on-disk aggregates | `metrics.ts` |
| Journal admin | Compaction, archive, inspection | `journal-admin.ts` |
| Doctor | Aggregate go/no-go check | `uw-doctor.ts` |

## Hybrid mining modes

```
chain-only             block-mining keeps 100% of capacity (useful-work off)
useful-only            useful-work uses 100% of capacity (chain mining must be off)
hybrid-balanced        ≈50/50 split with hard floor of 1 worker each
hybrid-miner-priority  block mining ¾, useful-work ¼; pauses useful-work if hashrate < 80% target
hybrid-useful-priority useful-work majority; block mining keeps 1 worker floor
```

Set indirectly through `resourceMode` in `.animica/agent.json`:

| resourceMode      | observed miner | resulting hybrid mode |
| ----------------- | -------------- | --------------------- |
| `balanced`        | active         | hybrid-balanced       |
| `miner-priority`  | active         | hybrid-miner-priority |
| `agent-priority`  | active         | hybrid-useful-priority|
| (any)             | none           | useful-only           |
| `minerMode=off`   | n/a            | useful-only           |

Inspect the live plan with `animica-agent hybrid plan`.

## Settlement safety model

The settlement engine treats every payout as a state machine:

```
pending_submission → submitted → confirming → confirmed → paid
                       │            │             │
                       │            │             └→ rejected   (chain status=0)
                       │            │                            (tx-dropped/replaced)
                       ├→ failed_transient (will retry next drive)
                       ├→ failed_permanent  (chain mismatch, insufficient balance, ...)
                       └→ expired           (past attemptDeadlineMs)
```

Distinct failure reasons recorded on each attempt:
`rpc-unavailable · signing-failure · nonce-conflict · insufficient-balance ·
invalid-recipient · chain-mismatch · tx-dropped · tx-replaced ·
confirmation-timeout · policy-rejected · duplicate-receipt ·
expired-deadline · unknown`.

`confirmed → paid` only occurs after the configured `confirmationDepth` is
reached. Restart the daemon and the engine resumes from the journal.

## Payout policy

Default config (see `DEFAULT_PAYOUT_POLICY`):

```
dailyCapRaw            10 ANM
perWorkerDailyCapRaw   1 ANM
perAddressDailyCapRaw  1 ANM
reserveBalanceRaw      0.01 ANM
minMaturityMs          60_000  (a receipt must be ≥ 60s old before settle)
duplicateReceiptDefense   true
duplicateArtifactDefense  true
mandatoryArtifactHash     true
enforceOnChainIds       ["1"]
```

Every refusal is journalled to `payout-decisions.jsonl` with a stable
`policyDigest` so you can correlate runtime behavior with the policy that
was active at decision time.

Override via runtime construction:

```ts
import { PolicyPayoutGuard, PayoutAuditor } from "@animica/agent-core";

const auditor = new PayoutAuditor(stateDir);
const guard = new PolicyPayoutGuard(
  { dailyCapRaw: 50_000_000_000_000_000_000n, mandatoryArtifactHash: true },
  auditor,
);
new MinerRuntime(cfg, { coordinator, stateDir, payoutGuard: guard, ... });
```

## Coordinator requirements

The `HardenedCoordinator` calls the following endpoints. Failure-modes are
explicit:

| Endpoint                              | Required shape                                 | On error |
| --                                    | --                                             | --       |
| `GET /health`                         | any 2xx                                        | doctor flags non-OK |
| `GET /jobs`                           | `{ jobs: Job[] }`                              | 5xx → retry, 4xx → permanent, malformed → CoordinatorShapeError |
| `GET /jobs/:id`                       | `Job`                                          | 404 → null; others as above |
| `POST /jobs/:id/submissions`          | `VerificationOutcome`                          | 5xx → enqueue offline if queueDir set |
| `GET /rewards?miner=...&limit=N`      | `{ rewards: Reward[] }`                        | as above |
| `GET /leaderboard?...`                | `{ leaderboard: {minerAddress, score}[] }`     | as above |
| `GET /adapters?model=...`             | `{ adapters: ... }`                            | as above |

Auth: bearer token from `$ANIMICA_AICF_KEY` (override with `authEnv`).
Worker identity: optional `X-Animica-Worker` header.

Validate a coordinator before relying on it:

```
animica-agent coordinator doctor --url https://coordinator.example
```

If the coordinator is briefly unreachable, submissions are queued to
`<stateDir>/submission-queue.jsonl` and replayed on the next iteration.

## Recovery model

| Component                | Persistence file                                | Recovery behavior |
| --                       | --                                              | --                |
| Job state machine        | `<stateDir>/jobs.jsonl`                         | journal replay on restart; `accepted`/`running` reset to `discovered` |
| Settlement engine        | `<stateDir>/settlement.jsonl`                   | latest attempt per receiptId is authoritative; `watch` resumes from this |
| Coordinator queue        | `<stateDir>/submission-queue.jsonl`             | `replayQueue()` on next iteration; CLI: `coordinator queue-replay` |
| Coordinator verifications| `<stateDir>/coordinator-verifications.jsonl`    | append-only audit log of `verify-live` reports |
| Payout audit             | `<stateDir>/payout-decisions.jsonl`             | append-only |
| Receipts                 | `<stateDir>/receipts.jsonl`                     | append-only |
| Hybrid decisions         | `<stateDir>/hybrid-decisions.jsonl`             | append-only |
| Usage journal            | `<stateDir>/usage.jsonl`                        | append-only |

## CLI

```
animica-agent doctor useful-work [--coordinator-url <url>] [--json]
animica-agent hybrid plan [--json]

animica-agent miner start [--once] [--max-jobs N] [--concurrency N] [--idle-ms N]
animica-agent miner stop
animica-agent miner runtime
animica-agent miner status
animica-agent miner connect [<addr>]

animica-agent settlement list [--json]
animica-agent settlement show <receiptId>
animica-agent settlement resume [<receiptId>...] [--depth N] [--json]
animica-agent settlement ready [--json]
animica-agent settlement check [--json]
animica-agent settlement verify-live <receiptId> [--recipient <addr>] [--amount-raw N] [--artifact-hash HEX] [--json]
animica-agent settlement dry-run    <receiptId>  # alias of verify-live
animica-agent settlement submit-live <receiptId> --i-understand-this-spends-real-funds [--depth N] [--json]
animica-agent settlement watch       [<receiptId>...] [--depth N] [--json]
animica-agent confirm <txHash>

animica-agent coordinator verify-live --url <baseUrl> [--submit-fixture] [--json]
animica-agent coordinator fetch-sample --url <baseUrl> [--limit N] [--json]
animica-agent coordinator submit-fixture --url <baseUrl> --job-id <id> [--json]
animica-agent coordinator queue [--dir <path>] [--json]
animica-agent coordinator queue-replay --url <baseUrl> [--dir <path>] [--json]
animica-agent coordinator history [--limit N] [--json]

animica-agent useful-work readiness [--coordinator-url <url>] [--explain] [--json]

animica-agent journal compact [--settlements] [--jobs] [--json]
animica-agent journal archive --older-than <ms> [--reason <substring>] [--json]
animica-agent journal inspect [--json]

animica-agent metrics [--json]
animica-agent coordinator doctor --url <baseUrl> [--json]
animica-agent payout audit [--limit N] [--json]
```

## Journal compaction / prune procedures

The miner appends to every journal. Compaction is **safe** to run while
the daemon is idle:

```
animica-agent miner stop
animica-agent journal compact            # collapses jobs.jsonl + settlement.jsonl
animica-agent journal archive --older-than 604800000 --reason permanent
animica-agent miner start
```

Compaction writes to a temp file and atomic-renames it; if interrupted,
the original file remains intact.

## Failure-mode checklist

| Symptom                                      | Likely cause                                | Check |
| --                                           | --                                          | --    |
| No jobs ever discovered                      | coordinator misconfigured                   | `coordinator doctor` |
| Jobs reach `failed` with `payout policy`     | payout guard rejected                       | `payout audit` |
| Jobs reach `failed` with `unsupported`       | job kind not supported by the local runner  | inspect the job manifest |
| Settlements stall at `confirming`            | node behind / depth not reached             | `settlement show <id>` + `confirm <tx>` |
| Settlements stall at `submitted`             | tx hash never seen on chain                 | check signer + RPC |
| Repeated `failed_transient`                  | RPC unreachable                             | `doctor useful-work` |
| Journal grows without compaction             | normal — run `journal compact` periodically |

## Live rollout checklist

**DO NOT run on funded mainnet until ALL of the following are configured.**

1. `chainId` in `.animica/agent.json` matches the target chain.
2. `minerAddress` is your real Animica payout address.
3. `workerName` is set (used for per-worker caps).
4. A `BalanceAwarePayoutGuard` is constructed (or a `PolicyPayoutGuard`
   with `signerBalance` threaded explicitly) with caps appropriate for
   your operation:
   - `dailyCapRaw` — your willing daily exposure
   - `perWorkerDailyCapRaw` — single-worker cap
   - `perAddressDailyCapRaw` — single-recipient cap
   - `reserveBalanceRaw` — minimum signer balance to preserve
   - `minMaturityMs` — non-zero so a flooded coordinator can't trigger immediate payouts
5. `mandatoryArtifactHash: true` (default), `duplicateReceiptDefense: true`,
   `duplicateArtifactDefense: true`.
6. `coordinator verify-live --url <url>` returns `ok: true` against your
   AICF endpoint, and the persisted report in
   `<stateDir>/coordinator-verifications.jsonl` shows no error-level checks.
7. `doctor useful-work` returns `ok: true`.
8. `useful-work readiness --explain` returns `ok: true` (warnings allowed).
9. A small-amount **test run** has been executed against a testnet wallet
   via `settlement verify-live` (must say `GO`) followed by
   `settlement submit-live <receiptId> --i-understand-this-spends-real-funds`;
   `settlement watch` eventually classifies the attempt as `paid` and
   `settlement show` shows a real tx hash.
10. Journal files exist and are writable by the daemon user.
11. SIGINT handling has been verified by running `miner start` and then
    `miner stop`; the daemon must drain in-flight work and exit cleanly.

## Configuration reference

These are the `.animica/agent.json` fields relevant to useful-work:

```
{
  "rpcUrl":           "http://127.0.0.1:8545/rpc",
  "chainId":          "1",
  "minerMode":        "local",
  "minerAddress":     "anm1...",
  "workerName":       "rig-01",
  "resourceMode":     "miner-priority",
  "creditsMode":      "off",
  "aicfMode":         "off",
  "provider":         "offline",
  "providerBaseUrl":  "https://coordinator.example"
}
```

Useful env vars (read, never written):

- `ANIMICA_AICF_KEY`            — bearer token for HardenedCoordinator
- `ANIMICA_AGENT_RPC_URL`       — override RPC URL
- `ANIMICA_AGENT_RESOURCE_MODE` — override resourceMode
- `ANIMICA_MINER_TARGET_HASHRATE` — used by `hybrid-miner-priority`
- `ANIMICA_MINER_*`             — Animica mining runtime (probed only)

## Automatic signer-balance enforcement

The `BalanceAwarePayoutGuard` fetches the signer's on-chain balance from the
configured Animica RPC **before every payout decision** and feeds it into the
policy evaluator so `reserveBalanceRaw` is enforced by default. There is no
longer a need to thread `signerBalance` through the call site.

```ts
import {
  BalanceAwarePayoutGuard,
  PayoutAuditor,
  RpcBalanceProvider,
} from "@animica/agent-core";

const auditor = new PayoutAuditor(stateDir);
const balanceProvider = new RpcBalanceProvider({
  rpcUrl: cfg.rpcUrl,
  expectedChainId: cfg.chainId,
  cacheTtlMs: 10_000,    // re-fetch balance after 10s
});
const guard = new BalanceAwarePayoutGuard({
  signerAddress: cfg.minerAddress!,
  balanceProvider,
  cfg: { reserveBalanceRaw: 10_000_000_000_000_000n }, // 0.01 ANM reserve
  auditor,
});
new MinerRuntime(cfg, { coordinator, stateDir, payoutGuard: guard, ... });
```

Failure modes (each emits a journaled rejection in `payout-decisions.jsonl`):

| Failure                          | PayoutRejectionReason         |
| ---                              | ---                           |
| signer address missing/invalid   | `tampered-attempt` / `config-missing` |
| RPC unreachable                  | `reserve-balance-violation` (fails closed) |
| chain id mismatch                | `tampered-attempt`            |
| balance reply malformed          | `tampered-attempt`            |
| balance < reserve                | `reserve-balance-violation`   |

Cache TTL: default 10s. Successful payouts invalidate the cache so the next
decision always sees a fresh balance.

## Live settlement verification workflow

Three operator-visible phases, each safe to run repeatedly:

```
# 1. Dry-run only. Never touches the signer.
animica-agent settlement verify-live <receiptId>

# 2. Broadcast. Requires explicit operator acknowledgement.
animica-agent settlement submit-live <receiptId> \
    --i-understand-this-spends-real-funds

# 3. Resume / track. Drives in-flight attempts forward one step at a time.
animica-agent settlement watch [<receiptId> ...]
```

`verify-live` runs:
- signer + recipient address shape check
- amount > 0 check
- chain id match
- live balance lookup + sufficiency check
- payout-policy dry-run (if `--policy` is wired by the caller)
- idempotency guard against a prior `paid`/`confirmed`/`rejected` attempt

`submit-live` refuses unless `--i-understand-this-spends-real-funds` is set
(the literal token is `I-UNDERSTAND-THIS-SPENDS-REAL-FUNDS`). It persists a
`pending_submission` record before calling the signer and reaches `paid` only
through the `pending_submission → submitted → confirming → confirmed → paid`
ordering enforced by the SettlementEngine.

`watch` is read-only by default (no re-broadcast). Each in-flight attempt is
classified as `paid | still-confirming | stuck-pending | dropped | replaced |
rejected | expired | failed` so an oncall can route quickly.

Recovery flows:
- **Stuck `confirming`**: `settlement reconcile <id>` (read-only) or
  `settlement watch <id>` repeatedly; if the head moves past
  `blockNumber + confirmationDepth` and status is still `confirming`, inspect
  with `settlement inspect <id>` for the full transition history.
- **`failed_transient` with `tx-dropped` / `tx-replaced`**: the receipt is
  eligible for a fresh `verify-live` / `submit-live` cycle. Run
  `settlement reconcile --rebroadcast <id>` to drive it forward (this is the
  *only* way reconcile re-broadcasts; otherwise it never calls the signer).
- **`failed_permanent`**: the receipt is locked. `payout audit` shows the
  decision; if the cause is environmental (chain id mismatch, recipient
  typo, …), fix and run `settlement verify-live` again — a new receipt id
  is required to retry.
- **Process crashed mid-broadcast**: every transition is persisted **before**
  it is observable. Restart the daemon, then run `settlement reconcile` to
  walk the journal. An attempt persisted as `pending_submission` (no signer
  call yet) is surfaced as `stuck-pending`. An attempt persisted as
  `submitted` / `confirming` (tx hash on chain, awaiting confirmations) is
  surfaced as `broadcast_pending_confirmation` and the poller continues
  watching it. There is no synthetic `paid` state.

### Settlement subcommand index

```
animica-agent settlement verify-live <receiptId>
animica-agent settlement submit-live <receiptId> --i-understand-this-spends-real-funds
                                                  [--require-fresh-coordinator]
                                                  [--freshness-window-ms N]
animica-agent settlement pending           # non-terminal attempts only
animica-agent settlement inspect <id>      # full transition history
animica-agent settlement reconcile [<id>...] [--rebroadcast]
                                            # walks journal, drives to terminal-or-stall
animica-agent settlement watch [<id>...]   # one-pass driver, read-only by default
```

## Coordinator verify-live workflow

The hardened coordinator is unit-tested against shimmed `fetch`. For a real
AICF endpoint, use the live verify command, which persists each report to
`<stateDir>/coordinator-verifications.jsonl`:

```
# Set the auth token (or set authEnv to point at a different env var)
export ANIMICA_AICF_KEY=…

# Full verification: doctor + handshake + sample fetch + queue self-test.
animica-agent coordinator verify-live --url https://coordinator.example

# Optional fixture submission round-trip (uses a 0-byte artifact hash).
animica-agent coordinator verify-live --url … --submit-fixture

# Lighter read-only smoke check.
animica-agent coordinator fetch-sample --url https://coordinator.example --limit 5

# Inspect and replay the offline submission queue.
animica-agent coordinator queue
animica-agent coordinator queue-replay --url https://coordinator.example

# Review past verification reports.
animica-agent coordinator history --limit 10
```

`verify-live` is **fail-closed**: any check failing at error level produces
`ok: false` and a non-zero exit code. Audit trail lives in
`<stateDir>/coordinator-verifications.jsonl`.

Recovery flows:
- **Malformed endpoint**: doctor will say which check failed (health, jobs
  schema, get-job round-trip). Fix the upstream or switch endpoints; verify
  again. The local client refuses to proceed.
- **Auth refused (401/403)**: `coordinator.auth` failure. Set `ANIMICA_AICF_KEY`
  or pass a custom `authEnv` and re-run verify-live.

## Coordinator freshness gate

`coordinator verify-live` reports are journaled to
`<stateDir>/coordinator-verifications.jsonl`. The freshness gate refuses live
submits unless a recent successful verification exists:

```
animica-agent coordinator latest                  # show the most recent verdict
animica-agent coordinator freshness               # exit 0 if fresh, 1 if stale
animica-agent coordinator freshness --window-ms 3600000

# Refuse live submit unless a fresh verify-live exists:
animica-agent settlement submit-live <id> \
    --i-understand-this-spends-real-funds \
    --require-fresh-coordinator
```

Freshness semantics: the **latest** report (any verdict) must be `ok=true`
AND younger than `windowMs` (default 24h). A later failed report
invalidates an earlier successful one — a regression must trigger a re-verify
before live submits resume.

## Operator readiness aggregator

```
animica-agent useful-work readiness [--coordinator-url <url>] [--explain] [--json]
```

Single go/no-go aggregator. Rolls up:
- wallet identity is resolvable
- live balance lookup succeeds + is above reserve
- coordinator (when configured) doctor passes
- journal sizes / ages are within bounds
- settlement queue has no abandoned in-flight attempts
- hybrid plan produces a non-`chain-only` plan
- state dir exists and is writable

`--explain` adds a per-failure remediation table from `READINESS_FAILURE_GUIDE`.
Exit code is `1` if any error-level check fails (warnings do **not** flip ok).

## Pre-live-payout gate: `useful-work go-live`

The strictest gate, intended as the final check before flipping the daemon to
live mode. Returns non-zero on **any** error-level failure.

```
animica-agent useful-work go-live [--coordinator-url <url>] [--json]
```

Validates:
- coordinator verify-live report exists and is fresh
- wallet/signer identity is configured and valid
- settlementMode=live AND reservePolicy is not disabled
- chain RPC is reachable and chain id matches config
- signer balance ≥ policy reserve
- state dir is writable

Each failed check carries a `fix` hint. Exit code is the gate.

## Status snapshot

```
animica-agent useful-work snapshot [--json]
```

Compact, machine-readable status object suitable for dashboards and CI:
counters, in-flight settlements, journal state, coordinator freshness,
queue depth, settlement mode, reserve policy.

## Automatic settlement-mode wiring

Two config fields drive the production runtime:

| Field            | Values                | Default                                |
| ---              | ---                   | ---                                    |
| `settlementMode` | `offline` \| `live`   | `offline`                              |
| `reservePolicy`  | `strict` \| `off`     | `strict` when `settlementMode=live`, `off` otherwise |

When `settlementMode=live` AND `reservePolicy=strict` AND `minerAddress` is set
AND no explicit `payoutGuard` was passed to `MinerRuntime`, the runtime
**auto-constructs a `BalanceAwarePayoutGuard`** using an `RpcBalanceProvider`
bound to the configured RPC. There is no silent fallback: a balance lookup
failure produces an audited `reserve-balance-violation` rejection and the job
ends as `failed_permanent`.

To keep legacy / offline behavior, leave `settlementMode=offline`. To use live
mode with explicit reserve enforcement disabled (dev/test only), set
`reservePolicy=off`.

Environment overrides:
- `ANIMICA_AGENT_SETTLEMENT_MODE` — `offline` | `live`
- `ANIMICA_AGENT_RESERVE_POLICY`  — `strict` | `off`

## What is still operator-controlled by design

The following are *intentionally* not automatic. They require an operator
decision and explicit action — not because the code is incomplete, but
because acting on funded wallets without a human in the loop is a
deliberate non-goal of this subsystem.

1. **The very first live payout.** `submit-live` will not proceed without
   `--i-understand-this-spends-real-funds`. The flag is the gate; there is no
   way to make the runtime issue a live transaction without it.
2. **Pointing the tool at a real AICF coordinator.** The unit tests cover
   every classification (auth, 5xx, malformed, queue + replay). The first
   round-trip against a real endpoint must be made by an operator running
   `coordinator verify-live --url <url>`; the runtime can require freshness
   but cannot create freshness.
3. **Switching settlementMode from `offline` to `live`.** This is a config
   change. The runtime never silently flips it for you. `go-live` is the
   recommended pre-flight before making this change.
4. **Reservation top-ups when the signer balance falls below reserve.**
   Live submits will refuse cleanly; no balance auto-management happens.
5. **Mid-flight rebroadcasts (`reconcile --rebroadcast`, `settlement watch`
   with a signer).** Re-submitting a `failed_transient` is an operator
   choice; reconcile is read-only by default for exactly this reason.
6. **Long-running confirmation watches.** The CLI runs one pass and exits.
   A daemonized watcher is left to ops tooling (systemd, k8s, …).

## Recipes

**Offline dev mode** — receipts settle locally; no chain interaction:
```
settlementMode = "offline"     # in .animica/agent.json
reservePolicy  = "off"
```

**Live payout mode (default reserve enforcement)**:
```
settlementMode = "live"
# reservePolicy omitted → strict by default
```

**Live with coordinator freshness gate**:
```
animica-agent coordinator verify-live --url https://coordinator.example
animica-agent settlement submit-live <id> \
    --i-understand-this-spends-real-funds \
    --require-fresh-coordinator
```

**Replay / reconcile after process crash**:
```
animica-agent settlement pending          # see what's stuck
animica-agent settlement inspect <id>     # see exact transition history
animica-agent settlement reconcile        # read-only sweep
animica-agent settlement reconcile <id> --rebroadcast   # force a retry
```

## What this guide does NOT promise

- **No live mainnet payout has been demonstrated.** The full live workflow
  (`verify-live → submit-live → watch`) is unit-tested against a deterministic
  signer + fake poller. The first run against a funded wallet must be done by
  an operator following the rollout checklist + live verification commands.
- **No real AICF coordinator has been exercised by this work.** `verify-live`
  ships as the operator-runnable tool that exercises the contract; the unit
  tests cover doctor + auth + schema + queue against an in-process fake.
  Operators must run `animica-agent coordinator verify-live --url <url>`
  against their real coordinator and record the persisted report before
  trusting that endpoint.
- **BalanceAwarePayoutGuard is opt-in.** The default runtime continues to
  accept the simpler `PolicyPayoutGuard`; wiring `BalanceAwarePayoutGuard`
  is what activates automatic on-chain balance enforcement.
