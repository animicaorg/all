# Animica Documentation — Master Table of Contents

> This is the **authoritative index** for specs, design docs, and how-to guides across the repo.  
> Links are relative to repo root to work in GitHub and local viewers.

---

## 0) Start Here

- [Docs Philosophy & Structure](./README.md)
- Website (rendered subset): [`website/README.md`](../website/README.md)
- Project overview (website):
  - Landing: [`website/src/pages/index.astro`](../website/src/pages/index.astro)
  - Status API: [`website/src/pages/api/status.json.ts`](../website/src/pages/api/status.json.ts)

---

## 1) Specs (Normative Sources)

- **Spec index & conventions:** [`spec/README.md`](../spec/README.md)
- **Canonical params:** [`spec/params.yaml`](../spec/params.yaml)
- **PoIES policy:** [`spec/poies_policy.yaml`](../spec/poies_policy.yaml)
- **PQ policy:** [`spec/pq_policy.yaml`](../spec/pq_policy.yaml)
- **Chain registry:** [`spec/chains.json`](../spec/chains.json)
- **Domains & separators:** [`spec/domains.yaml`](../spec/domains.yaml)
- **Schemas (CDDL/JSON)**
  - Tx: [`spec/tx_format.cddl`](../spec/tx_format.cddl)
  - Header: [`spec/header_format.cddl`](../spec/header_format.cddl)
  - Blob/DA: [`spec/blob_format.cddl`](../spec/blob_format.cddl)
  - ABI: [`spec/abi.schema.json`](../spec/abi.schema.json)
  - Manifest: [`spec/manifest.schema.json`](../spec/manifest.schema.json)
  - OpenRPC: [`spec/openrpc.json`](../spec/openrpc.json)
  - VM opcodes & gas: [`spec/opcodes_vm_py.yaml`](../spec/opcodes_vm_py.yaml)
  - Alg-policy objects: [`spec/alg_policy.schema.json`](../spec/alg_policy.schema.json)
- **Math & formal**
  - PoIES math notes: [`spec/poies_math.md`](../spec/poies_math.md)
  - Lean/K models: [`spec/formal/README.md`](../spec/formal/README.md)
- **Test vectors**
  - Tx: [`spec/test_vectors/txs.json`](../spec/test_vectors/txs.json)
  - Headers: [`spec/test_vectors/headers.json`](../spec/test_vectors/headers.json)
  - Proofs: [`spec/test_vectors/proofs.json`](../spec/test_vectors/proofs.json)
  - VM programs: [`spec/test_vectors/vm_programs.json`](../spec/test_vectors/vm_programs.json)

---

## 2) Core Modules

- **Core (state, blocks, DB):** [`core/README.md`](../core/README.md)
- **Consensus (PoIES, Θ, fork choice):** [`consensus/README.md`](../consensus/README.md)
- **Proofs (Hash/AI/Quantum/Storage/VDF):** [`proofs/README.md`](../proofs/README.md)
- **Data Availability (NMT/erasure/DAS):** [`da/README.md`](../da/README.md)
- **Execution (state machine, receipts):** [`execution/README.md`](../execution/README.md)
- **Python VM (deterministic contracts):** [`vm_py/README.md`](../vm_py/README.md)
- **Capabilities (syscalls: AI/Quantum/zk/blob):** [`capabilities/README.md`](../capabilities/README.md)
- **AICF (providers, staking, payouts):** [`aicf/README.md`](../aicf/README.md)
- **Randomness (commit→reveal→VDF→mix):** [`randomness/README.md`](../randomness/README.md)
- **RPC (JSON-RPC & WS):** [`rpc/README.md`](../rpc/README.md)
- **P2P (handshake, gossip, sync):** [`p2p/README.md`](../p2p/README.md)
- **Mempool (fees, priority, eviction):** [`mempool/README.md`](../mempool/README.md)
- **Mining (templates, Stratum/WS):** [`mining/README.md`](../mining/README.md)

---

## 3) ZK Subsystem

### Architecture & Guides
- **Architecture:** [`zk/docs/ARCHITECTURE.md`](../zk/docs/ARCHITECTURE.md)
  - Data flow: `#data-flow-high-level`
  - Envelope → Adapters → Verifiers: `#envelope--adapters--verifiers`
  - Policy & integration: `#policy--integration`
  - VK pinning: `#registry--vk-pinning`
  - Determinism & reproducibility: `#determinism--reproducibility`
  - Native paths: `#native-fast-paths`
- **Add a circuit:** [`zk/docs/HOWTO_add_circuit.md`](../zk/docs/HOWTO_add_circuit.md)
  - Prereqs: `#prerequisites`
  - Steps & validation: `#step-by-step` / `#validate`
  - Pin VKs: `#pin-verification-keys`
- **Security:** [`zk/docs/SECURITY.md`](../zk/docs/SECURITY.md)
  - Trusted setup: `#trusted-setup`
  - VK pinning: `#verification-key-pinning`
  - Malleability & domains: `#malleability--domain-separation`
