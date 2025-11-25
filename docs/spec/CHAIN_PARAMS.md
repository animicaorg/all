# Chain Parameters — IDs, Constants, and Limits

Authoritative reference for **chain IDs**, **economic/consensus constants**, **limits**, and their mapping to
`spec/params.yaml` and `rpc/methods/chain.py::chain.getParams`. Use this doc while editing
`spec/params.yaml` and when validating releases.

> See also:
> - `spec/params.yaml` — canonical values shipped with each network.
> - `spec/poies_policy.yaml` — PoIES caps (per-type) and total Γ cap.
> - `spec/chains.json` — CAIP-2 chain registry.
> - `rpc/methods/chain.py` — `chain.getParams` RPC surface.
> - `core/types/params.py` — in-node typed view.

---

## 1) Chain IDs (CAIP-2)

Animica networks follow CAIP-2 style identifiers:

| Network   | CAIP-2 ID      | Numeric `chainId` | Address HRP | Notes                          |
|-----------|-----------------|-------------------|-------------|--------------------------------|
| Mainnet   | `animica:1`     | `1`               | `anim`      | Production; params locked per release. |
| Testnet   | `animica:2`     | `2`               | `anim`      | Public test network; rapid upgrades.   |
| Devnet    | `animica:1337`  | `1337`            | `anim`      | Local/dev; permissive limits.          |

> **Address HRP:** We use a single HRP `anim` (bech32m) across networks. Network selection is enforced by `chainId` in the **signing domain** and by node/RPC configuration.

---

## 2) Units & Notation

- **Currency:** `ANM` (Animica). Smallest unit **atto-ANM (aANM)** = `10^-18 ANM`.  
- **Gas price unit:** `aANM/gas`.  
- **PoIES score space:** μ-nats (micro-nats). \( H(u) = -\ln(u) \) and \( \sum \psi \) are expressed in μ-nats internally.
- **Sizes:** KiB = 1024 bytes, MiB = 1024 KiB.

---

## 3) Consensus & PoIES Targets

| Parameter                                  | Default (Mainnet)   | Testnet/Devnet Example | Description |
|--------------------------------------------|---------------------|------------------------|-------------|
| `block.time_target_seconds`                 | **3**               | 2                      | Target inter-block time used by Θ retarget. |
| `difficulty.ema_alpha`                      | **0.10**            | 0.20                   | EMA smoothing factor for fractional retarget. |
| `difficulty.clamp_min_ratio`                | **0.75**            | 0.50                   | Lower clamp on Θ change per block. |
| `difficulty.clamp_max_ratio`                | **1.25**            | 1.50                   | Upper clamp on Θ change per block. |
| `poies.gamma_total_cap_μnats`               | **8_000_000**       | 12_000_000             | Total Γ cap (μ-nats) across all proof types. Per-type caps live in `spec/poies_policy.yaml`. |
| `nullifier.ttl_blocks`                       | **4096**            | 1024                   | Nullifier reuse prevention window. |
| `headers.mix_seed_bytes`                     | **32**              | 32                     | Length of mixSeed used in HashShare draw domain. |

---

## 4) Economic Limits & Gas

| Parameter                                  | Mainnet             | Testnet/Devnet         | Description |
|--------------------------------------------|---------------------|------------------------|-------------|
| `gas.block_gas_limit`                       | **20_000_000**      | 30_000_000             | Max total gas per block. |
| `gas.tx_intrinsic.transfer`                 | **21_000**          | 21_000                 | Intrinsic gas for native transfer. |
| `gas.tx_intrinsic.deploy_base`              | **100_000**         | 80_000                 | Base deploy intrinsic gas. |
| `gas.tx_intrinsic.deploy_per_code_byte`     | **20**              | 12                     | Per-byte intrinsic for contract code. |
| `gas.refund_max_ratio`                      | **0.20**            | 0.30                   | Max refundable fraction of gas used. |
| `fees.min_gas_price_aANM`                   | **1_000**           | 100                    | Network floor; mempool may enforce dynamic watermark above this. |
| `fees.base_tip_split`                       | **0.9/0.1**         | 0.85/0.15              | Base burn / tip to coinbase split. |

