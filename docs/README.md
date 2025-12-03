# Animica Docs

This `docs/` tree is the **single source of truth** for concepts, specs, and how-to guides across the Animica stack. The public website mirrors a curated subset via `website/scripts/sync_docs_from_repo.mjs`. Keep this folder authoritative, reproducible, and friendly to contributors.

---

## Getting Started

- **Run a node (devnet stack):** Follow [quickstart-devnet.md](quickstart-devnet.md) to clone the repo, create a venv, and launch the node + miner + dashboard with `ops/run.sh --profile devnet all`.
- **Use the wallet:** The Flutter wallet quickstart in [wallet/README.md](../wallet/README.md#quickstart-any-platform) shows how to install deps and launch on desktop/mobile/web with `flutter run`.
- **Write a Python contract:** [dev/CONTRACTS_START.md](dev/CONTRACTS_START.md) walks through building, compiling, and deploying the Counter contract on VM(Py).
- **Connect to devnet/mainnet RPC:** [rpc-quickstart.md](rpc-quickstart.md) covers local JSON-RPC usage (HTTP/WS); chain metadata in [chains/animica.testnet.json](../chains/animica.testnet.json) and [chains/animica.mainnet.json](../chains/animica.mainnet.json) list public endpoints.

---

## Documentation Philosophy

- **One repo, one truth.** Specs, reference, and examples live next to the code they describe.
- **Task-oriented.** Lead with “how do I…?” guides; link deeper concept and reference pages.
- **Runnable examples.** Prefer copy-pasteable commands and minimal fixtures over prose.
- **Deterministic builds.** Pin versions and include hashes/commit SHAs where relevant.
- **Security-first.** Call out trust assumptions, attack surfaces, and verification steps.

---

## Audience

- **Builders:** node operators, smart-contract devs (VM(Py)), SDK users (Py/TS/Rust).
- **Researchers:** consensus/PoIES, DA/NMT/erasure, ZK (Groth16/PLONK/STARK), PQ crypto.
- **Integrators:** wallet/extension, studio, explorer, AICF providers.

---

## Folder Structure (suggested)

> Create subfolders as needed; keep paths stable—URLs may depend on them.

docs/
overview/                # high-level intro, architecture, FAQ
concepts/                # PoIES, DA, VM(Py), PQ, ZK schemes, randomness beacon
specs/                   # normative specs; link to /spec/ sources
modules/
core/                  # core/ overview & data types
consensus/             # policy, scorer, Θ retarget, fork choice
proofs/                # proof kinds, metrics, nullifiers
rpc/                   # JSON-RPC surface, OpenRPC notes
p2p/                   # transport, gossip, sync flows
mining/                # templates, shares, stratum/ws
mempool/               # fees, admission, eviction
da/                    # NMT, erasure, availability sampling, APIs
execution/             # state machine, gas, receipts
vm_py/                 # compiler, IR, runtime, stdlib
capabilities/          # syscalls (AI/Quantum, blob, zk.verify, randomness)
aicf/                  # provider registry, staking, payouts, SLA
randomness/            # commit→reveal→VDF→mix
wallet-extension/      # MV3 provider & UX
sdk/                   # Python / TypeScript / Rust users’ guides
studio-wasm/           # in-browser simulator
studio-services/       # deploy/verify/faucet proxy
studio-web/            # web IDE user guide
installers/            # desktop packaging + signing (wallet/explorer)
zk/                    # verifiers, adapters, registry, integration hooks
how-to/                  # task guides (deploy, verify, mine, run devnet, add circuits)
reference/               # JSON-RPC, ABI, CLI manpages, error codes
ops/                     # runbook, metrics, dashboards, backups, upgrades
security/                # threat models, trusted setup notes, hardening
release-notes/           # user-visible changes (links to tags/CHANGELOGs)

---

## Source Mappings

- **Specs:** Canonical machine-readable sources live in `/spec/`. Mirror or link them here with context and examples.
- **OpenRPC/ABI:** See `/spec/openrpc.json` and `/spec/abi.schema.json`. Reference pages should show field semantics and request/response examples.
- **ZK:** High-level docs should point to `/zk/docs/*` and `/zk/registry/*` for VK pinning and formats.

---

## Authoring Conventions

### File & Frontmatter
- Use lowercase, hyphenated filenames (e.g., `how-to/run-a-node.md`).
- Optional frontmatter (for site renderers that support it):
  ```yaml
  ---
  title: Run a Node
  description: Start a devnet node from genesis and inspect the head.
  tags: [node, devnet]
  ---

Writing Style
	•	Use plain, direct language. Prefer active voice. Define acronyms on first use.
	•	Explain why, then how. Start with a minimal working example (MWE).
	•	Use inclusive language; avoid idioms that don’t translate well.

Code & Commands
	•	Shell:

omni -V
python -m core.boot --genesis core/genesis/genesis.json --db sqlite:///animica.db


	•	JSON (compact but readable); include comments with separate blocks, not in JSON.
	•	Mark expected output with comments or separate “Output” block.

Security Callouts

Security: Explicitly document trust roots, keys, circuits/VKs, and assumptions.

Diagrams
	•	Prefer Mermaid for architecture/state flows:

flowchart LR
  tx[TX] -->|CBOR decode| exec[Execution]
  exec --> receipts[Receipts]
  proofs --> consensus
  consensus --> head[Head]


	•	Link out to larger diagrams or include SVG assets when needed.

Links
	•	Relative links within docs/ (stable).
	•	For code, link to files with commit SHAs when referencing specific lines.

⸻

Reproducibility & Versioning
	•	Pin tool versions in examples (e.g., omni-sdk==X.Y.Z, @animica/sdk@x.y.z).
	•	When referencing proofs/VKs, include content hashes and circuit IDs.
	•	If behavior changed since a tag, add a Compatibility section explaining deltas.

⸻

Local Preview (optional)

If rendering via the website:
	1.	Ensure Node LTS installed.
	2.	Set env in website/.env as needed.
	3.	Run sync script:

node website/scripts/sync_docs_from_repo.mjs


	4.	Start site:

npm -w website install
npm -w website run dev



⸻

How to Contribute
	1.	Issues & Proposals
	•	File an issue with: purpose, audience, scope, and acceptance criteria.
	•	Tag docs and the affected module labels (e.g., consensus, zk).
	2.	Branch & PR
	•	Branch naming: docs/<area>-<short-topic> (e.g., docs/zk-groth16-verify).
	•	Keep PRs focused; include screenshots for UI docs and exact commands for guides.
	3.	Checks before review
	•	Spell/grammar pass (any editor or CI plugin).
	•	Validate code blocks: commands run on a fresh checkout, fixtures present.
	•	Run site sync (if applicable) and check for broken links:

node website/scripts/check_links.mjs


	4.	Review
	•	Tech review: module maintainer confirms correctness.
	•	Security review: required for anything touching keys, proofs, VKs, or setup.
	•	Style review: consistency with this guide.
	5.	Merging
	•	Squash & merge with a clear message.
	•	If user-visible, add an entry under docs/release-notes/ and link in the website changelog.

⸻

Templates

Use these starter templates when adding pages:
	•	Concept → docs/concepts/<topic>.md

# <Concept>
What problem it solves, where it fits, invariants, trade-offs.


	•	How-to → docs/how-to/<task>.md

# Goal
## Prereqs
## Steps
## Verify
## Troubleshooting


	•	Reference → docs/reference/<surface>.md

# API/CLI/Schema
Fields, types, error codes, examples.



⸻

License & Attribution

Follow the repo’s root license. When including third-party material (diagrams, excerpts), ensure licenses allow redistribution and add credits in a Credits section or LICENSE-THIRD-PARTY.md.

⸻

Maintainers

Each module’s lead maintains its docs:
	•	Core/Consensus/Execution: Core team
	•	DA/Proofs/ZK: Cryptography team
	•	P2P/RPC/Mempool/Mining: Networking & Node team
	•	Wallet/Studio/SDK/Installers/Website: Apps & Platform team

Questions? Open an issue with the docs label. PRs welcome. ✍️
