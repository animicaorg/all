# Animica Ethereum/EVM-Compatible RPC Mode

> An **Ethereum RPC-compatible facade, with a path toward full EVM execution.**
>
> Animica can support an Ethereum/EVM-compatible RPC facade, allowing
> Ethereum-style tools — MetaMask, ethers.js, web3.js, explorers, bots, and
> indexers — to connect to Animica through familiar `eth_*` methods while Animica
> remains its own **post-quantum PoIES useful-work L1**.
>
> This is an **RPC facade, not EVM execution.** Animica does **not** run Solidity
> bytecode. Do not read this as "Animica is EVM-compatible" — that claim is only
> true once Solidity deployment/execution is supported (a future phase).

## Endpoints

Ethereum clients POST JSON-RPC; methods answer at both `/` and `/rpc`.

```
POST /        ← eth_* / net_* / web3_*  (ethers.js, web3.js, MetaMask, explorers)
POST /rpc     ← the same, plus native animica chain.*/state.*/tx.*/miner.*/aicf.*
```

```bash
# MetaMask → Add network → RPC URL http://<host>:8545  (chainId from eth_chainId)
curl -s http://<host>:8545/ -d '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}'
# ethers v6
new ethers.JsonRpcProvider("http://<host>:8545")
```

## Implemented methods

**Connect / chain:** `eth_chainId`, `net_version`, `net_listening`, `net_peerCount`,
`web3_clientVersion`, `web3_sha3`, `eth_blockNumber`, `eth_syncing`,
`eth_protocolVersion`, `eth_mining`, `eth_hashrate`, `eth_coinbase`.

**Blocks:** `eth_getBlockByNumber`, `eth_getBlockByHash`,
`eth_getBlockTransactionCountByNumber`, `eth_getBlockTransactionCountByHash`.

**Accounts:** `eth_getBalance`, `eth_getTransactionCount`, `eth_getCode`,
`eth_getStorageAt`, `eth_accounts`.

**Transactions (read):** `eth_getTransactionByHash`, `eth_getTransactionReceipt`,
`eth_getTransactionByBlockNumberAndIndex`.

**Gas / fees / call:** `eth_gasPrice`, `eth_maxPriorityFeePerGas`, `eth_feeHistory`,
`eth_estimateGas`, `eth_call`.

**Logs / filters:** `eth_getLogs`, `eth_newFilter`, `eth_newBlockFilter`,
`eth_newPendingTransactionFilter`, `eth_uninstallFilter`, `eth_getFilterChanges`,
`eth_getFilterLogs`.

**Write / sign:** `eth_sendRawTransaction`, `eth_sendTransaction`, `eth_sign`
(bounded — see *The address & signature problem*).

**Address bridge:** `animica_evmBind`, `animica_evmAlias`.

## Mapping

| Ethereum | Animica source |
|---|---|
| `eth_chainId` | `chain.getChainId` → hex (override `ANIMICA_EVM_CHAIN_ID`) |
| `net_version` | chain id as decimal string |
| `web3_clientVersion` | `/Animica:<ver>/evm-facade` |
| `eth_blockNumber` | `chain.getHead().height` → hex |
| `eth_getBlockByNumber` / `…ByHash` | `chain.getBlockByHeight` / `…ByHash` → Ethereum block |
| `eth_getBalance` | `state.getBalance` (nANM as the wei-style smallest unit) |
| `eth_getTransactionCount` | `state.getNonce` / `getPendingNonce` |
| `eth_getTransactionByHash` / `…Receipt` | `tx.getTransactionByHash` (+ `tx.getTransactionStatus`) |
| `eth_gasPrice` / `eth_estimateGas` | synthetic (Animica fee is account-model, not a gas market) |

- **Hex encoding:** strict Ethereum QUANTITY (`0x`-compact, `0x0` for zero) and
  DATA (`0x`, even length). Animica `0x`+64-hex hashes pass through as 32-byte hashes.
- **Units:** Animica balances are nANM; exposed as the raw integer smallest unit
  (1 ANM = 1e9 nANM), the way Ethereum exposes wei.

## The address & signature problem (the real boundary)

Ethereum assumes 20-byte `0x` addresses and secp256k1 ECDSA. Animica is
post-quantum (`anim1…`, ML-DSA/SPHINCS+). So:

- **Address bridge:** every Animica account gets a **deterministic 20-byte EVM
  alias** = `0x ‖ keccak256(anim1)[-20:]`, with a binding registry so
  `eth_getBalance(0xalias)` resolves back. `animica_evmAlias(anim1)` returns the
  alias; `animica_evmBind` records the binding. An alias is a display/lookup
  handle — **not** key-compatibility.
- **`eth_sendRawTransaction` is bounded by default:** a secp256k1-signed EVM tx
  cannot be admitted directly (Animica accounts are PQ). With the relayer **off**,
  the facade decodes the tx, recovers the EVM sender, and returns `-32004`
  explaining how to enable the relayer, rather than silently failing.

## The relayer — moving value between EVM and native

