# Animica L2 — Fees

`l2/fees.py`. Integer nanos only; the schedule is deterministic and
**consensus-relevant** (it moves balances), so the constants are part of the
protocol and the sequencer may not deviate — a batch charging different fees
fails re-execution verification.

## The formula

A transaction pays the marginal resource it imposes:

```
fee = base                            # anti-spam floor: admission + a state write
    + da_per_byte   * encoded_bytes   # data availability: bytes anchored to L1
    + exec_per_unit * exec_units      # execution work
    (+ priority)                      # optional tip via the signed tx.fee ceiling
```

### Schedule constants (`FeeSchedule` defaults)

| Constant | Value | |
|---|---|---|
| `base` | `100` nanos | 1e-7 ANM |
| `da_per_byte` | `2` nanos/byte | charged on `body_bytes()` length |
| `exec_per_unit` | `20` nanos/unit | |

### Execution-unit weights per tx type

| Tx type | Units |
|---|---|
| `TRANSFER`, `PAY` | 1 |
| `AGENT_PAYMENT`, `INFERENCE_PAYMENT` | 1 |
| `WITHDRAW`, `FORCED_WITHDRAW` | 2 (touches bridge accounting) |
| `ESCROW_OPEN` / `ESCROW_RELEASE` / `ESCROW_REFUND` | 2 |
| `BATCH_PAYMENT` | 1 per recipient (max(1, N)) |
| `DEPOSIT_CLAIM` | 0 — protocol-minted, **fee-free** |

## Max-fee semantics

The signed `tx.fee` is the sender-authorized **ceiling**, not the charge.
Execution charges the schedule fee and reverts the tx if the ceiling is below
it — so a wallet can authorize a stable max without knowing the exact byte
count in advance. The excess (ceiling − charged) is **not** taken; only the
marginal fee is collected. Anything a sender adds above the schedule fee acts
as a priority tip.

`l2_estimateFee(raw)` returns the RPC-facing breakdown:
`{base, da, exec, total}` in nanos.

## Where fees go

Fees accrue to the protocol-fixed `L2_TREASURY_ADDRESS`
(`sha3_256("animica.l2.treasury.v1")`). Fees move *between accounts inside
L2* — they are never destroyed — so the bridge conservation invariant holds
exactly and the validity verifier's "no ANM minted" check
(Δ balances == deposited − withdrawn) is unaffected by fee flow.

## Micropayment viability (why these numbers)

Defaults make a 0.01 ANM AI micropayment economically sensible. A ~180-byte
`INFERENCE_PAYMENT` body costs

```
100 + 2·180 + 20·1 = 480 nanos ≈ 4.8e-7 ANM
```

— a rounding error against the payment itself. `BATCH_PAYMENT` (one ML-DSA-65
signature authorizing up to 4096 (recipient, amount) pairs) is charged per
recipient but shares one signature's DA bytes, so a large payout batch costs
far below N individual transfers — that is the point of the primitive.