---

## 5) Block & Transaction Size Limits

| Parameter                               | Mainnet          | Testnet/Devnet   | Description |
|-----------------------------------------|------------------|------------------|-------------|
| `limits.max_block_bytes`                | **2_000_000**    | 4_000_000        | Hard cap on encoded block size (bytes). |
| `limits.max_txs_per_block`              | **10_000**       | 20_000           | Safety valve; ordering still by gas/priority. |
| `limits.max_tx_bytes`                   | **128_000**      | 256_000          | Hard cap on encoded transaction size (bytes). |
| `limits.max_receipts_bytes`             | **2_000_000**    | 4_000_000        | Envelope accounting for receipts root. |

---

## 6) Data Availability (DA) Parameters

| Parameter                                  | Mainnet         | Testnet/Devnet | Description |
|--------------------------------------------|-----------------|----------------|-------------|
| `da.max_blob_bytes`                         | **4_194_304**   | 8_388_608      | Max blob size (4 MiB / 8 MiB). |
| `da.share_bytes`                            | **512**         | 512            | Share/chunk size. |
| `da.erasure.k_n_profile`                    | **(k=64, n=128)** | (k=32, n=64)  | Reed-Solomon profile for parity expansion. |
| `da.sampling.samples_target`                | **80**          | 40             | Light-client sample count targeting low p_fail. |

---

## 7) Randomness Beacon Parameters

| Parameter                           | Mainnet    | Testnet/Devnet | Description |
|-------------------------------------|------------|-----------------|-------------|
| `rand.commit_window_blocks`         | **30**     | 20              | Blocks per commit window. |
| `rand.reveal_window_blocks`         | **30**     | 20              | Blocks per reveal window. |
| `rand.vdf.iterations`               | **100_000**| 25_000          | Wesolowski iterations for verifier cost target. |

---

## 8) Post-Quantum Crypto Policy (summary)

> Full policy lives in `spec/pq_policy.yaml` and `pq/alg_ids.yaml`. This section pins **default** algorithms for chain use.

| Purpose         | Default           | Allowed (policy-gated)         | Notes |
|-----------------|-------------------|---------------------------------|-------|
| Tx signatures   | Dilithium3        | SPHINCS+ (SHAKE-128s)          | Enforced at admission/verify. |
| P2P KEM         | Kyber-768         | (n/a)                           | Handshake to derive AEAD keys. |
| Address format  | `anim1…` bech32m  | —                               | `payload = alg_id || sha3_256(pubkey)`. |

---

## 9) JSON Layout in `spec/params.yaml`

A canonical `params.yaml` **MUST** include at least:

