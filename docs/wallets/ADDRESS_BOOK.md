# Wallet Address Book — Watch-Only & Light-Client Flows

This document describes the **address book** feature for Animica wallets, focusing on:
- **Watch-only accounts** (no private keys held; read-only portfolio & alerts).
- **Light-client verification** paths for balances, transactions, and logs.

The goal is to provide a **safe default** for tracking accounts and contracts across networks, with integrity checks that do not require a full node.

> Animica notes  
> - Addresses are bech32m `anim1…` and encode **alg_id || sha3_256(pubkey)** for PQ keys.  
> - Networks (chain IDs, RPCs) are defined under `website/chains/*`.  
> - Light verification primitives are documented in `docs/spec/LIGHT_CLIENT.md` and implemented in SDKs (`sdk/*/light_client/verify`).

---

## 1) Concepts

### 1.1 Address Types
- **Externally Owned Accounts (EOA):** Controlled by a PQ key (e.g., Dilithium3). May be **watch-only** if the wallet does not store the key.
- **Contracts:** Code at an address; watch-only tracks code hash, ABI (if verified), and emitted events.
- **Labels & Tags:** Human-friendly names and quick filters (e.g., “Treasury”, “Ops”, “Provider”).

### 1.2 Watch-Only
- No private material stored. The wallet:
  - Fetches **balance, nonce, code hash**, recent **transactions**, and **events**.
  - Can create **unsigned transactions** for simulation or export.
  - **Cannot sign or send** unless a signer is attached later.
- Safe defaults: **send actions disabled**, “watch-only” badge, & opt-in alerts.

### 1.3 Light-Client Flows (High Level)
- **Headers:** Keep a verified chain of headers (via `sdk/*/light_client/verify`).
- **Receipts:** Verify transaction inclusion using the **receipts root** from headers.
- **DA (optional):** For blob-backed data, verify **DA roots** as needed.
- **Randomness Beacon:** Cross-check rounds when used by clients (not required for address book basics).

---

## 2) Data Model

