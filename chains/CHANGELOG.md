# Chains — Change Log
All notable changes to the **Animica chain metadata** (files under `chains/`) will be documented in this file.

This project adheres to:
- **Keep a Changelog** format (categories: Added/Changed/Deprecated/Removed/Fixed/Security)
- **Semantic Versioning** for the **schema** (`chains/schemas/*.json`) and for the **published bundle** of chain JSONs.

**Date:** 2025-10-31

---

## [Unreleased]
> Changes merged to main but not yet cut as a signed bundle.
### Added
- (example) New field `randomness.contract` (optional) in schema for on-chain beacon address.
- (example) Added WS endpoint to `animica.testnet.json`.

### Changed
- (example) Bumped `pq.policyVersion` to `2025-11` in testnet/localnet.
- (example) Updated `chains/icons/*` dark variants for better contrast.

### Deprecated
- (example) `rpc.http` entries using `http://` are marked for removal; prefer `https://`.

### Removed
- (example) Dropped legacy `seed3.testnet.animica.dev` (NXDOMAIN).

### Fixed
- (example) Corrected `nmtNamespaceBytes` description in schema.

### Security
- (example) Rotated release signature and re-signed `checksums.txt`.

---

## [1.0.0] — 2025-10-31
**Initial public baseline of chain metadata.** Signed checksums and loaders shipped.

### Added
- `chains/animica.mainnet.json` (reserved placeholders; status: `planned`).
- `chains/animica.testnet.json` (status: `active`, `testnet: true`).
- `chains/animica.localnet.json` (developer profile; permissive bootstraps).
- `chains/registry.json` (index of all chain JSONs with ids, names, paths).
- `chains/checksums.txt` (SHA-256 for each JSON; deterministic order).
- Schemas: `chains/schemas/chain.schema.json`, `chains/schemas/registry.schema.json`.
- Bootstrap lists in `chains/bootstrap/*` (seed hosts + bootnodes for each network).
- Icons in `chains/icons/*` (SVG + PNG sizes for wallets/explorers).
- Bindings:
  - TypeScript loader with schema validation in `chains/bindings/typescript/`.
  - Python loader (Pydantic) in `chains/bindings/python/animica_chains/`.
- Scripts:
  - `chains/scripts/validate.py` — jsonschema validate all files.
  - `chains/scripts/generate_checksums.py` — regenerate `checksums.txt`.
  - `chains/scripts/sync_to_website.py` — export/minify bundle.
  - `chains/scripts/check_endpoints.py` — simple RPC/WS health probe.
- Tests:
  - `chains/tests/test_registry.py` — schemas pass & ids unique.
  - `chains/tests/test_chain_integrity.py` — checksums match; endpoints format OK.
- Signatures:
  - `chains/signatures/registry.sig` — detached signature over `checksums.txt`.
  - `chains/signatures/maintainers.asc` — public key bundle (placeholder, see governance keys).

### Changed
- N/A (first release).

### Deprecated
- N/A

### Removed
- N/A

### Fixed
- N/A

### Security
- Release bundle signed with **Release Signing Key (CI/HSM)**; verification steps documented in `chains/README.md`.

---

## Release Process (for maintainers)
1. Run validation:  
   \`python chains/scripts/validate.py\`
2. Recompute checksums (deterministic order):  
   \`python chains/scripts/generate_checksums.py\`
3. Sign the checksums with the **Release key**:  
   \`gpg --local-user RELEASE_KEYID --detach-sign --armor -o chains/signatures/registry.sig chains/checksums.txt\`
4. Update this file:
   - Move entries from **[Unreleased]** into a new version block with today’s date.
   - Keep entries concise and user-visible.
5. Open PR → obtain maintainer review → merge → tag (signed):
   \`git tag -s chains-vX.Y.Z -m "Chains bundle X.Y.Z"\` then \`git push --tags\`.

---

## Notes
- Each chain JSON includes a **self-embedded** `checksum` that must match the value in the signed `checksums.txt`. The signed file remains the source of truth.
- Breaking schema changes require a **major** schema version bump and coordinated updates to bindings/tests.

