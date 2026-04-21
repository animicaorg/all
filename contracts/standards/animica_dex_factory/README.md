# Animica DEX Factory (VM-PY)

`AnimicaDexFactory` is the canonical registry for pair discovery and fee policy.

## Responsibilities
- Stores one canonical pair per sorted token tuple.
- Calls pair `init(...)` during registration.
- Tracks pair metadata, creator, configured fee, and deterministic pair ID.
- Holds launch fee policy and fee recipient for router-enforced pair creation.

## Registration Model
`register_pair(...)` accepts an already deployed pair address. Contract deployment remains an off-chain responsibility (script / launcher service), while registry consistency is enforced on-chain.