```yaml
chainId: 1
name: Animica Mainnet
currency:
  symbol: ANM
  decimals: 18
gas:
  block_gas_limit: 20000000
  tx_intrinsic:
    transfer: 21000
    deploy_base: 100000
    deploy_per_code_byte: 20
  refund_max_ratio: 0.20
fees:
  min_gas_price_aANM: 1000
  base_tip_split:
    base_burn: 0.9
    tip_to_coinbase: 0.1
consensus:
  block:
    time_target_seconds: 3
  difficulty:
    ema_alpha: 0.10
    clamp_min_ratio: 0.75
    clamp_max_ratio: 1.25
  poies:
    gamma_total_cap_μnats: 8000000
  nullifier:
    ttl_blocks: 4096
limits:
  max_block_bytes: 2000000
  max_txs_per_block: 10000
  max_tx_bytes: 128000
da:
  max_blob_bytes: 4194304
  share_bytes: 512
  erasure:
    k: 64
    n: 128
randomness:
  commit_window_blocks: 30
  reveal_window_blocks: 30
  vdf:
    iterations: 100000
pq:
  signatures:
    default: dilithium3
    allowed: [dilithium3, sphincs_shake_128s]
  kem:
    default: kyber768
address:
  hrp: anim
  payload: "alg_id || sha3_256(pubkey)"


⸻

10) chain.getParams (RPC) Shape

Nodes expose a read-only snapshot:

{
  "chainId": 1,
  "name": "Animica Mainnet",
  "currency": { "symbol": "ANM", "decimals": 18 },
  "gas": { "block_gas_limit": 20000000, "refund_max_ratio": 0.2 },
  "fees": { "min_gas_price_aANM": 1000, "base_tip_split": { "base_burn": 0.9, "tip_to_coinbase": 0.1 } },
  "consensus": {
    "block": { "time_target_seconds": 3 },
    "difficulty": { "ema_alpha": 0.1, "clamp_min_ratio": 0.75, "clamp_max_ratio": 1.25 },
    "poies": { "gamma_total_cap_μnats": 8000000 },
    "nullifier": { "ttl_blocks": 4096 }
  },
  "limits": { "max_block_bytes": 2000000, "max_txs_per_block": 10000, "max_tx_bytes": 128000 },
  "da": { "max_blob_bytes": 4194304, "share_bytes": 512, "erasure": { "k": 64, "n": 128 } },
  "randomness": { "commit_window_blocks": 30, "reveal_window_blocks": 30, "vdf": { "iterations": 100000 } },
  "pq": { "signatures": { "default": "dilithium3", "allowed": ["dilithium3","sphincs_shake_128s"] }, "kem": { "default": "kyber768" } },
  "address": { "hrp": "anim", "payload": "alg_id || sha3_256(pubkey)" },
  "policy_roots": {
    "poies_policy_root": "0x…",
    "alg_policy_root": "0x…"
  },
  "version": "x.y.z"
}


⸻

11) Validation & Invariants
	1.	Numeric sanity:
	•	limits.max_tx_bytes ≤ limits.max_block_bytes.
	•	gas.block_gas_limit > gas.tx_intrinsic.transfer.
	•	difficulty.clamp_min_ratio < 1 ≤ difficulty.clamp_max_ratio.
	2.	Policy compatibility:
	•	poies.gamma_total_cap_μnats ≥ max per-type cap sum in spec/poies_policy.yaml.
	3.	Address & PQ:
	•	pq.signatures.default ∈ pq.signatures.allowed.
	•	HRP must be anim for all networks (network separation via chainId).
	4.	DA & Sampling:
	•	For chosen k/n, da.sampling.samples_target must reach configured p_fail bound (see da/sampling/probability.py).

⸻

12) Release Process Notes
	•	Update spec/params.yaml and bump policy_roots when policy changes.
	•	Re-run vectors in:
	•	consensus/tests/test_difficulty_retarget.py
	•	mempool/tests/test_fee_market.py
	•	da/tests/test_sampling_probability.py
	•	Publish updated spec/chains.json and website /chains/ bundle.

⸻

13) Appendix — Devnet Template (quick copy)

chainId: 1337
name: Animica Devnet
currency: { symbol: ANM, decimals: 18 }
gas:
  block_gas_limit: 30000000
  tx_intrinsic: { transfer: 21000, deploy_base: 80000, deploy_per_code_byte: 12 }
  refund_max_ratio: 0.30
fees:
  min_gas_price_aANM: 100
  base_tip_split: { base_burn: 0.85, tip_to_coinbase: 0.15 }
consensus:
  block: { time_target_seconds: 2 }
  difficulty: { ema_alpha: 0.20, clamp_min_ratio: 0.50, clamp_max_ratio: 1.50 }
  poies: { gamma_total_cap_μnats: 12000000 }
  nullifier: { ttl_blocks: 1024 }
limits:
  max_block_bytes: 4000000
  max_txs_per_block: 20000
  max_tx_bytes: 256000
da:
  max_blob_bytes: 8388608
  share_bytes: 512
  erasure: { k: 32, n: 64 }
randomness:
  commit_window_blocks: 20
  reveal_window_blocks: 20
  vdf: { iterations: 25000 }
pq:
  signatures: { default: dilithium3, allowed: [dilithium3, sphincs_shake_128s] }
  kem: { default: kyber768 }
address: { hrp: anim, payload: "alg_id || sha3_256(pubkey)" }


⸻

Change history: maintained in docs/CHANGELOG.md and release tags.

