# Writing VM-PY Contracts For AICF Jobs

Use deterministic storage/events only.

Recommended fields in contract state:

- on-chain job/task id
- input hash/reference
- max ANM budget
- timeout/challenge window
- verification mode
- accepted result hash/reference
- settlement outcome

Recommended event sequence:

- `AICFModelCallRequested` or `AICFAgentTaskCreated`
- commitment/reference events
- accepted/challenged events
- finalized/refunded events

See examples in `contracts/examples/aicf/`.
