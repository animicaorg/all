# Transaction Flow

## Summary

Transactions are prepared by the wallet and submitted against the hosted Animica RPC endpoint.

Canonical assumptions:

- endpoint: `https://rpc.animica.org`
- network: mainnet
- chain ID: `1`

## Send flow

1. User selects a wallet account.
2. Wallet validates recipient and amount.
3. Wallet estimates fee data from remote chain context.
4. Wallet prepares/signs via the wallet bridge.
5. Wallet submits through hosted RPC.
6. Wallet records local transaction state and updates UI.

## Receive flow

Receive is wallet-local:

- display address
- build QR/payment URI
- optionally include amount and memo metadata

No local node is involved.

## Failure handling

Common failure classes:

- hosted RPC unreachable
- RPC returned an error
- malformed remote response

These should be surfaced as remote connectivity or RPC issues, not node startup failures.
