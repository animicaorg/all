# Wallet Qt Architecture

## Summary

`wallet-qt` is a remote-only desktop wallet.

The application always assumes:

- Animica mainnet
- hosted RPC at `https://rpc.animica.org`
- no embedded node process
- no bundled chain data, genesis files, or node runtime

## Runtime components

### Qt UI

Primary UI surfaces:

- accounts
- address book
- send
- receive
- history
- contracts
- settings

### Hosted RPC client

`src/rpc/AnimicaRpcClient.*` is the HTTP JSON-RPC client used for:

- connectivity checks
- balances
- sync/health signals
- chain queries
- transaction submission helpers

The canonical endpoint is defined in `src/rpc/RpcSettings.*` and resolves to:

- `https://rpc.animica.org`

### Wallet backend bridge

Wallet/account operations still use the wallet bridge helper invoked by `AnimicaWalletBackend`.

That layer is responsible for:

- canonical wallet store operations
- account import/export helpers
- transaction/history helper calls
- contract helper calls

This is not an embedded node. It does not launch or manage a local chain process.

## Startup flow

1. Create the Qt application.
2. Create the hosted RPC client pointed at `https://rpc.animica.org`.
3. Open or create the wallet store in the wallet data directory.
4. Show `WalletWidget`.
5. Refresh balances and remote connectivity once the event loop starts.

There is no node bootstrap, subprocess supervision, readiness loop, or bundled-genesis lookup.

## Data model

The wallet data directory contains wallet-local state only:

- `wallets.json`
- `address_book.json`
- `wallet.db`
- `logs/`

It does not contain:

- chain databases
- node PID files
- embedded-node logs
- bundled network metadata

## Removed architecture

The Qt wallet no longer includes:

- `NodeManager`
- `NodeControlWidget`
- local RPC settings dialogs
- diagnostics windows for local process state
- embedded-node packaging/runtime branches

## Design intent

The desktop wallet is now intentionally narrow:

- wallet UX
- remote mainnet access
- smaller packaging surface
- faster startup
- fewer crash paths

Node operation belongs to separate Animica tooling, not `wallet-qt`.
