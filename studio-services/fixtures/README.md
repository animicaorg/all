# Fixtures for **studio-services**

This directory contains small, deterministic sample assets used by tests and local development of the Studio Services API (deploy/verify/faucet/artifacts/simulate). Nothing here includes secrets or production keys.

## What’s included

fixtures/
├─ counter/
│  ├─ contract.py                # Canonical sample contract (deterministic “Counter”)
│  └─ manifest.json              # ABI + deploy metadata for the Counter example
├─ deploy_signed_tx.cbor         # Example signed deploy transaction (CBOR, devnet parameters)
├─ artifact_abi.json             # Example ABI artifact blob (used by /artifacts tests)
├─ api_key.txt                   # Throwaway API key for test/dev runs

### Notes

- **counter/** mirrors the canonical example used across the repo (SDKs, studio-wasm, studio-web). The source and manifest are kept intentionally tiny and deterministic to make code‐hash verification straightforward.
- **deploy_signed_tx.cbor** is a pre-signed deploy targeting a devnet chainId (see `.env.example`). It is **not** valid on mainnet/testnet and embeds no private material—signatures were generated from a deterministic throwaway key used in tests.
- **artifact_abi.json** is an example of what gets POSTed/GET via the `/artifacts` endpoints. The service stores blobs content-addressed on disk or S3 (when configured).
- **api_key.txt** is only for local dev/tests. Real deployments should generate their own keys via the CLI (`scripts/gen_api_key.py`) and apply strict CORS and rate-limits.

---

## Quickstart (local dev)

1) Configure env:

```bash
cp studio-services/.env.example .env
# Edit RPC_URL / CHAIN_ID / STORAGE_DIR as needed for your devnet

	2.	Initialize storage & DB schema:

bash studio-services/scripts/migrate.sh

	3.	Load sample artifacts and a verification job:

python studio-services/scripts/load_fixtures.py

	4.	Start the API:

make -C studio-services dev
# or:
# uvicorn studio_services.app:app --reload

	5.	Use the throwaway API key for protected endpoints:

export STUDIO_SERVICES_API_KEY="$(cat studio-services/fixtures/api_key.txt)"


⸻

Using the fixtures with the API

Deploy (relay a signed CBOR)

curl -sS -X POST "$SERVICES_URL/deploy" \
  -H "Authorization: Bearer $STUDIO_SERVICES_API_KEY" \
  -H "Content-Type: application/cbor" \
  --data-binary @studio-services/fixtures/deploy_signed_tx.cbor

	•	Returns a JSON object with txHash. The service relays the signed CBOR to the node RPC.
	•	For a dry-run preflight (no relay), use /preflight with a JSON body referencing the same manifest/source.

Verify (recompile & compare code-hash)

Upload the source + manifest (from fixtures/counter/) to /verify:

curl -sS -X POST "$SERVICES_URL/verify" \
  -H "Authorization: Bearer $STUDIO_SERVICES_API_KEY" \
  -F "source=@studio-services/fixtures/counter/contract.py;type=text/x-python" \
  -F "manifest=@studio-services/fixtures/counter/manifest.json;type=application/json"

Then poll a result or fetch by address/txHash:

curl -sS "$SERVICES_URL/verify/{address}"
curl -sS "$SERVICES_URL/verify/{txHash}"

Artifacts (store & read)

# PUT
curl -sS -X POST "$SERVICES_URL/artifacts" \
  -H "Authorization: Bearer $STUDIO_SERVICES_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @studio-services/fixtures/artifact_abi.json

# GET by id (id returned from POST)
curl -sS "$SERVICES_URL/artifacts/{id}"

# List artifacts linked to an address
curl -sS "$SERVICES_URL/address/{addr}/artifacts"


⸻

Regenerating the deploy fixture (advanced)

If you change the contract or manifest and need a refreshed deploy_signed_tx.cbor:

Option A — Use the Python SDK directly

# ./tools/make_deploy_cbor.py (example sketch)
from omni_sdk.tx.build import build_deploy
from omni_sdk.tx.encode import signbytes_for_tx, encode_signed_cbor
from omni_sdk.wallet.signer import Dilithium3Signer
from omni_sdk.address import Address

manifest_path = "studio-services/fixtures/counter/manifest.json"
with open(manifest_path, "rb") as f:
    manifest = f.read()

tx = build_deploy(
    chain_id=1,  # devnet sample
    sender=Address.from_bech32("anim1..."),  # test sender
    manifest_bytes=manifest,
    nonce=0,
    gas_price=1,
    gas_limit=500_000
)

signer = Dilithium3Signer.from_mnemonic("test test test ...")  # throwaway
sign_bytes = signbytes_for_tx(tx)
sig = signer.sign(sign_bytes)

cbor = encode_signed_cbor(tx, sig)
open("studio-services/fixtures/deploy_signed_tx.cbor", "wb").write(cbor)

Ensure the chainId, gas limits, and nonce match your devnet and that the sender has funds.

Option B — Use the test harness

The cross-language test harness under sdk/test-harness can spin up or attach to a devnet and perform a deploy. Inspect the saved artifacts or adapt the script to dump the raw signed CBOR before submission.

⸻

Keeping things in sync
	•	The manifest.json and ABI must agree with the VM compiler and RPC encoding rules used by the node. These fixtures track the repo’s current vm_py and spec/abi.schema.json.
	•	If you update any of:
	•	ABI encoding rules,
	•	code-hash computation,
	•	transaction CBOR shape,
re-run the fixtures loader and adjust the tests that assert specific hashes or example payloads.

⸻

Security & Licensing
	•	These fixtures are non-sensitive and safe to commit.
	•	Keys used to produce signatures are throwaway, deterministically generated, and not reused outside tests.
	•	See repo license; third-party notices apply as documented in LICENCE-THIRD-PARTY.md.

