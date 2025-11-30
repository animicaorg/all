# Copilot Instructions for Animica

This is a **monorepo** hosting a blockchain (PoIES consensus), execution layer (Python-VM), crypto infrastructure, SDKs (Python/TypeScript/Rust), and multiple UIs. Use this guide to navigate code patterns and workflows quickly.

## Architecture Overview

**Core layers:**
1. **Consensus** (`consensus/`) — PoIES (Proof-of-Integrated-External-Services) accepts blocks using a deterministic score combining hash-share work + external proofs (AI, Quantum, Storage). Uses fixed-point μ-nats math, nullifier windows, and EMA-based difficulty retargeting.
2. **Execution** (`execution/`) — Deterministic state machine running Python-VM contracts. Canonical CBOR I/O, gas metering, journaled state, and deterministic receipts. No side effects; pure functions.
3. **Capabilities** (`capabilities/`) — Off-chain compute coordination for AI/Quantum jobs. Integrates with AICF (`aicf/`), which matches jobs to providers, settles payments, and enforces SLAs.
4. **Contracts** (`contracts/`) — User-written Python contracts compiled to IR, deployed on-chain, and invoked via standardized ABIs.
5. **SDKs** (`sdk/`) — Multi-language clients (Python `omni_sdk`, TypeScript `@animica/sdk`, Rust `animica-sdk`) for RPC, tx building, wallet, and contract interaction.

**Integration:**
```
Contracts (Python-VM) ──enqueue AI/Quantum──> AICF ──assign──> Providers
      ▲                                          │                  │
      │                                      PoIES proofs         execute
      └──Receipts(on-chain) <─ Proofs ◀─ Providers publish ────────┘
```

## Key Patterns

### Determinism & Canonical Encoding
- **All consensus paths must be pure** (no network I/O, no clock). Randomness is seeded from chain data.
- **CBOR canonical**: sorted keys, minimal int encoding. See `spec/` for schemas (params.yaml, poies_policy.yaml, abi.schema.json, manifest.schema.json).
- **Fixed-point math**: logs in μ-nats (micro-nats, `int64`). See `consensus/math.py` for safe arithmetic with rounding rules documented.
- **Gas**: deterministic per-opcode costs in `vm_py/gas_table.json`; metering in `execution/gas/`.

### Configuration & Policy
- **Governance-bound**: chain parameters (block interval, gas limits, Θ targets, PoIES caps) are stored in headers via **policy roots** to force consensus on active settings.
- Policy files: `spec/params.yaml`, `spec/poies_policy.yaml`, `governance/registries/*.json` (upgrade paths, params registry).
- Loading: adapt via `execution/adapters/params.py`, `consensus/policy.py`.

### Testing Strategy
- **Unit tests**: use pytest; locate under `<module>/tests/`.
- **Fixtures**: deterministic CBOR/JSON vectors under `<module>/fixtures/` and `sdk/common/test_vectors/`.
- **Integration**: spawn minimal nodes or mock adapters; e.g., `tests/integration/test_vm_deploy_and_call.py`.
- **Run all**: `./testall.sh` (Python unit + SDK e2e), or `pytest <module>` for a single package.
- **Conftest patterns**: `conftest.py` provides automatic async support via `@pytest.mark.asyncio`; optional modules (da, randomness, pq) are auto-skipped in lightweight environments.

### State & Storage
- **Execution state** is journaled (`execution/state/journal.py`): track reads/writes per tx for rollback and deterministic snapshots.
- **State DB** (SQLite/RocksDB): adapters in `execution/adapters/state_db.py` and `core/db/state_db.py`.
- **Block DB** (`core/db/block_db.py`): stores receipts and logs alongside blocks.

### Error Handling
- Use structured exceptions: `ConsensusError`, `PolicyError`, `ThetaScheduleError`, `NullifierError`, `SchemaError` (see `consensus/interfaces.py`).
- Never swallow errors in consensus paths; propagate with context.
- Return tuples `(result, reason)` for reject cases (e.g., `("REJECT", "nullifier_reuse")`).

## Workflow: Add or Modify a Feature

1. **Understand the scope**: Is it consensus (PoIES), execution (gas/state), capabilities (AI/Quantum), or SDK? Check the README in that module.
2. **Consult the spec**: Before changing, read the spec doc (`spec/*.md`, `<module>/specs/*.md`). Specs document invariants and breaking changes must be justified.
3. **Write tests first**: Add unit test under `<module>/tests/` with fixtures in `<module>/fixtures/`. Use CBOR for serialization tests.
4. **Implement & validate**: Ensure determinism (no I/O, clock, randomness leaks). Run `pytest <module>/tests/` locally.
5. **Check encoding**: if touching I/O, verify against `spec/` schemas using `jsonschema` or embedded validators.
6. **Update docs**: Spec, README, or inline comments. Governance changes go to `governance/`.

