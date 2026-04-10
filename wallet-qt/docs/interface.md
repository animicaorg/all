# Hosted RPC Interface

## Endpoint

The Qt wallet uses hosted JSON-RPC over HTTPS:

- Base URL: `https://rpc.animica.org`
- Network: `mainnet`
- Chain ID: `1`

There is no supported loopback RPC mode in the Qt wallet.

## Typical RPC usage

The wallet uses hosted RPC for:

- connectivity checks
- balance reads
- sync/state reads
- fee-related chain queries
- transaction broadcast support

## UX expectations

- connection status should describe hosted RPC health
- failures should be reported as network/RPC problems
- diagnostics should not refer to a local node process

## Non-goals

This document does not describe node lifecycle APIs because the Qt wallet no longer launches or manages a node.
