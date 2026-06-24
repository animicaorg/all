# Animica Bitcoin-Core-Compatible RPC Mode

> A **compatibility layer**, not an identity claim. Animica is a post-quantum,
> account-model, PoIES useful-work chain. This mode exposes Bitcoin Core's
> JSON-RPC **method names and response shapes** (targeting Bitcoin Core 30.x) and
> maps their *meaning* to Animica — so existing explorers, wallets, exchanges,
> bots, indexers, and pool dashboards can "point Bitcoin RPC tooling at a new
> endpoint." It does **not** make Animica behave identically to Bitcoin.

## Endpoints

Bitcoin clients POST JSON-RPC to the root path; Animica's native namespaces stay
on `/rpc`. Both go through the same dispatcher.

```
POST /        ← Bitcoin-compat (bitcoin-cli, BTCPay, Electrum-personal-server, …)
POST /rpc     ← Bitcoin-compat AND native animica state.*/chain.*/miner.*/aicf.*
```

JSON-RPC 2.0, positional + named params, batches, and notifications all work.

```bash
# point bitcoin-cli at an Animica node
bitcoin-cli -rpcconnect=<host> -rpcport=8545 getblockcount
curl -s http://<host>:8545/ -d '{"jsonrpc":"2.0","id":1,"method":"getblockchaininfo","params":[]}'
```

## What's implemented

**Tier 1 — read-only core (highest viability):** `getblockcount`,
`getbestblockhash`, `getblockhash`, `getblock`, `getblockheader`,
`getblockchaininfo`, `getchaintips`, `getdifficulty`, `getmempoolinfo`,
`getrawmempool`, `getrawtransaction`, `sendrawtransaction`, `testmempoolaccept`,
`decoderawtransaction`, `validateaddress`, `getnetworkinfo`, `getpeerinfo`,
`uptime`, `stop`, `help`.

**Tier 2 — wallet adapter:** `getnewaddress`, `getbalance`, `listunspent`,
`sendtoaddress`, `sendmany`, `listtransactions`, `gettransaction`,
`createwallet`, `loadwallet`, `listwallets`, `backupwallet`.

**Tier 3 — mining:** `getblocktemplate`, `submitblock`, `prioritisetransaction`,
`generatetoaddress`, `getmininginfo`.

## How the mapping works

| Bitcoin | Animica source |
|---|---|
| `getblockcount` | `chain.getHead().height` |
| `getbestblockhash` | `chain.getHead().hash` (0x stripped) |
| `getblock` / `getblockheader` | `chain.getBlockByHash` → Bitcoin-shaped header/block |
| `getrawtransaction` | `tx.getTransactionByHash` + `mempool.getRawTx` |
| `sendrawtransaction` | `tx.sendRawTransaction` |
| `getmempoolinfo` / `getrawmempool` | `mempool.getStats` / `mempool.getPending` |
| `getnetworkinfo` / `getpeerinfo` | `node.health` / `p2p.listPeers` |
| `getblockchaininfo` | `chain.getHead` + `chain.getChainIdentity` |
| `getblocktemplate` / `submitblock` | `miner.getBlockTemplate` / `miner.submitBlock` |

- **Hashes:** Animica uses `0x`+64-hex SHA3-256; responses strip `0x` for the
  Bitcoin bare-hex convention. SHA3 vs double-SHA256 is invisible to clients.
- **Units:** nANM ↔ Bitcoin coin-unit float via `/1e9` (nANM ≈ satoshi).
- **Difficulty:** synthetic — `exp(thetaMicro/1e6)` (expected PoIES trials/block),
  a monotonic Bitcoin-difficulty analogue (PoIES has no compact `nBits` target).

## Where compatibility is *degraded* (by design)

Animica diverges from Bitcoin at the data-model level; these are returned as
compatibility-only values so clients don't break:

- `chainwork` = `"00"*32`, `bits` = `"1d00ffff"`, `mediantime` = block time,
  `verificationprogress` = `1.0`, `pruned` = `false`, `softforks` = `{}`.
- **Account model, not UTXO:** `decoderawtransaction`/`getrawtransaction` synthesize
  one `vin` (sender, `txid: null`) + one `vout` (recipient + value). The real
  sender/kind/nonce/fee are carried under `animica:*` keys (clients ignore them).
- **`getblock`/`getblockheader` by hash:** the node has no reverse hash→height
  index, so block *height* is resolved only for the chain tip (best block); for
  deeper blocks fetched by hash it is reported as `-1` (Bitcoin's "unknown"
  convention). Callers that obtained the hash via `getblockhash(height)` already
  know the height; block content (txs, parent, roots) is always correct.
- **No address-indexed history:** `listtransactions` returns `[]`; `listunspent`
  synthesizes one "UTXO" per address balance. Use an explorer index for history.
- **Single node wallet:** `createwallet`/`loadwallet` are advisory; `wallet_name`
  is ignored.
- **PoIES mining:** `getblocktemplate` returns a Bitcoin-shaped envelope with the
  real consensus inputs under `animica:*` (`thetaMicro`, `templateId`, `stateRoot`).
- **`stop`** is a no-op string by default (the node may be the verifier seed);
  set `ANIMICA_BTC_COMPAT_ALLOW_STOP=1` to enable.

## Errors

Bitcoin RPC error codes are used in `error.code`: `-5` (invalid address/tx),
`-8` (invalid parameter), `-22` (deserialization), `-25`/`-26`/`-27` (verify /
rejected / already-in-chain), `-28` (warmup), `-32601` (method not found). Native
Animica failures are remapped to the nearest Bitcoin code + reject-reason string.

## Status

Tier 1 is production-grade for read-only integrations. Tiers 2–3 are adapters
with the documented account-model/PoIES caveats. Declared compatible against
**Bitcoin Core 30.x**; Bitcoin Core's RPC is implicitly versioned by major
release, so behavior is tracked against that target.
