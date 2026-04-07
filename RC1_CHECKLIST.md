# RC1 Checklist

Date: 2026-04-07

## Core Backend

- [x] Backend runtime dependencies install from `setup.sh`
- [x] Focused node and sync CLI suites pass
- [x] Focused P2P supervisor and sync completion tests pass
- [ ] Live leader/follower convergence smoke proves full-height sync
- [ ] Transaction propagation and mining inclusion e2e is green

## Docker and Ops

- [x] Docker build context is trimmed with `.dockerignore`
- [x] Compose port binding tests reflect actual shipped exposure
- [x] Explorer Docker build permission bug is fixed
- [ ] Explorer Docker runtime points at a real explorer app
- [ ] Host and volume permission paths are revalidated end to end

## Wallets and User Flows

- [x] Wallet `pending_outgoing` survives canonical wallet load
- [x] Focused wallet and tx smoke passes
- [ ] Wallet send/receive/mine/confirm e2e passes
- [ ] Wallet extension matches current RPC contract
- [ ] Wallet-qt builds and launches cleanly

## Frontends and Studio

- [x] Explorer web unit sync smoke passes
- [ ] Studio web provider smoke passes
- [ ] Explorer shows live backend data
- [ ] Studio wallet and tx flows work against the live stack
- [ ] Miner GUIs and dashboards are audited

## Exchange and Token Ecosystem

- [ ] Admin web type-check passes
- [ ] CEX e2e harness builds cleanly
- [ ] DEX and token launch flow is mapped and validated
- [ ] ANM pairing and listing path is grounded in code and tests

## Release

- [x] Root RC audit documents exist
- [x] Smoke helper scripts exist
- [ ] Smoke helper scripts are all green in one run
- [ ] Release packaging and deployment scripts are audited