## Build & Run

### Local Python

```bash
# Activate venv and install
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"  # Install repo in editable mode with dev extras

# Run a single module's tests
pytest -q consensus/tests
pytest -q execution/tests
pytest -q aicf/tests

# Run with a specific filter
pytest -q consensus/tests -k "test_scorer_accept"

# Run with coverage
pytest --cov=consensus consensus/tests
```

### Node & devnet

- Start a devnet node: check `tests/devnet/docker-compose.yml` or run via `setup.sh`.
- Test on devnet: `pytest tests/integration/ --rpc http://127.0.0.1:8545`.

### SDK (TypeScript/Rust)

```bash
# Node package manager is pnpm@9.0.0 (see package.json)
pnpm install
pnpm test

# Or per-workspace (sdk/typescript, studio-web, explorer-web, etc.)
cd sdk/typescript && pnpm install && pnpm test
```

## Common Tasks

### Deploy a Contract
```bash
# Compile
python -m vm_py.cli.compile \
  --manifest contracts/packages/counter/manifest.json \
  --out-dir contracts/build/counter

# Deploy via Python SDK
python -m omni_sdk.cli.deploy \
  --rpc http://127.0.0.1:8545 --chain-id 1337 \
  --keystore ~/.animica/keystore.json \
  --manifest contracts/packages/counter/manifest.json \
  --ir contracts/build/counter/counter.ir
```

### Add a New Proof Type to Scoring
1. Create verifier in `proofs/<type>/` → outputs `ProofMetrics`.
2. Add metrics → ψ mapping in `proofs/policy_adapter.py`.
3. Update `spec/poies_policy.yaml` with caps (Γ_type, diversity).
4. Add unit test in `consensus/tests/`.
5. No changes to retarget or fork choice needed.

### Enqueue a Job (AICF)
```bash
python -m aicf.cli.queue_submit --ai --prompt "hello" --max-units 100
python -m aicf.cli.queue_list
# (dev) Inject a result:
python -m aicf.cli.inject_result --task-id <id> --result aicf/fixtures/result_example.json
python -m aicf.cli.settle_epoch
```

### Verify Determinism (State/Receipts)
- Use `execution/cli/apply_block` to replay CBOR blocks and compare state/receipt roots.
- Fixture `execution/fixtures/genesis_state.json` + `tx_transfer_valid.cbor` for minimal repro.
- Check that `state_root` and `receipts_root` are stable across runs.

## File Organization Tips

- **Spec & governance**: `spec/`, `governance/` — read these first for context.
- **Module READMEs**: Each major module has a README describing its purpose, data flow, and invariants.
- **Fixtures & vectors**: `<module>/fixtures/` (CBOR/JSON examples), `sdk/common/test_vectors/` (cross-SDK test data).
- **Adapters**: `<module>/adapters/` bridges local logic to core DBs or external services.
- **CLI & RPC**: `<module>/cli/`, `<module>/rpc/` for operational tooling.

## PQ Cryptography

- Default signer: **Dilithium3** (stateless); optional **SPHINCS+** (stateful).
- Domain separation: each message kind (tx, commitment, header, etc.) has a unique prefix to prevent replay.
- Signatures in SDKs use PQ by default; no Ed25519 fallbacks on mainnet.

## Key External Dependencies

- **Python**: FastAPI (RPC), uvicorn (server), pydantic (schemas), pytest (testing).
- **TypeScript**: vite (build), vitest (test), monaco-editor (studio-web), react (explorer-web, studio-web).
- **Rust**: tokio (async), serde (serialization), tonic (gRPC, optional).
- **WASM**: Pyodide (vm_py in browser), wasm-bindgen (bindings).

## Debugging Tips

1. **Enable logging**: `export RUST_LOG=debug` (if Rust components present) or `logging.basicConfig(level=DEBUG)` in Python.
2. **Inspect CBOR**: Use `python -m cbor2` or hex dumps (`xxd file.cbor`).
3. **Trace state**: Use `execution/state/snapshots.py` to capture before/after of tx.
4. **Nullifier window**: Check `consensus/nullifiers.py` for TTL/replay issues.
5. **Policy roots**: If a block rejects with "policy root mismatch", ensure spec files match the chain's header.

## Quick Links

- **PoIES math**: `spec/poies_math.md`
- **Gas & VM**: `vm_py/specs/` (GAS.md, DETERMINISM.md)
- **Execution**: `execution/specs/` (RECEIPTS.md, SCHEDULER.md)
- **AICF lifecycle**: `aicf/README.md` (enqueue → assign → prove → settle)
- **Governance**: `governance/GOVERNANCE.md`, `governance/PROCESS.md`
- **SDK usage**: `sdk/docs/USAGE.md`, `sdk/python/examples/`, `sdk/typescript/examples/`
