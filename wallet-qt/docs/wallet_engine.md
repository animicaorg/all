# Wallet Engine

## Role

`WalletEngine` coordinates wallet-local state with remote chain access.

It is responsible for:

- canonical wallet store management
- account creation/import/export
- address book operations
- balance tracking through hosted RPC
- transaction/history/contract bridge calls

## Remote-only assumptions

- RPC base URL is `https://rpc.animica.org`
- network is mainnet
- no local node process is available

## Startup behavior

When an RPC client is provided without an endpoint, the engine normalizes it to the canonical hosted endpoint. This keeps tests and runtime code aligned with the remote-only product model.

## Data ownership

The engine owns wallet-local state only. It does not own:

- chain storage
- genesis metadata
- node process lifecycle

## Balance tracking

Balance tracking polls the hosted endpoint and reports:

- confirmed balances
- sync state
- RPC/network errors

These are wallet-facing remote errors, not local-node failures.
