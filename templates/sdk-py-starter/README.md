# sdk-py-starter — Animica Python SDK Starter Template

A turnkey template for bootstrapping a **Python client/app** that talks to an Animica node (HTTP JSON-RPC + WebSocket), deploys and calls contracts, listens to new heads/events, and works out-of-the-box against **devnet**, **testnet**, or a local node.

This template is intentionally minimal but batteries-included: virtualenv setup, pinned deps, example scripts, simple `.env` configuration, and a sane project layout you can grow into a real application.

---

## What this template generates

After rendering, you’ll get a project that looks like:

{{project_slug}}/
├─ README.md
├─ .gitignore
├─ .env.example                  # RPC_HTTP_URL, RPC_WS_URL, CHAIN_ID
├─ pyproject.toml                # build config (PEP 621), ruff/mypy hooks
├─ requirements.txt              # runtime + sdk pins
├─ {{package_name}}/             # your importable package
│  ├─ init.py
│  └─ client.py                  # tiny wrapper around omni_sdk RPC
└─ examples/
├─ deploy_counter.py          # deploy & call the canonical Counter
├─ send_transfer.py           # build/sign/send a transfer tx
└─ subscribe_new_heads.py     # WS subscription demo (auto-reconnect)

> The exact set of files may expand over time, but the shape above reflects the intended “getting started” experience.

---

## Template inputs (variables)

When rendering, you can customize:

- **`project_slug`** *(string, default: `sdk-py-starter`)*  
  Directory/repo name. Lowercase letters, digits, `-` or `_`.

- **`package_name`** *(string, default: `sdk_py_starter`)*  
  Top-level Python package import name (PEP 8 compatible).

- **`author_name`** *(string, optional)*  
  Used in `pyproject.toml`.

- **`author_email`** *(email, optional)*  
  Used in `pyproject.toml`.

- **`description`** *(string, default provided)*  
  One-liner for README and package metadata.

- **`license`** *(enum: Apache-2.0 | MIT | BSD-3-Clause | Unlicense, default: Apache-2.0)*

- **`version`** *(semver, default: `0.1.0`)*

- **`rpc_http_url`** *(string, default: `http://127.0.0.1:8545/rpc`)*  
  HTTP JSON-RPC endpoint.

- **`rpc_ws_url`** *(string, default: `ws://127.0.0.1:8545/ws`)*  
  WebSocket endpoint for subscriptions.

- **`chain_id`** *(integer, default: `1337`)*  
  Chain ID (`1` mainnet, `2` testnet, `1337` devnet).

- **`include_examples`** *(boolean, default: `true`)*  
  Include example scripts.

> These correspond to `templates/sdk-py-starter/variables.json`. Override via `--set key=value` or an answers file.

---

## Rendering the template

You can render with the template engine that ships in this repo:

```bash
# From repo root
python -m templates.engine.cli render \
  sdk-py-starter \
  --out ./my-client \
  --set project_slug=my-client \
        package_name=my_client \
        chain_id=1337 \
        rpc_http_url=http://127.0.0.1:8545/rpc \
        rpc_ws_url=ws://127.0.0.1:8545/ws

Alternative: point directly at the directory path instead of the short id:

python -m templates.engine.cli render ./templates/sdk-py-starter --out ./my-client

The renderer validates your inputs against the schema and will fail fast with helpful messages if something is off (e.g., invalid slug or version).

⸻

Getting started (after render)

cd my-client

# 1) Create and activate a virtualenv
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2) Install dependencies
pip install -U pip
pip install -r requirements.txt

# 3) Configure RPC/chain in a local .env
cp .env.example .env
# Edit .env to match your node (devnet/testnet/local)

Try the examples

Deploy and interact with the canonical Counter contract:

python examples/deploy_counter.py

Send a simple transfer (requires a funded key configured in the example script or via environment):

python examples/send_transfer.py

Subscribe to new heads over WebSocket:

python examples/subscribe_new_heads.py

All examples use the Python SDK (omni_sdk) under the hood, handling JSON-RPC calls, CBOR encoding, PQ address formats, and receipt polling.

⸻

Configuration

The generated .env.example contains:

RPC_HTTP_URL=http://127.0.0.1:8545/rpc
RPC_WS_URL=ws://127.0.0.1:8545/ws
CHAIN_ID=1337

	•	Devnet (local Docker or tests/devnet): use the defaults above.
	•	Public testnet: swap in the published endpoints and set CHAIN_ID=2.
	•	Mainnet (when live): set CHAIN_ID=1 and update URLs.

You can also override at runtime via environment variables or CLI flags in your own scripts.

⸻

What’s inside (tech notes)
	•	omni_sdk: the Animica Python SDK — JSON-RPC client, typed helpers for tx/blocks, contract ABI client, DA/AICF/Randomness clients, and utilities.
	•	CBOR: all tx/header/proof encoding matches the repo’s spec/*.cddl.
	•	Addresses: bech32m anim1… payloads (PQ aware).
	•	Typing & Linting: mypy + ruff preconfigured in pyproject.toml.

⸻

Common workflows

Install as editable while building your own package code:

pip install -e .

Run unit tests (if you add pytest):

pytest -q

Format / lint (ruff):

ruff check .
ruff format .

Publish (optional):

python -m build
# twine upload dist/*


⸻

Troubleshooting
	•	Connection refused: Verify your node is up and that /rpc and /ws endpoints are reachable (CORS if browser-based).
	•	ChainId mismatch: Ensure CHAIN_ID in .env matches the node’s chain.
	•	Insufficient balance: Fund the sender on devnet/testnet (see faucet or pre-funded keys in tests/devnet).
	•	WebSocket drops: The examples auto-reconnect, but confirm your WS URL and any proxies in front of the node.

⸻

License

The generated project uses the license you select at render time. This template’s content is provided under the repository’s top-level license unless otherwise noted.

