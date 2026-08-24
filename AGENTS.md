# Animica — agent guide

Animica is a live, fully decentralized proof-of-work Layer-1 blockchain (chain id 1, mainnet) with post-quantum signatures (ML-DSA-65), deterministic Python-VM smart contracts, PoIES consensus (blocks are accepted when hash work plus verified useful-work evidence — AI/quantum/storage proofs — clears a difficulty threshold Θ; see `spec/poies_math.md`), an ANM-native Layer-2 rollup (`l2/`), and an off-chain AI-compute layer (AICF). This monorepo contains the node, consensus and execution engines, cryptography, wallets, SDKs (Python/TypeScript/Rust), the mining pool, explorer, and websites. The native coin is ANM; addresses are bech32m `anim1…`; 1 ANM = 10^9 base units.

## Repo layout (the directories that matter)

| Path | What it is |
|---|---|
| `consensus/` | PoIES consensus: scoring, difficulty retarget, fork choice, nullifiers |
| `execution/` | Deterministic state machine: gas, journaled state, receipts |
| `core/` | Node boot, block/state DBs, genesis |
| `mempool/`, `mempool2/` | Transaction admission and propagation |
| `rpc/` | JSON-RPC server (FastAPI) — namespaces `chain.*`, `state.*`, `tx.*`, `mempool.*`, `net.*`, `aicf.*`, `l2_*` |
| `p2p/` | Gossip network, peer discovery, PQ handshake |
| `mining/` | Miner core; `apps/miner-gui/` is the Qt GUI miner |
| `l2/` | ANM-native L2 rollup (10.x): SMT state, parallel executor, validity-by-re-execution, bridge. Docs in `docs/l2/` |
| `aicf/`, `capabilities/` | AI-compute framework: job matching, providers, settlement |
| `contracts/`, `vm_py/` | Python-VM contracts, compiler, gas table (`vm_py/gas_table.json`) |
| `pq/`, `proofs/`, `zk/`, `randomness/` | Post-quantum crypto, proof verifiers, ZK, randomness beacon |
| `spec/` | Canonical schemas and specs — read before changing serialization or consensus |
| `governance/` | Chain-parameter registries and upgrade process |
| `sdk/` | Python (`omni_sdk`), TypeScript (`@animica/sdk`), Rust clients |
| `python/` | **The PyPI `animica` package** (CLI + client, version 10.1.0). This is what `pip install animica` ships |
| `wallet/`, `wallet-qt/`, `wallet-extension/` | Flutter mobile, Qt desktop, browser-extension wallets |
| `explorer-web/`, `explorer2/` | Block explorer frontends/backends |
| `animica-pool/`, `pool-web/` | Stratum mining pool + pool site |
| `website/` | animica.org (Astro) |
| `docs/`, `tests/`, `ops/`, `scripts/` | Guides, integration tests, deployment, tooling |

The repo root is littered with hundreds of `*_SUMMARY.md` / `*_FIX*.md` files — these are historical work logs, **not** canonical documentation. Canonical sources: `spec/`, `docs/`, and each module's `README.md`.

## Install and run

There is **no `pyproject.toml` at the repo root** — `pip install -e .` from the root fails. The Python package lives in `python/`. The supported path:

```bash
./setup.sh                  # creates .venv and installs everything (use --fresh to rebuild)
source .venv/bin/activate
animica --help
```

What `setup.sh` does (the manual equivalent):

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip setuptools wheel
pip install -e sdk/python                # omni-sdk — local dependency, install FIRST
pip install -e "python[operator,dev]"    # the animica package (note: python/, not root)
pip install -e pq                        # pure-Python PQ fallback backend
pip install -r requirements.txt          # backend runtime deps
```

Run a node: `animica network set devnet && animica node up` (Docker Compose wrapper), status via `animica node status`. Mainnet users just do `pip install animica && animica up`.

## Tests

```bash
./testall.sh                             # everything: pytest -c tests/pytest.ini, cargo tests
                                         # (native/, crates/animica-native/), pnpm -r test/lint, ruff, pre-commit
ANIMICA_TESTALL_NO_LINT=1 ./testall.sh   # skip lint steps

pytest -q consensus/tests                # single module (also: execution/, rpc/, mempool/, p2p/, aicf/, l2/, mining/)
pytest -m "not slow and not integration" -q   # fast smoke
```

`conftest.py` auto-skips optional modules (da, randomness, pq) when their deps are missing. Node/TS workspaces use `pnpm` (see `pnpm-workspace.yaml`).

## Live endpoints (mainnet, verified 2026-08-14)

| Service | Endpoint | Notes |
|---|---|---|
| Node JSON-RPC | `POST https://rpc.animica.org/rpc` | JSON-RPC 2.0. The `/rpc` path is required — the bare domain returns a 301 that breaks naive POST clients |
| Explorer REST | `https://explorer.animica.org/api/…` | Free, no auth: `/head`, `/blocks`, `/tx/:hash`, `/address/:bech32`, `/richlist`, `/circulating-supply`, `/mining/info`, `/l2/*`, `/aicf/*` |
| Explorer UI | `https://explorer.animica.org` | |
| Free AI inference | `https://animica.dev/v1` | OpenAI-compatible (chat/completions, models, **embeddings**), **keyless**, 30 req/min/IP. Capacity is community-GPU: check each model's `serving` flag in `/v1/models` — non-serving models may 503 or queue. `POST /v1/embeddings` (model `animica-embed` → `bge-small-en-v1.5`, 384-d, ≤256 inputs) is computed by network workers; when none is serving embeddings it falls back to a local `all-MiniLM-L6-v2` and the response `model` says which (different vector spaces — don't mix) |
| Mining pool | `stratum+tcp://pool.animica.org:3333` | PPS + sub-block shares; `:3334` = solo (95/5). Stats: `https://pool.animica.org/api/pool/summary`; Swagger: `https://pool.animica.org/api/docs` |
| Payments | `https://pay.animica.dev` | Merchant REST `/api/v1/payment-intents` (Bearer key + Idempotency-Key), 2.00% fee, amounts in base units |
| PyPI | `https://pypi.org/project/animica/` | `pip install animica` — CLI, node, wallet, miner |
| MCP server | `pip install animica-mcp` → `animica-mcp` | Wraps `animica mcp serve` (stdio; also streamable-http/sse): 15 read+compute tools, no private keys |

