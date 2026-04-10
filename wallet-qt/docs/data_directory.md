# Data Directory

## Purpose

The wallet data directory stores wallet-local user data only.

Default override environment variable:

- `ANIMICA_WALLET_DATA_DIR`

## Contents

Typical files:

```text
<data-dir>/
├── wallets.json
├── address_book.json
├── wallet.db
└── logs/
    └── wallet.log
```

## What is not stored here

The Qt wallet no longer owns any embedded-node state, so the data directory does not contain:

- chain databases
- snapshots
- genesis/spec copies
- node logs
- node PID files
- node network markers

## Platform defaults

The exact location is resolved through Qt standard paths plus the wallet app name. Use `ANIMICA_WALLET_DATA_DIR` to override it during development or testing.

## Operational guidance

- back up `wallets.json`, `address_book.json`, and `wallet.db`
- do not expect this directory to contain chain state
- if remote RPC is unavailable, troubleshooting should focus on network access to `https://rpc.animica.org/rpc`, not local node files
