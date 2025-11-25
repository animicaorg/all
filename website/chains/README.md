# website/chains/

This folder mirrors the repository’s top-level **`/chains/`** package and provides a
simple, declarative source of truth for chain metadata used by the website:

- The **Network** page (`/network`) lists these chains.
- The **status** and **deep-links** components read RPC + Explorer URLs from here.
- The API endpoint **`/api/chainmeta.json`** aggregates every `*.json` in this folder.

> If your repo already has a top-level `/chains/`, keep this directory **in sync**.
> You can symlink, copy during CI, or set `CHAINS_DIR` to point at the canonical
> location (see below).

---

## File format

Each chain is one JSON file named after its **id** (the filename stem). Minimal shape:

```json
{
  "id": "animica-mainnet",
  "chainId": 1,
  "name": "Animica Mainnet",
  "rpc": ["https://rpc.animica.org"],
  "explorer": "https://explorer.animica.org",
  "testnet": false,
  "docs": "https://docs.animica.org/network",
  "faucets": []
}

Fields
	•	id (string) — stable identifier; must match the filename (without .json).
	•	chainId (number) — numeric chain id. Prefer CAIP-2 alignment where applicable.
	•	name (string) — human-readable chain name.
	•	rpc (string or string[]) — one or more HTTPS RPC endpoints.
	•	explorer (string) — base URL to the explorer (homepage).
	•	testnet (boolean, optional) — mark non-mainnet chains.
	•	docs (string, optional) — network documentation link.
	•	faucets (string[], optional) — faucet links for dev/test networks.
	•	Additional fields are allowed; they will be passed through to consumers.

⸻

How the website consumes this
	•	Build-time imports: src/config/chains.ts loads from /chains/ (typed) to render pages.
	•	Runtime API: src/pages/api/chainmeta.json.ts reads all *.json here and
returns a merged list. It supports ?id=<id> filters and conditional caching.

Environment override:
	•	CHAINS_DIR — absolute/relative path to the chains directory.
If set, the API will read from there instead of website/chains/.

Example:

CHAINS_DIR=../chains pnpm dev
# /api/chainmeta.json now serves from ../chains


⸻

Conventions
	•	One file per chain. The filename is the canonical id (e.g. animica-mainnet.json).
	•	JSON only. Avoid comments and trailing commas.
	•	HTTPS endpoints. Prefer TLS for all RPC/Explorer URLs.
	•	Stable ids. Changing id or filename is a breaking change for deep links.

⸻

Adding a new chain (checklist)
	1.	Copy the template below into website/chains/<your-id>.json and fill in values.
	2.	Run the site locally and verify:
	•	/network lists your chain with correct links.
	•	/api/chainmeta.json?id=<your-id> returns your record.
	3.	Ensure the RPC responds to basic chain.getHead (used by status widgets).
	4.	Commit with a clear message: chains: add <your chain>.

Template:

{
  "id": "your-chain-id",
  "chainId": 0,
  "name": "Your Chain Name",
  "rpc": ["https://rpc.yourchain.example"],
  "explorer": "https://explorer.yourchain.example",
  "testnet": true,
  "docs": "https://docs.yourchain.example",
  "faucets": ["https://faucet.yourchain.example"]
}


⸻

Troubleshooting
	•	404 from /api/chainmeta.json
The directory doesn’t exist or is empty. Create website/chains/ or set CHAINS_DIR.
	•	TPS/Status shows null
The status endpoint infers TPS from recent blocks. Ensure your RPC supports:
	•	chain.getHead
	•	chain.getBlockByNumber (optionally with { includeTxs: true })
	•	CORS blocked calls (browser)
The website fetches data server-side where possible. Use the provided API
routes (/api/*) as proxies when embedding RPC calls in client code.

⸻

Keeping in sync with the canonical registry

If the project maintains a canonical /chains/ at repo root:
	•	Prefer that as the source of truth.
	•	Point the website’s runtime to it with CHAINS_DIR in dev and prod.
	•	Optionally add a CI step to copy/symlink into website/chains/ before build.

