# tests/fixtures

Canonical, **small** test artifacts shared across unit/property/integration tests.
These fixtures are deterministic, human-auditable, and safe to commit. No secrets.

> Large or volatile artifacts (full DB snapshots, long traces) should **not** live here.
> Use module-local `fixtures/` (e.g., `da/fixtures`, `vm_py/fixtures`) or generate on the fly.

---

## What lives here

- **Accounts / addresses** – tiny JSON blobs (names, expected bech32m) for smoke tests.
- **Contracts (mini)** – e.g., Counter source/manifest duplicates for cross-module tests.
- **Transactions (CBOR)** – hand-crafted valid/invalid edge cases used by codec/property tests.
- **Headers / Blocks (JSON/CBOR)** – minimal headers/blocks to test hashing/encoding paths.
- **DA blobs** – tiny binary samples + commitments for light-client demos.
- **Misc** – helper inputs for fuzz/bench harnesses that need stable seeds.

Module-specific, heavier fixtures remain in their module (e.g., `proofs/fixtures`, `da/fixtures`).

---

## Conventions

- **Encoding**
  - JSON: UTF-8, LF line endings, sorted keys, no trailing spaces.
  - CBOR: **canonical** (deterministic map ordering) matching `core/encoding/cbor.py`.
  - Hex: lowercase, `0x` prefixed.
  - Bech32m addresses: HRP `anim` (devnet) unless stated.

- **Determinism**
  - All randomized artifacts derive from fixed test seeds (see `tests/devnet/seed_wallets.json`).
  - Timestamps use RFC3339 with `Z`, rounded to seconds.

- **Schemas**
  - Follow spec files under `spec/` (e.g., `*.cddl`, `*.schema.json`) where applicable.
  - Keep files small: prefer minimal, representative vectors over breadth.

---

## Typical layout (suggested)

tests/fixtures/
accounts/
simple_accounts.json
contracts/
counter/
contract.py
manifest.json
txs/
transfer_valid.cbor
transfer_low_fee.cbor
deploy_counter.cbor
headers/
genesis_header.json
blocks/
tiny_block.cbor
da/
blob_small.bin
blob_small.commitment.json

(Only create subfolders you actually need.)

---

## Regenerating fixtures

Where possible, regenerate via the project’s public CLIs so formats remain canonical:

- **CBOR encode/decode**: `python -m core.cli_demo` or `sdk/python/omni_sdk/tx/*`
- **VM compile**: `python -m vm_py.cli.compile …`
- **DA commit**: `python -m da.cli.put_blob …`
- **Proof helpers**: `python -m proofs.cli.*` (devnet-safe, non-secret)

When adding a new fixture:
1. Prefer a command that produces canonical output (sorted/normalized).
2. Keep it as small as possible (aim <10 KiB).
3. Document the generation command in a `// generated-by:` comment (JSON) or sidecar `.txt`.

---

## Validation

- `pytest -q` runs codec/property tests that traverse these fixtures.
- Property tests assert:
  - **Tx codec idempotence** (`tests/property/test_tx_codec_props.py`)
  - **Block hash stability** (`tests/property/test_block_codec_props.py`)
  - **DA sampling math bounds** (`tests/property/test_da_sampling_props.py`)
  - …and more.

---

## Security & licensing

- Do not store private keys or real secrets here.
- Test-only material; never reuse in production networks.

