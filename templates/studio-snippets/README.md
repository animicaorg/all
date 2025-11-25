# Studio Snippets

A curated, copy-paste friendly library of small, **production-oriented code snippets** designed to accelerate common Animica workflows in **Studio Web**, local editors, and docs. Snippets cover contracts (Python VM), SDK usage (TypeScript / Python / Rust), JSON-RPC, Data Availability (DA), AICF (AI/Quantum), Randomness beacon, and testing patterns.

These snippets are intentionally **short**, **idiomatic**, and **determinism-aware**. They double as live docs: each snippet shows the minimum safe shape to complete a task, with comments highlighting constraints and gotchas.

---

## What these are good for

- Pasting into **Studio Web** editors (Python contract pane, TS/Py SDK scratchpads).
- Inline help in docs/tutorials and sample repos.
- Kicking off integrations (DA posting, job enqueue, beacon reads, tx build/submit).
- Teaching the VM’s deterministic subset (stdlib, events, storage patterns).
- Demonstrating **post-quantum** address/signature flows at the edges (wallet/SDK).

> Snippets are not “templates”. If you want project scaffolds (multi-file, tests, scripts), see `templates/` (e.g., `contract-python-basic`, `dapp-react-ts`, `fullstack-monorepo`).

---

## Organization & scope

This directory may contain categorized snippet sets in subfolders (optional) or flat files referenced by Studio:

templates/studio-snippets/
├─ README.md                      # this file
├─ contracts-python.snippets.json # VM contract patterns
├─ sdk-typescript.snippets.json   # @animica/sdk examples
├─ sdk-python.snippets.json       # omni_sdk examples
├─ sdk-rust.snippets.json         # animica-sdk examples
├─ rpc-curl.snippets.json         # curl/HTTP JSON-RPC
├─ da.snippets.json               # post/get/proof flows
├─ aicf.snippets.json             # AI/Quantum enqueue/consume
├─ randomness.snippets.json       # commit/reveal/beacon read
└─ testing.snippets.json          # pytest/property/bench quickies

Studio can ingest **any** of the above files; each file bundles multiple snippets.

---

## Snippet format

Snippets are JSON files with this shape:

