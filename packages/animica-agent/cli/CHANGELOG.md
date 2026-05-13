# Changelog

## 0.1.0 — initial release

- npm-installable `animica-agent` CLI with `init`, `doctor`, `status`, `chat`,
  `code`, `diff`, `apply`, `rollback`, `patches`.
- RPC pass-through with BigInt-safe encoding.
- Wallet identity resolution + read-only balance via local node.
- Miner-aware operations: `miner connect`, `miner status`, resource modes,
  eligibility & subsidy hooks.
- Billing engine: pricing table, session/daily/monthly budgets, allowances,
  signed receipts (offline + node settlement).
- Useful-work job board (local + HTTP coordinator) with submission, rewards,
  leaderboard, adapters.
- Scaffolding for `contract`, `dapp`, `token`, `aicf-agent`.
- Local browser dashboard via `animica-agent ui`.
- 52-test vitest suite + Python-CLI regression smoke.