- **Performance:** [`zk/docs/PERFORMANCE.md`](../zk/docs/PERFORMANCE.md)
- **ZKML:** [`zk/docs/ZKML.md`](../zk/docs/ZKML.md)
- **Formats:** [`zk/docs/FORMATS.md`](../zk/docs/FORMATS.md)
- **Reproducibility:** [`zk/docs/REPRODUCIBILITY.md`](../zk/docs/REPRODUCIBILITY.md)

### Verifiers & Adapters
- Pairing (BN254): [`zk/verifiers/pairing_bn254.py`](../zk/verifiers/pairing_bn254.py)
- KZG (BN254): [`zk/verifiers/kzg_bn254.py`](../zk/verifiers/kzg_bn254.py)
- Poseidon: [`zk/verifiers/poseidon.py`](../zk/verifiers/poseidon.py)
- Fiat–Shamir transcript: [`zk/verifiers/transcript_fs.py`](../zk/verifiers/transcript_fs.py)
- Groth16: [`zk/verifiers/groth16_bn254.py`](../zk/verifiers/groth16_bn254.py)
- PLONK (KZG): [`zk/verifiers/plonk_kzg_bn254.py`](../zk/verifiers/plonk_kzg_bn254.py)
- STARK/FRI (toy): [`zk/verifiers/stark_fri.py`](../zk/verifiers/stark_fri.py)
- Merkle helper: [`zk/verifiers/merkle.py`](../zk/verifiers/merkle.py)

- Adapters:
  - snarkjs loader: [`zk/adapters/snarkjs_loader.py`](../zk/adapters/snarkjs_loader.py)
  - plonkjs loader: [`zk/adapters/plonkjs_loader.py`](../zk/adapters/plonkjs_loader.py)
  - stark loader: [`zk/adapters/stark_loader.py`](../zk/adapters/stark_loader.py)
  - omni bridge: [`zk/adapters/omni_bridge.py`](../zk/adapters/omni_bridge.py)

### Registry
- VK cache: [`zk/registry/vk_cache.json`](../zk/registry/vk_cache.json)
- Circuit registry: [`zk/registry/registry.yaml`](../zk/registry/registry.yaml)
- Tools:
  - Update VKs: [`zk/registry/update_vk.py`](../zk/registry/update_vk.py)
  - List circuits: [`zk/registry/list_circuits.py`](../zk/registry/list_circuits.py)

### Integration
- Types: [`zk/integration/types.py`](../zk/integration/types.py)
- Policy: [`zk/integration/policy.py`](../zk/integration/policy.py)
- Omni hooks: [`zk/integration/omni_hooks.py`](../zk/integration/omni_hooks.py)

### Native (optional accelerators)
- Crate: [`zk/native/Cargo.toml`](../zk/native/Cargo.toml)
- Py bindings: [`zk/native/python/animica_zk_native/__init__.py`](../zk/native/python/animica_zk_native/__init__.py)
- Benchmarks & tests: [`zk/native/benches/verify_bench.rs`](../zk/native/benches/verify_bench.rs), [`zk/native/tests/verify_tests.rs`](../zk/native/tests/verify_tests.rs)

---

## 4) SDKs & Tools

- **SDK overview:** [`sdk/README.md`](../sdk/README.md)
- Python SDK: [`sdk/python/README.md`](../sdk/python/README.md)
- TypeScript SDK: [`sdk/typescript/README.md`](../sdk/typescript/README.md)
- Rust SDK: [`sdk/rust/README.md`](../sdk/rust/README.md)
- Codegen: [`sdk/CODEGEN.md`](../sdk/CODEGEN.md)
- Test harness: [`sdk/test-harness/README.md`](../sdk/test-harness/README.md)

---

## 5) Apps & Studio

- **Wallet (MV3 extension):** [`wallet-extension/README.md`](../wallet-extension/README.md)
- **Studio (web IDE):** [`studio-web/README.md`](../studio-web/README.md)
- **Studio WASM (Pyodide):** [`studio-wasm/README.md`](../studio-wasm/README.md)
- **Studio Services (deploy/verify/faucet):** [`studio-services/README.md`](../studio-services/README.md)
- **Explorer desktop (Tauri):** [`installers/explorer-desktop/README.md`](../installers/explorer-desktop/README.md)

---

## 6) Installers & Updates

- **Installers overview:** [`installers/README.md`](../installers/README.md)
- Makefile & env: [`installers/Makefile`](../installers/Makefile), [`installers/.env.example`](../installers/.env.example)
- Wallet installers:
  - macOS: [`installers/wallet/macos/README.md`](../installers/wallet/macos/README.md)
  - Windows: [`installers/wallet/windows/README.md`](../installers/wallet/windows/README.md)
  - Linux: [`installers/wallet/linux/README.md`](../installers/wallet/linux/README.md)
