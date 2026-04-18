# BANM User Guide

## Before You Start

- This is a custodial bridge.
- MetaMask connection proves only EVM address control.
- Pasted Animica addresses and order amounts are immutable after order creation.

## ANM -> BANM

1. Open `/bridge`.
2. Connect MetaMask.
3. Select `ANM -> BANM`.
4. Enter Animica source address and amount.
5. Create order and sign challenge in MetaMask.
6. Send exact ANM amount to provided Animica deposit instruction.
7. Attach Animica tx hash in UI.
8. Track order at `/status/{orderId}` until completion.

## BANM -> ANM

1. Connect MetaMask.
2. Select `BANM -> ANM`.
3. Enter destination Animica address and amount.
4. Create order and sign challenge.
5. Deposit BANM through MetaMask router action.
6. If claim code is enabled, confirm claim code.
7. Track release progress on status page.

## Failure Handling

- Mismatched sender/amount/order ID goes to manual review.
- Expired orders do not settle and require a new order.

