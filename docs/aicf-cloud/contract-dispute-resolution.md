# Contract Dispute Resolution

Dispute lifecycle:

1. Authorized actor opens dispute with reason/evidence reference.
2. Job moves to challenged state.
3. Resolver action: `slash`, `clear`, or `refund_requester`.
4. Provider stake and escrow records are updated deterministically.

Admin surfaces:

- `/admin/contract-disputes`
- `/app/disputes`
