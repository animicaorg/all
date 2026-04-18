# MetaMask Integration

## Wallet UX Goals

- MetaMask-first flow using injected EIP-1193 provider.
- Clean provider detection and install guidance.
- EIP-712 structured signature for order binding.
- Router deposit path for BANM->ANM.

## Signature Flow

1. Backend creates immutable order and typed challenge payload.
2. Frontend calls `signTypedData`.
3. Backend recovers signer and verifies against expected order EVM address.
4. Order transitions to `AWAITING_DEPOSIT`.

Fallback:

- Personal-sign message is supported only when typed signing is unavailable.

## Chain Handling

- UI requests BNB chain switch/add (`97` testnet, `56` mainnet).
- BANM deposit uses `BANMBridgeDepositRouter.deposit(bytes32 orderId, uint256 amount)`.

## What MetaMask Proves

- Proof of control for the connected EVM address used in order signing and transaction submission.

## What MetaMask Does Not Prove

- It does not prove ownership of pasted Animica source/destination address unless Animica-side signing is also enabled.