```jsonc
{
  "$schema": "https://example.animica/specs/studio-snippets.schema.json",
  "language": "python | typescript | rust | json | bash",
  "snippets": [
    {
      "id": "contract.counter.inc",
      "title": "Counter: increment + event",
      "description": "Minimal deterministic mutator with gas-aware storage and event emission.",
      "tags": ["contracts", "storage", "events"],
      "scope": "contracts/python",     // logical area for Studio palette
      "body": [
        "# Deterministic counter increment",
        "from stdlib import storage, events, abi",
        "",
        "def inc() -> None:",
        "    # Read, add, store (saturating on overflow handled in safe libs if used)",
        "    n = storage.get(b\"counter\") or 0",
        "    n = n + 1",
        "    storage.set(b\"counter\", n)",
        "    events.emit(b\"Inc\", {b\"new\": n})"
      ]
    }
  ]
}

	•	body is an array of lines; Studio joins with \n.
	•	You may use VSCode-style tabstops (${1:name}) and choices (${2|optionA,optionB|}) for interactive insertion.
	•	Studio also expands context variables written as {{VAR_NAME}} (see below).

⸻

Context variables (Studio expansion)

When snippets are inserted inside Studio, the following variables are available:

Variable	Example	Notes
{{CHAIN_ID}}	1	From configured network
{{RPC_URL}}	https://rpc.dev.animica.xyz	From Studio settings
{{ACCOUNT_ADDRESS}}	anim1...	From connected wallet
{{CONTRACT_ADDRESS}}	anim1...	From last deployment / selection
{{DA_NAMESPACE}}	24	Default namespace for demos
{{AICF_MODEL}}	animica/llama-mini	AICF demo model id (editable)
{{QUANTUM_TRAPS}}	{"depth":8,"shots":512}	Reasonable defaults
{{BEACON_ROUND}}	current	Can be numeric too

If a variable is unset, Studio will prompt or leave it as-is for manual editing.

⸻

Example snippets (ready to paste)

Below are representative examples mirroring what lives in the JSON snippet sets.

1) Python VM — read-only getter

# Read-only counter getter: deterministic, no side effects
from stdlib import storage

def get() -> int:
    return storage.get(b"counter") or 0

2) Python VM — safe transfer pattern (treasury API)

# Transfer using treasury API (local sim-safe)
from stdlib import treasury, abi

def pay(to: bytes, amount: int) -> None:
    abi.require(amount >= 0, b"amount<0")
    ok = treasury.transfer(to, amount)
    abi.require(ok, b"transfer failed")

3) TypeScript SDK — deploy + simple call

// Deploy and call using @animica/sdk
import { HttpClient, wallets, tx, contracts } from "@animica/sdk";

const rpc = new HttpClient({ url: "{{RPC_URL}}", chainId: {{CHAIN_ID}} });
const signer = await wallets.fromMnemonic(process.env.MNEMONIC!);

const manifest = /* import your manifest JSON */;
const code = /* Uint8Array of compiled IR */;

const deployTx = await tx.build.deploy({
  from: await signer.address(),
  manifest,
  code,
  gasPrice: 1n,
  gasLimit: 1_000_000n,
});

const signed = await signer.sign(deployTx);
const receipt = await tx.send.wait(rpc, signed);

const addr = receipt.contractAddress!;
const ctr = new contracts.Client(rpc, addr, manifest.abi);

console.log("counter.get =", await ctr.call("get", []));

4) Python SDK — DA: post blob → light verify

from omni_sdk.da.client import DAClient
from omni_sdk.randomness.client import RandomnessClient

rpc_url = "{{RPC_URL}}"
ns = int("{{DA_NAMESPACE}}")

da = DAClient(rpc_url)
data = b"hello, availability!"
commitment, receipt = da.post_blob(ns, data)

ok = da.verify_light(commitment, samples=32)
assert ok, "DA light verification failed"
print("DA commitment:", commitment)

5) Rust SDK — subscribe to new heads (WS)

use animica_sdk::rpc::ws::WsClient;
use futures_util::StreamExt;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let url = "{{RPC_URL}}/ws";
    let mut ws = WsClient::connect(url).await?;
    let mut sub = ws.subscribe_new_heads().await?;

    while let Some(head) = sub.next().await {
        println!("height={} hash={}", head.number, head.hash);
    }
    Ok(())
}

6) JSON-RPC (curl) — sendRawTransaction

# CBOR hex payload goes in 0x… below
curl -sS {{RPC_URL}}/rpc \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tx.sendRawTransaction","params":["0xfade..."]}'

7) AICF — enqueue AI job (TS)

import { AICFClient } from "@animica/sdk/aicf/client";

const aicf = new AICFClient({ url: "{{RPC_URL}}" });
const job = await aicf.enqueueAI({
  model: "{{AICF_MODEL}}",
  prompt: "Summarize: Animica PoIES in 3 bullet points.",
  maxTokens: 128,
});

console.log("task_id:", job.taskId);
// Contract will consume next block via capabilities/read_result

8) Randomness — commit/reveal (Python)

from omni_sdk.randomness.client import RandomnessClient
from omni_sdk.utils.bytes import hex_from

rc = RandomnessClient("{{RPC_URL}}")
salt = b"\x00"*16  # demo only; use OS RNG in practice
payload = b"my-commitment-payload"

commit = rc.commit(salt, payload)
print("commit:", hex_from(commit))

# ... after reveal window opens:
reveal = rc.reveal(salt, payload)
print("reveal:", hex_from(reveal))


⸻

Authoring guidelines

Keep them tiny. Prefer < 30 lines per snippet. Compose several small snippets rather than one large one.

Be deterministic. Contract snippets must use only allowed stdlib calls and patterns compatible with vm_py determinism rules. No time/IO, no ambient randomness (except the deterministic PRNG APIs).

Signal costs. Where gas could surprise, add a one-liner comment: # costs ~X gas, # OOG if not enough gasLimit.

Prefer real types. Show int, bytes, address strings, and ABI arrays/dicts in true forms used by SDK and runtime.

Tag well. Use tags that reflect domain and discoverability: ["contracts","events"], ["sdk","da"], ["aicf","quantum"].

No secrets. Never hardcode private keys, mnemonics, or API tokens.

⸻

Style conventions (per language)
	•	Python (contracts): deterministic subset only; imports exclusively via from stdlib import .... Use abi.require for defensive checks. Events: events.emit(b"Name", {b"key": value}). Storage keys are bytes.
	•	TypeScript: @animica/sdk helpers; use BigInt for amounts/gas; await receipts with tx.send.wait.
	•	Rust: feature-gate PQ signers; prefer anyhow for examples; use tokio async WS clients.
	•	JSON-RPC: show exact param shapes; hex is 0x prefixed; content types set to application/json.
	•	Bash/curl: pass -sS for clean logs; avoid jq unless necessary.

⸻

Using in Studio Web
	1.	Open a code pane (Contract Python, SDK TS/Py, or “Scratch”).
	2.	Press the Snippets button (or shortcut shown in the app) to open the palette.
	3.	Filter by scope and tags; insert with one click.
	4.	Fill any ${tabstops}; Studio will also expand {{VARIABLES}} from your session context.

You can also paste directly from this README—Studio won’t auto-expand variables from plain text; use the palette for that.

⸻

Adding your own snippets
	•	Clone this repo and add to the relevant *.snippets.json file or create a new categorized file.
	•	Ensure each id is globally unique (reverse-DNS style is fine: animica.sdk.ts.deploy).
	•	Run linters in your editor; JSON must be valid. Keep lines short and readable.
	•	Open a PR with a clear title (“Add: TS SDK deploy + call minimal snippet”).

⸻

Frequently asked

Q: Can I use Jinja-style {{ }} in contract code?
A: Yes, but only as literal bytes inside strings if you mean to keep them; Studio’s context expansion only happens when inserting via the Snippets palette.

Q: How do I get a compiled code buffer for deploy snippets?
A: Use vm_py CLI or studio-services /simulate to compile locally; most templates include scripts/build.py or make build.

Q: Are the snippets security-reviewed?
A: They follow the contracts/SECURITY.md checklist and deterministic subset. Treat them as starting points; assets/limits may differ per app.

⸻

Related references
	•	vm_py/specs/DETERMINISM.md — contract language limits
	•	execution/specs/GAS.md — gas accounting & refunds
	•	spec/openrpc.json — RPC surface
	•	da/specs/* — DA roots, proofs, DAS
	•	aicf/specs/* — compute lifecycle, SLA
	•	randomness/specs/* — commit-reveal + VDF

⸻

License

Snippets are MIT-licensed as part of this repository. See templates/_common/LICENSE.