Example RPC call:

```bash
curl -s -X POST https://rpc.animica.org/rpc -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"chain.getHead","params":{}}'
```

## Signature schemes — current vs legacy

- **Current: ML-DSA-65 (FIPS 204), scheme id `0x1003`. This is the only signature scheme to build on.** Older repo docs say "Dilithium3" — that is the lineage name for ML-DSA-65 (the FIPS-204 successor of Dilithium3); treat them as the same current scheme.
- **Legacy: SPHINCS+.** Present in code and old docs but legacy/stranded at the consensus level. Never build new features on it and never present it as current.
- Enumerate live schemes with the `tx.getSupportedSignatureSchemes` RPC. SDK signing must produce the canonical `tx_signing_preimage` — nodes verify only that.

## Key specs

- `spec/openrpc.json` — complete JSON-RPC API specification
- `spec/abi.schema.json` — contract ABI format
- `spec/params.yaml`, `spec/poies_policy.yaml` — chain parameters and PoIES policy (governance-bound via policy roots in headers)
- `spec/poies_math.md` — consensus math; `spec/tx_format.cddl`, `spec/header_format.cddl` — wire formats
- `vm_py/specs/` (GAS.md, DETERMINISM.md), `execution/specs/` (RECEIPTS.md, SCHEDULER.md)

Consensus/execution rules: all consensus paths must be pure (no I/O, no clock, no ambient randomness); serialization is canonical CBOR (sorted keys, minimal ints); math is fixed-point μ-nats (`consensus/math.py`); gas costs come from `vm_py/gas_table.json`. Validate any I/O change against `spec/` schemas.

## Gotchas for coding agents

- **The PyPI wheel builds from the WORKING TREE**, not from git: `python/pyproject.toml` force-includes top-level sibling packages into the wheel. Uncommitted edits ship to PyPI; conversely a file that exists on disk but was never `git add`-ed publishes fine yet breaks every fresh clone. Check both `git status` and `git ls-files` before releases.
- **Addresses are bech32m** with prefix `anim1…` — not 0x hex, and bech32m specifically (plain-bech32 checksums fail).
- **All API amounts are integer base units**: 1 ANM = 10^9 base units (nano-ANM). Never send floats.
- **AI-compute RPC lives under `aicf.*`** (60 methods: submitInferenceJob, jobStatus, estimateJobCost, listProviders, workerRegister/ClaimNextJob/SubmitResult/ReleaseClaim/Earnings…; enumerate via `https://explorer.animica.org/api/rpc/discover`). There is **no `ai.*` namespace** and **no `chain.getHeight`** — use `chain.getHead` and read `height`.
- **Job dispatch (11.1.1, node flag `ANIMICA_AICF_DISPATCH`)**: a job's `spec.kind` (`chat` default, `embed`, `classify`, `batch`) sets how many workers it fans out to (K) — deterministic kinds are K=1 and only offered to workers whose `workerRegister` `hardware.kinds` lists them; chat still races K workers for quality. A worker that claims and then can't answer must call `aicf.workerReleaseClaim` or its slot stays held for the lease. Embed results travel as one `EMB1 <model> <dims> f16 <base64 f16> <sha256>` line (see `apps/animica-chat/bridge/embeddings_route.py`).
- **Always use the `/rpc` path** on rpc.animica.org (see endpoints table).
- L2 methods are flat `l2_*` (e.g. `l2_status`, `l2_getBalance`), not `l2.*`.
- `faucet.request` is devnet/testnet-only; there is no mainnet faucet. Acquire ANM by mining via pool.animica.org, trading on NonKYC (https://nonkyc.io/market/ANM_USDT), or accepting payments via pay.animica.dev.
- The root `LICENSE.txt` is the Inter **font** license (OFL), not the project license — the codebase is Apache-2.0 (declared in `python/pyproject.toml`). See `LICENSE-NOTE.md`.
- Any `pip install -e ".[dev]"` instruction referring to the repo root is stale — install from `python/` as shown above.

## Contributing

Keep PRs scoped to one module/feature. Write tests first under `<module>/tests/` with fixtures in `<module>/fixtures/` (CBOR for serialization tests). Read the relevant `spec/*.md` before touching consensus, execution, or wire formats — breaking spec invariants must be justified. Run `./testall.sh` before submitting; Python style is enforced with `ruff`. Update the module README/spec alongside code. Security issues: do not open public issues — use a private GitHub security advisory (see `SECURITY.md`). Full guidelines: `CONTRIBUTING.md`.