- Explorer desktop:
  - Tauri app: [`installers/explorer-desktop/tauri/README.md`](../installers/explorer-desktop/tauri/README.md)
- Updates & appcasts:
  - [`installers/updates/README.md`](../installers/updates/README.md)
  - Wallet appcast (stable/beta): [`installers/updates/wallet/stable/appcast.xml`](../installers/updates/wallet/stable/appcast.xml), [`installers/updates/wallet/beta/appcast.xml`](../installers/updates/wallet/beta/appcast.xml)

---

## 7) Website Content & i18n

- Config: [`website/src/config/site.ts`](../website/src/config/site.ts), [`website/src/config/links.ts`](../website/src/config/links.ts)
- Chains: [`website/chains/index.json`](../website/chains/index.json)
- i18n: [`website/src/i18n/i18n.ts`](../website/src/i18n/i18n.ts)

---

## 8) Operations & Security

- Security docs (website): [`website/docs/SECURITY.md`](../website/docs/SECURITY.md)
- Accessibility (website): [`website/docs/ACCESSIBILITY.md`](../website/docs/ACCESSIBILITY.md)
- Deployment (website): [`website/docs/DEPLOYMENT.md`](../website/docs/DEPLOYMENT.md)
- Installers signing policies: [`installers/signing/policies.md`](../installers/signing/policies.md)
- CI examples:
  - Wallet pipelines: [`installers/ci/github/wallet-macos.yml`](../installers/ci/github/wallet-macos.yml), [`wallet-windows.yml`](../installers/ci/github/wallet-windows.yml), [`wallet-linux.yml`](../installers/ci/github/wallet-linux.yml)
  - Explorer pipelines: [`installers/ci/github/explorer-macos.yml`](../installers/ci/github/explorer-macos.yml), [`explorer-windows.yml`](../installers/ci/github/explorer-windows.yml), [`explorer-linux.yml`](../installers/ci/github/explorer-linux.yml)

---

## 9) Tests & Benches (Selected)

- ZK tests:
  - Groth16 embedding: [`zk/tests/test_groth16_embedding_verify.py`](../zk/tests/test_groth16_embedding_verify.py)
  - PLONK/KZG + Poseidon: [`zk/tests/test_plonk_poseidon_verify.py`](../zk/tests/test_plonk_poseidon_verify.py)
  - STARK Merkle toy: [`zk/tests/test_stark_merkle_verify.py`](../zk/tests/test_stark_merkle_verify.py)
  - VK cache: [`zk/tests/test_vk_cache.py`](../zk/tests/test_vk_cache.py)
  - Envelope round-trip: [`zk/tests/test_envelope_roundtrip.py`](../zk/tests/test_envelope_roundtrip.py)
- ZK benches:
  - Per-scheme verify speed: [`zk/bench/verify_speed.py`](../zk/bench/verify_speed.py)
- Module test suites live under each module’s `tests/` directory.

---

## 10) Handy Cross-References

- **OpenRPC surface:** [`spec/openrpc.json`](../spec/openrpc.json)
- **ABI schema:** [`spec/abi.schema.json`](../spec/abi.schema.json)
- **Chains (website bundle):** [`website/src/pages/api/chainmeta.json.ts`](../website/src/pages/api/chainmeta.json.ts)
- **Status endpoint (website):** [`website/src/pages/api/status.json.ts`](../website/src/pages/api/status.json.ts)

---

## 11) Tutorials & how-tos

- **AI agent starter:** [`docs/tutorials/AI_AGENT.md`](./tutorials/AI_AGENT.md)
- **DA oracle sample:** [`docs/tutorials/DA_ORACLE.md`](./tutorials/DA_ORACLE.md)
- **Escrow contract:** [`docs/tutorials/ESCROW.md`](./tutorials/ESCROW.md)
- **Hello, Counter (deploy & call):** [`docs/tutorials/HELLO_COUNTER.md`](./tutorials/HELLO_COUNTER.md)
- **Indexer lite:** [`docs/tutorials/INDEXER_LITE.md`](./tutorials/INDEXER_LITE.md)
- **Mine Animica locally (devnet or pool):** [`docs/tutorials/MINING_GUIDE.md`](./tutorials/MINING_GUIDE.md)
- **Provider GPU setup:** [`docs/tutorials/PROVIDER_GPU.md`](./tutorials/PROVIDER_GPU.md)
- **Quantum RNG flow:** [`docs/tutorials/QUANTUM_RNG.md`](./tutorials/QUANTUM_RNG.md)
- **Token example (mint/transfer):** [`docs/tutorials/TOKEN.md`](./tutorials/TOKEN.md)

---

### Updating This TOC

- Keep items **stable and alphabetized within sections** where possible.
- Prefer **direct file links** over GitHub permalinks; readers may be local.
- Use heading anchors (`#like-this`) for deep links where headings are stable.
- Validate links with:  
  ```bash
  node website/scripts/check_links.mjs

