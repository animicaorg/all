# execution/fixtures

Small, reproducible fixtures used by unit/integration tests and CLI smoke runs for the
execution engine. All files are self-contained and deterministic so they can be used
to validate CBOR encoding, intrinsic gas, receipt construction, and state transitions.

---

## Contents

- **genesis_state.json**  
  Minimal balances/nonces for a tiny dev state used in smoke tests. This is read by
  the execution adapters (via `execution.adapters.state_db`) when initializing a new DB.

- **tx_transfer_valid.cbor**  
  A CBOR-encoded *transfer* transaction that **succeeds** under `genesis_state.json`.
  Intended for positive-path tests: intrinsic gas, nonce increment, balance debit/credit,
  event/log emission, and stable receipt/hash.

- **tx_transfer_insufficient.cbor**  
  A CBOR-encoded *transfer* transaction that **fails with InsufficientBalance**.
  Intended for negative-path tests: status = `OOG`/`REVERT` depending on policy, no state
  change beyond gas accounting, and deterministic error propagation.

All transactions follow the canonical schema in `spec/tx_format.cddl` and are encoded
with deterministic CBOR (canonical map ordering, minimal integer encoding).

---

## Quick start

### Run a single CBOR tx against a temporary state

```bash
# Successful transfer
python -m execution.cli.run_tx \
  --tx execution/fixtures/tx_transfer_valid.cbor \
  --genesis core/genesis/genesis.json \
  --chain-id 1 \
  --json

# Failing transfer (insufficient balance)
python -m execution.cli.run_tx \
  --tx execution/fixtures/tx_transfer_insufficient.cbor \
  --genesis core/genesis/genesis.json \
  --chain-id 1

Apply a small CBOR block and print the new head

python -m execution.cli.apply_block \
  --block path/to/block.cbor \
  --genesis core/genesis/genesis.json \
  --chain-id 1

Tip: use --persist-db in run_tx to keep the ephemeral SQLite DB for inspection.

⸻

Determinism & Canonicalization
	•	CBOR: encoded with canonical rules (sorted map keys by major type/bytewise,
shortest integer widths, no float unless required).
	•	SignBytes: Tx “sign domain” must be byte-for-byte stable; any re-encoding must
produce the same digest.
	•	Receipts: logs ordering and bloom/hash are deterministic (see
execution/receipts/*).
	•	State root: applying the same tx over the same prior state must yield the same
state root and receipt hash.

⸻

Regenerating fixtures (advanced)

If you change schemas or intrinsic-gas rules, regenerate the CBOR fixtures so tests
remain meaningful. Example (Python REPL/script):

from core.types.tx import Tx  # dataclass shaped per spec
from core.encoding import cbor as core_cbor

# Build a tiny, valid transfer (fields shown here are illustrative)
tx = Tx(
    kind="transfer",
    chain_id=1,
    sender=b"\x01"*32,
    to=b"\x02"*32,
    nonce=0,
    value=12345,
    gas_limit=21000,
    gas_price=1,
    access_list=[],
    payload=b"",
    signature={
        "alg_id": 0x01,   # e.g., Dilithium3 in this devnet
        "sig": b"\x00"*2700
    },
)

data = core_cbor.encode(tx)
open("execution/fixtures/tx_transfer_valid.cbor", "wb").write(data)

Keep signatures domain-separated and consistent with pq/ policy ids. For tests that
don’t validate signatures, zero-bytes placeholders are acceptable if the fast-path
precheck is disabled in that test.

When you regenerate a fixture, record its size and (optional) checksum:

wc -c execution/fixtures/tx_transfer_valid.cbor
sha256sum execution/fixtures/tx_transfer_valid.cbor


⸻

How these fixtures are used in tests
	•	Unit tests: execution/tests/test_* import these fixtures to verify intrinsic gas,
transfer semantics, receipts hashing, and scheduler determinism.
	•	Integration smoke: CLI examples above provide end-to-end “decode → apply → print”
checks that mirror what the node does when importing a tx.

⸻

Updating safely
	1.	Update schemas in spec/*.cddl or spec/*.json first.
	2.	Update encoders/decoders in core/encoding and execution/receipts/encoding.py.
	3.	Regenerate fixtures and run:

pytest -q execution/tests


	4.	If state roots or receipt hashes change, ensure the deltas are explained by the
spec changes; avoid accidental non-canonical encodings.

⸻

Provenance

All fixtures are synthetic and contain no secrets or real keys. They are provided
for testability and developer ergonomics only.