Set **`ANIMICA_EVM_RELAYER=1`** to turn `eth_sendRawTransaction` into a working
bridge that **actually moves ANM between an EVM wallet and native Animica**. It is
**off by default** and **CUSTODIAL**: the node operator holds the native keys.

**Model — one managed native account per EVM address.** Each EVM address `E`
(recovered via `ecrecover`, never a client-supplied field) maps to a
relayer-controlled ML-DSA-65 account `A(E)`, generated lazily and persisted. The
EVM signature proves intent; the relayer enforces it by signing the **native** tx
from `A(E)` with `A(E)`'s post-quantum key.

- **Fund (native → EVM):** call `animica_evmAccount(E)` to get `A(E)`'s `anim1`
  address, then send ANM there with any native wallet. `eth_getBalance(E)` then
  reflects it.
- **Spend (EVM → EVM or EVM → native):** sign a normal EVM tx `{from E, to, value,
  nonce, chainId: 149}` in MetaMask/ethers and `eth_sendRawTransaction` it. The
  relayer recovers `E`, resolves the recipient (a bound real `anim1`, else the
  recipient's own managed account), submits a native transfer `A(E) → recipient`
  of `value` nANM, and returns the EVM tx hash. `eth_getTransactionReceipt(hash)`
  resolves to the reshaped native receipt.

**Security (enforced in `relayer.py`):** the debited account is derived **only**
from `ecrecover`; `chainId` must equal **149** exactly (EIP-155 cross-chain replay
protection; pre-EIP-155 txs rejected); a per-`E` monotonic nonce plus
`keccak256(raw)` idempotency stop same-chain replay; secp256k1 signatures must be
canonical (EIP-2 low-s); `value` is **nANM** with a sanity cap and a `value+fee ≤
balance` check; native keys are encrypted at rest with **AES-256-GCM** under
`ANIMICA_EVM_RELAYER_KEK` (the relayer refuses to start with plaintext keys unless
`ANIMICA_EVM_RELAYER_ALLOW_PLAINTEXT=1`) and never cross an RPC boundary.

**Custodial, not trustless.** An EVM signature *authorizes* a spend from its
managed account; it is **not** secp256k1↔PQ key-equivalence. `animica_evmRelayerInfo`
reports `custodial: true`. Operators run this as a managed gateway (e.g. an
exchange or app backend), not as a trustless bridge.

> **Decimals caveat.** `value` is in **nANM** (Animica's 9-decimal smallest unit),
> matching the chain's registered `nativeCurrency.decimals = 9`. Configure your
> wallet accordingly. If you operate for wallets that assume 18-decimal native
> currency, set `ANIMICA_EVM_RELAYER_VALUE_SCALE=1000000000` so `1e18 → 1e9 nANM`.

Env vars: `ANIMICA_EVM_RELAYER` (gate) · `ANIMICA_EVM_RELAYER_KEK` (32-byte
hex/base64 at-rest key) · `ANIMICA_EVM_RELAYER_ALLOW_PLAINTEXT` · `ANIMICA_EVM_RELAYER_VALUE_SCALE`
· `ANIMICA_EVM_RELAYER_MAX_NANM` (per-tx sanity cap).

## What works vs. what doesn't (from the facade alone)

**Works:** MetaMask "add network", ethers/web3 provider connect, block number /
block / tx / receipt lookup, balance & nonce lookup (for bridged addresses),
explorer/indexer read traffic, EIP-1193-style provider usage.

**Doesn't (facade alone):** running Solidity contracts. `eth_call` returns `0x`
and `eth_getCode` returns `0x` because Animica executes **Python-VM** contracts,
not EVM bytecode. `eth_getLogs` is empty until an EVM-shaped log index exists.

## Roadmap

1. **EVM RPC facade** *(shipped)* — `eth_*` connect + read.
2. **Custodial relayer** *(shipped)* — `ANIMICA_EVM_RELAYER=1` moves value between
   EVM and native Animica via managed accounts (see above).
3. **ERC facade** — native Animica contracts presented as ERC-20/721 via
   `eth_call`/`eth_getLogs`/receipt topics.
4. **Full EVM module** — an actual EVM runtime so Solidity deploys unchanged.

## Chain-id note

The EVM facade advertises its **own dedicated chain id**, decoupled from
Animica's native chain id. Animica's native chain id is `1`, but `1` is Ethereum
mainnet — advertising it to EVM wallets collides in chain-lists and weakens
EIP-155 replay protection. So:

- **`eth_chainId` / `net_version` default to `149`** (`0x95`), the lowest
  unregistered EIP-155 chain id (everything `1..148` is taken on
  `chainid.network`). This is a clean, low, collision-free id for MetaMask
  "add network".
- This changes **only what the facade reports.** Animica's underlying chain, its
  blocks, its state, and its native chain id (`1`) are **untouched** — the chain
  is never reset for EVM compatibility.
- Override with the `ANIMICA_EVM_CHAIN_ID` env var if you need a different id
  (e.g. a private deployment).
