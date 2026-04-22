# Verification Modes

Supported modes:

1. `SINGLE_PROVIDER`
- One provider executes.
- Result finalizes after challenge window if unchallenged.

2. `QUORUM_MATCH`
- Multiple providers execute.
- Matching hash quorum required before acceptance/finalization.

3. `VERIFIER_REVIEW`
- Verifier or reviewer acceptance required.

4. `CALLBACK_ACCEPT`
- Requester/contract-app explicitly accepts before finalization.
