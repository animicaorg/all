# ENA CLI Reference

## Command Group

All ENA commands are under the `animica ena` subcommand:

```bash
animica ena --help
```

---

## `animica ena call`

Submit an ENA inference request and optionally wait for the result.

```bash
animica ena call \
  --model ena-v0.9.0-h10000 \
  --task classify \
  --input "Is this spam?" \
  --fee-limit 0.00001 \
  [--wait] \
  [--json] \
  [--rpc-url http://127.0.0.1:8545/rpc]
```

**Options:**
- `--model TEXT` — Model version (required, or use default)
- `--task TEXT` — Task type: classify, embed, summarize, custom (required)
- `--input TEXT` — Input text (use `--file` for binary input)
- `--file PATH` — Read input from file
- `--fee-limit FLOAT` — Max ANM fee (default: network minimum)
- `--wait` — Poll until result is available
- `--json` — Output JSON
- `--rpc-url TEXT` — Override RPC endpoint

**Example output:**
```
ENA Request Submitted
  Request ID: ena-a1b2c3d4e5f6...
  Status: queued
  Model: ena-v0.9.0-h10000
  Task: classify
  Fee locked: 0.00001 ANM
```

With `--wait`:
```
ENA Request Submitted
  Request ID: ena-a1b2c3d4e5f6...
Waiting for result...
  ✓ Status: completed
  Result hash: sha3hex...
  DA pointer: da:abc123...
```

---

## `animica ena status <request_id>`

Get the status of a previously submitted ENA request.

```bash
animica ena status ena-a1b2c3d4e5f6...
```

**Output:**
```
Request: ena-a1b2c3d4e5f6...
Status: completed
Model: ena-v0.9.0-h10000
Task: classify
Created at height: 10050
Expiry height: 11490
```

---

## `animica ena result <request_id>`

Get the result of a completed ENA request.

```bash
animica ena result ena-a1b2c3d4e5f6...
animica ena result ena-a1b2c3d4e5f6... --raw
animica ena result ena-a1b2c3d4e5f6... --json
```

**Options:**
- `--raw` — Print raw result bytes
- `--json` — Output as JSON

**Output:**
```
Result for: ena-a1b2c3d4e5f6...
  Status: completed
  Result hash: sha3hex...
  DA pointer: da:abc123...
  Worker: provider-0x1234
  Accepted at height: 10051
```

---

## `animica ena models`

List available ENA model versions.

```bash
animica ena models
animica ena models --json
```

**Output:**
```
ENA Model Versions
┌────────────────────────┬────────────┬────────┬─────────────┐
│ Version                │ Activation │ Status │ DA Pointer  │
├────────────────────────┼────────────┼────────┼─────────────┤
│ ena-v0.9.0-h10000      │ 10000      │ active │ da:abc123.. │
│ ena-v0.8.0-h0          │ 0          │ depr.  │ da:old123.. │
└────────────────────────┴────────────┴────────┴─────────────┘
Active model: ena-v0.9.0-h10000
```

---

## `animica ena model show <version>`

Show details for a specific ENA model version.

```bash
animica ena model show ena-v0.9.0-h10000
```

**Output:**
```
ENA Model: ena-v0.9.0-h10000
  Activation height: 10000
  Status: active
  DA pointer: da:abc123...
  Metadata hash: da:meta456...
```

---

## `animica ena fees`

Show the current ENA fee schedule.

```bash
animica ena fees
```

**Output:**
```
ENA Fee Schedule
  Base fee (nano-units): 10000
  Provider share: 60% (6000 bps)
  AICF pool share: 30% (3000 bps)
  Treasury share: 10% (1000 bps)
  Failure/expiry slash: 1% to AICF, 99% refund

Example (fee_limit=10000 nano-units):
  → Provider: 6000 nano-units
  → AICF: 3000 nano-units
  → Treasury: 1000 nano-units
```

---

## `animica ena explain <request_id>`

Debug tool: explain the status or rejection reason for a request.

```bash
animica ena explain ena-a1b2c3d4e5f6...
```

**Output:**
```
ENA Request Explanation
  Request ID: ena-a1b2c3d4e5f6...
  Model: ena-v0.9.0-h10000
  Task: classify
  Policy check: ✓ PASS

  Status: queued
  Reason: Waiting for worker assignment
  Expiry in: 1389 blocks (~3.9 hours)
```

---

## Global Options

All `animica ena` commands support:

- `--rpc-url TEXT` — Override the RPC endpoint URL (default: `ANIMICA_RPC_URL` env or mainnet)
- `--json` — Output structured JSON for piping/scripting
- `--verbose` — Enable verbose logging

---

## Environment Variables

| Variable           | Description                                      |
|--------------------|--------------------------------------------------|
| `ANIMICA_RPC_URL`  | JSON-RPC endpoint (e.g., `http://127.0.0.1:8545/rpc`) |
| `ANIMICA_NETWORK`  | Network profile (local-devnet, devnet, mainnet)  |
| `ENA_ENDPOINT`     | Direct ENA service endpoint (for local inference)|

---

## See Also

- [rpc.md](rpc.md) — Underlying RPC methods
- [contract-integration.md](contract-integration.md) — Contract usage
- [operator.md](operator.md) — Operator guide
