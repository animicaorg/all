# Master Release Plan

Date: 2026-04-07

## Release Goal

Drive the monorepo to a defensible RC1 state where backend truth, wallet truth, explorer truth, and operator tooling agree with each other.

## Phase Plan

1. Foundational truth
   Exit gate: setup works, focused backend tests pass, Docker packaging is sane, status surfaces stop lying.
2. Chain coherence
   Exit gate: leader/follower sync converges, mempool and mining include eligible transactions, CLI and RPC agree.
3. User-critical surfaces
   Exit gate: explorer, wallet, wallet extension, and wallet-qt reflect actual chain state and build cleanly.
4. Product surfaces
   Exit gate: studio, miner GUIs, websites, and admin panels stop being shells and pass focused smoke checks.
5. Advanced platform
   Exit gate: AICF, ENA, useful-work, and DA flows expose real executable paths instead of disconnected stubs.
6. Exchange and token ecosystem
   Exit gate: CEX, DEX, token launch, listing, and ANM pairing flows have coherent build, service, and test boundaries.
7. Release hardening
   Exit gate: smoke scripts, docs, scorecards, and release checklist match current executable reality.

## Immediate Next Loops

1. Explorer loop
   Reproduce the runtime mismatch between `ops/docker/entrypoints/explorer.sh` and the actual explorer code.
   Either wire it to a real app entrypoint or retire the stale path.
2. Studio loop
   Reproduce `studio-web` provider failures and decide whether code or tests are stale.
3. Admin/CEX loop
   Fix `apps/admin-web` typing drift and get `cex/tests/e2e` to a buildable state.
4. Sync e2e loop
   Add a real two-node convergence smoke, not only CLI and supervisor tests.
5. Wallet extension loop
   Reproduce current raw transaction submission failures against the current RPC client and backend contract.

## Operating Rules

- Do not trust markdown summaries unless backed by a command, test, or direct code path.
- Prefer fixing behavior before rewriting docs.
- Any subsystem left incomplete must have a concrete blocker and next step, not a vague TODO.
- Smoke scripts must run real commands even when they currently fail.
