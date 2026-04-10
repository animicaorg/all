# Chains Signatures — Verification & Provenance

This folder contains **detached signatures** and key bundles so wallets, explorers, and node operators can verify that the **chain metadata** under `chains/` is authentic and untampered.

## What is signed

- `chains/checksums.txt` — a deterministic, sorted list of SHA-256 hashes for:
  - `chains/animica.mainnet.json`
  - `chains/animica.testnet.json`
  - `chains/animica.localnet.json`
  - `chains/registry.json`
- Detached ASCII signature: `chains/signatures/registry.sig`
- Public keys bundle (ASCII-armored): `chains/signatures/maintainers.asc`
  - Mirrors of repo keys may also live at `governance/keys/gpg/maintainers.asc`.

> Each individual chain JSON also embeds a **self-check** field `checksum` which must match its line in `checksums.txt`. The signed `checksums.txt` is the **source of truth**.

---

## One-time setup (import keys)

```bash
gpg --import chains/signatures/maintainers.asc
# Optional: set trust (or use TOFU):
gpg --edit-key "<RELEASE_KEY_EMAIL_OR_FPR>" trust
# choose a trust level you are comfortable with
Verify the signed bundle
bash
Copy code
# 1) Verify the signature over checksums.txt
gpg --verify chains/signatures/registry.sig chains/checksums.txt

# 2) Locally recompute file hashes and compare to the signed list
shasum -a 256 chains/animica.localnet.json
shasum -a 256 chains/animica.mainnet.json
shasum -a 256 chains/animica.testnet.json
shasum -a 256 chains/registry.json

# 3) Optional: machine check — fail if any line mismatches
awk '{print $1"  "$2}' chains/checksums.txt | shasum -a 256 -c -
You should see Good signature and all OK lines. Any mismatch indicates tampering, partial download, or local edits.

Deterministic checksums (maintainers/CI)
Rebuild checksums.txt in deterministic order (sorted paths) before signing:

bash
Copy code
{ \
  shasum -a 256 chains/animica.localnet.json; \
  shasum -a 256 chains/animica.mainnet.json; \
  shasum -a 256 chains/animica.testnet.json; \
  shasum -a 256 chains/registry.json; \
} | awk '{printf "%s  %s\n",$1,$2}' > chains/checksums.txt
Sign with the Release Signing Key:

bash
Copy code
gpg --local-user "<RELEASE_KEY_FPR_OR_UID>" \
    --detach-sign --armor \
    -o chains/signatures/registry.sig \
    chains/checksums.txt
Publish maintainers.asc updates via PR when rotating keys (see Governance keys policy).

Verifying individual files (quick spot checks)
bash
Copy code
# Extract expected hash from the signed list:
grep 'chains/animica.testnet.json$' chains/checksums.txt | awk '{print $1}'

# Compute locally and compare:
shasum -a 256 chains/animica.testnet.json | awk '{print $1}'
They must be identical.

Supply-chain notes
Prefer HTTPS for fetching this repo or use signed release archives (Git signed tags).

Consumer tools should:

Verify registry.sig → checksums.txt

Recompute file hashes and compare

Check each JSON’s embedded checksum equals the signed value

Validate JSONs against chains/schemas/*.json

Troubleshooting
BAD signature: wrong key or corrupted file. Re-fetch maintainers.asc and registry.sig.

Hash mismatch: ensure no local edits; line endings must be LF; do not minify/reformat JSONs.

Key not found: import keys from maintainers.asc; verify key fingerprint via out-of-band channels.

Provenance & Rotation
Release key lives in a hardware-backed signer (HSM/YubiKey) per governance/keys/multisig_policies.md.

On rotation:

Add the new public key to maintainers.asc

Re-sign checksums.txt

Note rotation in chains/CHANGELOG.md under Security

Keep the old key valid long enough to cross-sign the first new release

Programmatic verification (TypeScript sketch)
ts
Copy code
import { readFileSync } from "node:fs";
import * as crypto from "node:crypto";
// 1) Parse checksums.txt into a map
const lines = readFileSync("chains/checksums.txt","utf8").trim().split(/\r?\n/);
const map = new Map(lines.map(l => { const [h,p] = l.split(/\s+/); return [p,h]; }));
// 2) Recompute one file
const buf = readFileSync("chains/animica.testnet.json");
const h  = crypto.createHash("sha256").update(buf).digest("hex");
if (h !== map.get("chains/animica.testnet.json")) throw new Error("checksum mismatch");
Signature verification in code depends on your crypto lib; many production setups run GPG as a separate step in CI and only ship the verified bundle.

