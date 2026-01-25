# Withdrawals Service - Architecture Diagrams

## System Overview

```
┌──────────────────────────────────────────────────────────────────┐
│                        WITHDRAWALS SERVICE                        │
│                                                                   │
│  ┌────────────┐      ┌─────────────┐      ┌─────────────┐      │
│  │  HTTP API  │─────▶│  Pipeline   │─────▶│   Outbox    │      │
│  │            │      │   Stages    │      │   Worker    │      │
│  │ • User     │      │             │      │             │      │
│  │ • Admin    │      │ • Request   │      │ • Ledger    │      │
│  │ • Webhook  │      │ • Risk      │      │ • BitGo     │      │
│  └────────────┘      │ • Approve   │      └──────┬──────┘      │
│                      │ • Submit    │             │             │
│                      │ • Track     │             │             │
│                      │ • Finalize  │             │             │
│                      └─────────────┘             │             │
│                                                   │             │
│  ┌────────────────────────────────────┐          │             │
│  │       Background Jobs               │          │             │
│  │                                    │          │             │
│  │  • Poll Pending (1 min)           │          │             │
│  │  • Reconcile (6 hours)            │          │             │
│  └────────────────────────────────────┘          │             │
└───────────────────────────────────────────────────┼─────────────┘
                                                    │
                   ┌────────────────────────────────┼──────┐
                   │                                │      │
                   ▼                                ▼      ▼
        ┌─────────────────┐              ┌──────────────────┐
        │  Ledger Service │              │      BitGo       │
        │                 │              │    Custodian     │
        │ • Lock funds    │              │                  │
        │ • Broadcast tx  │              │ • Create transfer│
        │ • Cancel lock   │              │ • Sign & broadcast
        └─────────────────┘              │ • Webhooks       │
                                         └──────────────────┘
```

## Data Flow: Withdrawal Lifecycle

```
┌──────────┐
│  User    │
│ Request  │
└────┬─────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ 1. REQUEST VALIDATION                   │
│                                         │
│ • Validate amount vs policy             │
│ • Check KYC tier                        │
│ • Calculate fee                         │
│ • Evaluate risk                         │
│ • Create withdrawal record              │
│ • Queue: APPLY_LEDGER_LOCK             │
└────┬────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ 2. RISK EVALUATION                      │
│                                         │
│ • Check velocity limits (24h)           │
│ • Verify address whitelist              │
│ • Check if new address                  │
│ • Calculate risk score                  │
│ • Determine required approvals          │
│                                         │
│ Decision:                               │
│ • ALLOW → auto-approve                  │
│ • REVIEW → require approval(s)          │
│ • BLOCK → reject immediately            │
└────┬────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ 3. APPROVAL (if required)               │
│                                         │
│ • Admin(s) review withdrawal            │
│ • Record approval/rejection             │
│ • Check approval threshold              │
│ • Update status to APPROVED             │
│ • Queue: SUBMIT_TO_BITGO               │
└────┬────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ 4. BITGO SUBMISSION                     │
│                                         │
│ • Get wallet for asset/network          │
│ • Build transfer request                │
│ • Submit to BitGo API                   │
│ • Store provider_ref                    │
│ • Update status: SIGNING                │
└────┬────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ 5. BITGO PROCESSING                     │
│                                         │
│ • BitGo collects signatures             │
│ • Webhook: state=signed                 │
│ • Update status: BROADCAST              │
│ • Queue: APPLY_LEDGER_BROADCAST        │
│ • BitGo broadcasts to blockchain        │
└────┬────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ 6. CONFIRMATION                         │
│                                         │
│ • Blockchain confirms transaction       │
│ • BitGo webhook: state=confirmed        │
│ • Update status: CONFIRMED              │
│ • Record txid                           │
└─────────────────────────────────────────┘
```

## Database Schema Relationships

```
┌─────────────────────┐
│ withdrawal_policies │
│                     │
│ • min/max amounts   │
│ • daily limits      │
│ • KYC requirements  │
│ • approval rules    │
└──────────┬──────────┘
           │
           │ 1:N
           ▼
┌─────────────────────┐       ┌───────────────────────┐
│    withdrawals      │◀──────│ withdrawal_approvals  │
│                     │ 1:N   │                       │
│ • amount, fee       │       │ • approver_id         │
│ • destination       │       │ • action (APPROVE/    │
│ • status            │       │          REJECT)      │
│ • risk_score        │       │ • reason              │
│ • provider_ref      │       └───────────────────────┘
│ • txid              │
└──────────┬──────────┘
           │
           │ 1:1
           ▼
┌─────────────────────────┐
│ withdrawal_ledger_links │
│                         │
│ • lock_tx_id            │
│ • broadcast_tx_id       │
│ • cancel_tx_id          │
└─────────────────────────┘
           ▲
           │
           │ 1:N
┌──────────┴──────────┐
│ withdrawal_outbox   │
│                     │
│ • type (operation)  │
│ • payload           │
│ • status            │
│ • attempt_count     │
│ • next_retry_at     │
└─────────────────────┘

┌───────────────────────┐
│ withdrawal_audit_log  │
│                       │
│ • event_type          │
│ • actor_id/type       │
│ • changes             │
│ • metadata            │
└───────────────────────┘
```

## HTTP Request Flow