### 2.1 Address Record (JSON)
```json
{
  "id": "addr_01J5E5W2J8PV…",
  "address": "anim1qxyz…",
  "network": "animica.testnet",
  "chainId": 2,
  "kind": "eoa",             // "eoa" | "contract"
  "watchOnly": true,
  "label": "Ops Treasury",
  "tags": ["treasury", "ops"],
  "color": "#6C5CE7",
  "algId": "dilithium3",     // if known for EOAs
  "codeHash": null,          // if contract; hex string when known
  "abiRef": null,            // artifact/vk reference or URL when verified
  "notes": "One-way inflow; alerts on outbound.",
  "createdAt": "2025-01-05T12:03:11Z",
  "updatedAt": "2025-01-05T12:03:11Z",
  "sources": [
    {"type":"explorer","url":"https://explorer.animica.dev/address/…"},
    {"type":"import","format":"qrcode"}
  ],
  "alerts": {
    "txOutbound": true,
    "balanceBelow": "1000000000",
    "eventSignatures": ["Transfer(address,address,uint256)"]
  }
}

2.2 Storage & Crypto Hygiene
	•	Store locally (browser extension storage / mobile keystore) under a namespaced key.
	•	Encrypt notes and labels at rest (optional) with the wallet vault key.
	•	Non-secret metadata (chainId, tags) can remain plaintext.
	•	Maintain a content hash of each record for tamper detection.

⸻

3) Import / Export

3.1 Supported Inputs
	•	Paste Address / QR Code (bech32m): Validate HRP + checksum, then store.
	•	CSV (label,address,network,tags…): Strict header row, UTF-8.
	•	JSON Lines (NDJSON): One record per line (recommended for bulk).

Example CSV:

label,address,network,tags,watchOnly
Ops Treasury,anim1qxyz…,animica.testnet,"treasury;ops",true
Counter (dev),anim1qabc…,animica.localnet,"demo;contract",true

3.2 Export
	•	JSON Lines with a top-level export manifest:

{
  "exportFormat": "animica-address-book-v1",
  "exportedAt": "2025-01-06T10:10:00Z",
  "records": 42
}

	•	Offer redaction options: strip notes, strip tags, strip alert rules.

⸻

4) Fetching & Verification

4.1 Trust Tiers
	1.	Direct RPC (unverified): Fast UI updates; mark status as “unverified”.
	2.	Multi-RPC consensus: Query ≥2 providers; quorum on results; flag disagreements.
	3.	Light-verified: Use header chain + inclusion proofs to mark data verified.

The UI should surface a Status Badge:
	•	Healthy (verified): Data backed by light proof.
	•	Degraded (quorum only): Multiple RPCs agree, but not locally proven.
	•	Unverified: Single RPC source; show disclaimer.

4.2 Balances & Nonce (EOA)
	•	Query state.getBalance and state.getNonce (RPC).
	•	Optionally re-query via a second RPC for consensus.
	•	For light verification, pin the header height the response claims and verify the header chain up to that height.

4.3 Transactions & Receipts
	•	Fetch tx by hash; obtain block hash/number and receipt.
	•	Verify:
	1.	The block header is in the verified header chain.
	2.	The receipts root in that header matches the included receipt.
	•	SDK helpers: sdk/*/light_client/verify (Python/TS/Rust) can drive inclusion checks.

4.4 Contracts & Events
	•	For verified contracts, store the code hash and (optional) ABI reference.
	•	Event logs:
	•	Check bloom/logs root ↔ header receiptsRoot (see docs/spec/RECEIPTS_EVENTS.md).
	•	Light-verify inclusion where possible; otherwise degraded state.

4.5 Data Availability (Optional)
	•	If a watch-only flow references blobs (e.g., contract artifacts pinned in DA), verify:
	•	The blob commitment is under the header’s DA root.
	•	Use sdk/*/da/client for proof retrieval & light verification.

⸻

5) UX Flows

5.1 Add Watch-Only Address
	1.	Paste/Scan address → validate checksum & HRP.
	2.	Choose network (default from website/chains/index.json).
	3.	Set label, color, tags.
	4.	Confirm watch-only toggle ⇒ disable send/sign.
	5.	(Optional) Alerts: outbound tx, balance thresholds, event signatures.

5.2 Attach a Signer Later
	•	Convert a watch-only EOA into a signing account by:
	•	Unlocking a local vault (extension/mobile)
	•	Or attaching a hardware key or WebAuthn credential.
	•	Always show a dangerous change warning when enabling send/sign.

5.3 Cross-Network Portfolio
	•	List same address on multiple networks (main/test/local).
	•	Aggregate balances with per-network row and a converted total (FX rates optional).

5.4 Alerts & Notifications
	•	Implement debounced polling (WS subscribe preferred where available).
	•	For light-verified alerts, only trigger after inclusion proof passes; otherwise mark as prelim.

⸻

6) CLI / SDK Examples

6.1 TypeScript (balance + light verify)

import { HttpClient } from "@animica/sdk/src/rpc/http";
import { verifyHeaders } from "@animica/sdk/src/light_client/verify";

const rpc = new HttpClient({ url: process.env.RPC_URL! });
const head = await rpc.call("chain.getHead", []);
const ok = await verifyHeaders(head); // pins header chain locally

const balance = await rpc.call("state.getBalance", ["anim1qxyz…"]);
console.log({ height: head.height, verified: ok, balance });

6.2 Python (receipt inclusion)

from omni_sdk.rpc.http import HttpClient
from omni_sdk.light_client.verify import verify_headers, verify_receipt

rpc = HttpClient(url=os.environ["RPC_URL"])
head = rpc.call("chain.getHead", [])
verify_headers(head)

tx = rpc.call("tx.getTransactionByHash", ["0xabc…"])
rcpt = rpc.call("tx.getTransactionReceipt", ["0xabc…"])
assert verify_receipt(head, rcpt), "Receipt failed light verification"


⸻

7) Security & Privacy
	•	No private keys in watch-only mode. Ensure TX send/sign UI is disabled.
	•	Domain separation for any approval/sign messages (see docs/spec/ENCODING.md).
	•	Rate limit RPC to avoid leaking behavior patterns; optional anonymized proxy.
	•	Local encryption for notes/labels; redact on export by default.
	•	Phishing-resistant deep links: use InlineLink component rules from the website to set rel="noopener noreferrer" & target="_blank".

⸻

8) Edge Cases
	•	Reorgs: If a tracked tx is reorged out, mark status and re-verify on the new tip.
	•	Code Upgrades: If a contract code hash changes (via upgrade proxy), notify user and require ABI re-ack.
	•	Multiple HRPs / Chains: Reject addresses with mismatched HRP vs chosen network.
	•	Alias/Multisig: For AA or multisig schemes, expose implementation address and policy summary if known.

⸻

9) Testing Checklist
	•	Import CSV & JSONL (labels with unicode).
	•	Validate bech32m HRP and checksum; reject malformed.
	•	Watch-only cannot send/sign; buttons disabled.
	•	Multi-RPC quorum disagreement → “Degraded” status.
	•	Light-verification success/failure paths surfaced in UI.
	•	Reorg handling: tx status transitions correctly.
	•	Export redaction works as advertised.

⸻

10) Minimal API (Internal)

Storage key: wallet.addressBook.v1

type AddressRecord = {
  id: string;
  address: string;
  network: string;
  chainId: number;
  kind: "eoa" | "contract";
  watchOnly: boolean;
  label?: string;
  tags?: string[];
  color?: string;
  algId?: string | null;
  codeHash?: string | null;
  abiRef?: string | null;
  notes?: string;
  alerts?: {
    txOutbound?: boolean;
    balanceBelow?: string | null;
    eventSignatures?: string[];
  };
  createdAt: string;
  updatedAt: string;
};


⸻

11) Quickstart
	1.	Add address (anim1…) as watch-only on Animica Testnet.
	2.	Enable multi-RPC in settings for consensus fetch.
	3.	Turn on light verification (downloads recent headers).
	4.	Subscribe to outbound tx alerts; simulate a small tx (no signing).
	5.	Verify that the Status Badge shows Healthy (verified) once inclusion is proven.

⸻

12) References
	•	docs/spec/LIGHT_CLIENT.md — light verification details
	•	docs/spec/RECEIPTS_EVENTS.md — receipts, logs, and blooms
	•	website/src/components/StatusBadge.tsx — status indicator component
	•	website/chains/* — networks & RPC metadata
	•	SDKs: sdk/typescript, sdk/python, sdk/rust (light verify helpers)

