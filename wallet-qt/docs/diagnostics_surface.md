# Diagnostics Surface

## Purpose

Diagnostics in the Qt wallet are now limited to remote-wallet concerns.

Relevant diagnostics include:

- current hosted endpoint
- whether `https://rpc.animica.org/rpc` is reachable
- last RPC/network error message
- wallet-local file locations when useful

## Removed diagnostics

The wallet no longer exposes diagnostics for:

- node start/stop/restart
- local process state
- node stdout/stderr logs
- local RPC readiness checks
- chain DB ownership inside the wallet app

## User-facing behavior

When the hosted RPC cannot be reached, show:

- a clear banner
- retry affordance
- concise network/RPC error details

Do not show operator/node-management controls.