```
┌──────────┐
│  Client  │
└────┬─────┘
     │
     │ POST /withdrawals
     │ Authorization: Bearer {token}
     │ Idempotency-Key: {key}
     │
     ▼
┌─────────────────────┐
│ Auth Middleware     │───▶ Verify token, extract user
└────┬────────────────┘
     │
     ▼
┌─────────────────────┐
│ Rate Limit          │───▶ Check Redis, enforce limit
│ Middleware          │
└────┬────────────────┘
     │
     ▼
┌─────────────────────┐
│ Idempotency         │───▶ Check for duplicate, return
│ Middleware          │     cached response if exists
└────┬────────────────┘
     │
     ▼
┌─────────────────────┐
│ Route Handler       │
│                     │
│ 1. Validate body    │
│ 2. Begin DB tx      │
│ 3. Call pipeline    │
│ 4. Record idempotency│
│ 5. Commit tx        │
│ 6. Return response  │
└────┬────────────────┘
     │
     ▼
┌──────────┐
│  Client  │
└──────────┘
```

## Outbox Worker Processing

```
┌────────────────────────────────────────┐
│         Outbox Worker (5s interval)     │
└────┬───────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ 1. Get pending operations (SKIP LOCKED) │
└────┬────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ 2. For each operation:                  │
│                                         │
│    ┌───────────────────────────────┐   │
│    │ APPLY_LEDGER_LOCK             │   │
│    │ ───▶ POST /internal/lock      │   │
│    └───────────────────────────────┘   │
│                                         │
│    ┌───────────────────────────────┐   │
│    │ SUBMIT_TO_BITGO               │   │
│    │ ───▶ Call submitToBitGo()     │   │
│    └───────────────────────────────┘   │
│                                         │
│    ┌───────────────────────────────┐   │
│    │ APPLY_LEDGER_BROADCAST        │   │
│    │ ───▶ POST /internal/broadcast │   │
│    └───────────────────────────────┘   │
│                                         │
│    ┌───────────────────────────────┐   │
│    │ APPLY_LEDGER_CANCEL           │   │
│    │ ───▶ POST /internal/cancel    │   │
│    └───────────────────────────────┘   │
└────┬────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ 3. Update operation status:             │
│    • COMPLETED (success)                │
│    • PENDING (retry with backoff)       │
│    • FAILED (max attempts reached)      │
└─────────────────────────────────────────┘
```

## BitGo Webhook Processing

```
┌──────────┐
│  BitGo   │
└────┬─────┘
     │
     │ POST /webhooks/bitgo
     │ X-BitGo-Signature: {hmac}
     │
     ▼
┌─────────────────────┐
│ Verify Signature    │───▶ HMAC-SHA256 validation
└────┬────────────────┘
     │
     ▼
┌─────────────────────┐
│ Normalize Payload   │───▶ Convert to WithdrawalObservation
└────┬────────────────┘
     │
     ▼
┌─────────────────────┐
│ Find Withdrawal     │───▶ By provider_ref
└────┬────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│ Process State Transition                │
│                                         │
│ • SIGNING   → update status             │
│ • BROADCAST → update + queue ledger op  │
│ • CONFIRMED → finalize                  │
│ • FAILED    → mark failed + queue cancel│
└────┬────────────────────────────────────┘
     │
     ▼
┌─────────────────────┐
│ Log Audit Event     │
└────┬────────────────┘
     │
     ▼
┌──────────┐
│  Response│
└──────────┘
```

## State Machine

```
                    ┌──────────────┐
                    │  REQUESTED   │◀── Initial state
                    └───┬──────────┘
                        │
            ┌───────────┼───────────┐
            │           │           │
            ▼           ▼           ▼
      ┌──────────┐ ┌──────────┐ ┌──────────┐
      │ REJECTED │ │RISK_REVIEW│ │ APPROVED │
      └──────────┘ └───┬───────┘ └────┬─────┘
                       │              │
                       │   Admin      │
                       │   approves   │
                       ▼              │
                  ┌──────────┐        │
                  │ APPROVED │◀───────┘
                  └────┬─────┘
                       │
                       │ BitGo submit
                       ▼
                  ┌──────────┐
                  │ SIGNING  │
                  └────┬─────┘
                       │
            ┌──────────┼──────────┐
            │                     │
            ▼                     ▼
       ┌──────────┐          ┌──────────┐
       │BROADCAST │          │  FAILED  │
       └────┬─────┘          └──────────┘
            │
            │ Confirmations
            ▼
       ┌──────────┐
       │CONFIRMED │
       └──────────┘

Terminal States: CONFIRMED, FAILED, REJECTED, CANCELED
```

## Retry Strategy

```
Attempt  Delay (base 1s)    Jitter (±10%)   
────────────────────────────────────────────
   1      1s                 0.9s - 1.1s     
   2      2s                 1.8s - 2.2s     
   3      4s                 3.6s - 4.4s     
   4      8s                 7.2s - 8.8s     
   5      16s                14.4s - 17.6s   
   6      32s                28.8s - 35.2s   
   7      64s                57.6s - 70.4s   
   8      128s               115.2s - 140.8s 
   9      256s               230.4s - 281.6s 
  10      300s (max)         270s - 330s     

Max attempts: 10
After 10 attempts: Mark as FAILED permanently
```

## Security Layers

```
┌─────────────────────────────────────┐
│         User Authentication          │
│  Bearer token / JWT verification     │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│          Rate Limiting               │
│  Redis-backed, per-user limits       │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│          Idempotency                 │
│  Prevent duplicate submissions       │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│        Risk Evaluation               │
│  Score, velocity limits, whitelist   │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│      Approval Workflow               │
│  Multi-approver, threshold-based     │
└────────────┬────────────────────────┘
             │
             ▼
┌─────────────────────────────────────┐
│    BitGo Custody Integration         │
│  Secure key management & signing     │
└─────────────────────────────────────┘
```
