# Animica Rust SDK Starter

A batteries-included, minimal Rust project template for building command-line tools and services on top of **Animica**. It demonstrates how to:

- Connect to a node via HTTP/WS using `animica-sdk`
- Read chain params and the current head
- Send a simple transaction (e.g., deploy/call demo contracts)
- Subscribe to real-time events (new heads, pending txs)
- Structure a clean, testable Rust project with `.env` configuration and feature flags

This starter is rendered by the **Animica Templates Engine** and parameterized by a small set of variables (crate/project name, RPC URL, chain id, etc.).

---

## Prerequisites

- **Rust toolchain** (1.74+ recommended) with `cargo`
- **Tokio** runtime knowledge (the SDK uses async I/O)
- Access to an **Animica node** (local devnet or public testnet):
  - RPC HTTP (e.g., `http://localhost:8545`)
  - Optional: WebSocket (e.g., `ws://localhost:8546`) for subscriptions
- Optional tools:
  - `just` (or `make`) if you like task runners
  - `direnv` to auto-load `.env` in your shell

---

## Rendering this template

You can render interactively (recommended) or non-interactively (CI). The engine understands the `variables.json` in this directory and will prompt for missing values.

### Interactive

```bash
python -m templates.engine.cli render templates/sdk-rs-starter --out ./my-animica-rs

You’ll be prompted for:
	•	project_slug (directory name, e.g. my-animica-rs)
	•	crate_name (Rust crate name, e.g. my_animica_rs)
	•	description
	•	author_name, author_email
	•	org (for README badges/links)
	•	rpc_url (default http://localhost:8545)
	•	chain_id (e.g. 1337 for devnet)

Non-interactive

python -m templates.engine.cli render templates/sdk-rs-starter \
  --out ./my-animica-rs \
  --set project_slug=my-animica-rs \
  --set crate_name=my_animica_rs \
  --set description="Rust CLI for Animica" \
  --set author_name="Ada Lovelace" \
  --set author_email="ada@example.org" \
  --set org=your-org \
  --set rpc_url=http://localhost:8545 \
  --set chain_id=1337


⸻

What gets generated

The rendered project is intentionally small but complete. You’ll see something like:

{{project_slug}}/
├─ Cargo.toml
├─ README.md
├─ .env.example
├─ .gitignore
├─ src/
│  ├─ main.rs                 # CLI entrypoint with a few subcommands
│  └─ lib.rs                  # (optional) reusable helpers if you prefer a lib/bin split
├─ examples/
│  └─ deploy_counter.rs       # Example: deploy and call the “Counter” demo contract
└─ tests/
   └─ smoke.rs                # Basic smoke tests (RPC head, params)

Note: Exact file list can evolve. The README always reflects the intended layout; your template version may include additional helpers or split code differently (e.g., rpc.rs, wallet.rs modules).

⸻

Configure

Copy the example environment:

cp .env.example .env

Edit to match your environment:

RPC_URL=http://localhost:8545
WS_URL=ws://localhost:8546
CHAIN_ID=1337
# Optional PQ features or signer hints can be added later as your app matures.

You can override any value with CLI flags in the sample binaries (see below).

⸻

Build & Run

Build

cargo build

Run the CLI

The starter CLI includes a few subcommands to help you verify connectivity quickly.

# Print chain params (subset) and current head
cargo run -- head --rpc $RPC_URL

# Subscribe to new heads over WS (Ctrl-C to stop)
cargo run -- subscribe --ws $WS_URL

# (Optional) Deploy the demo Counter contract using an example script
cargo run -- deploy-counter --rpc $RPC_URL --chain-id $CHAIN_ID

If you enable a .env, you can omit flags:

# With .env loaded (direnv or manual `export`), flags become optional
cargo run -- head


⸻

Using the Rust SDK

This starter wires in the Rust SDK crate (animica-sdk) and shows idiomatic async usage. The exact API may change slightly across versions, but the patterns below hold:

Example: read the head

use anyhow::Result;
use animica_sdk::rpc::http::Client; // HTTP JSON-RPC client
use animica_sdk::types::Head;

#[tokio::main]
async fn main() -> Result<()> {
    let rpc = std::env::var("RPC_URL").unwrap_or_else(|_| "http://localhost:8545".into());
    let client = Client::new(&rpc)?;
    let head: Head = client.chain().get_head().await?;
    println!("height={} hash={}", head.height, head.hash);
    Ok(())
}

Example: subscribe to new heads

use anyhow::Result;
use animica_sdk::rpc::ws::Client as WsClient; // WS subscribe client
use animica_sdk::types::Head;

#[tokio::main]
async fn main() -> Result<()> {
    let ws = std::env::var("WS_URL").unwrap_or_else(|_| "ws://localhost:8546".into());
    let mut sub = WsClient::connect(&ws).await?
        .subscribe_new_heads()
        .await?;
    while let Some(head) = sub.next().await {
        let Head { height, hash, .. } = head?;
        eprintln!("[newHead] #{height} {hash}");
    }
    Ok(())
}

The starter’s src/main.rs already includes working commands that encapsulate patterns like the above. Use them as a reference, then shape the CLI around your needs.

⸻

Common flows
	•	Quick connectivity check
	•	cargo run -- head — prints current head and exits.
	•	cargo run -- subscribe — tails new heads.
	•	Deploy example contract
	•	cargo run -- deploy-counter — uses a built-in artifact of the Counter demo and submits a signed tx (on dev/test chains). See examples/deploy_counter.rs.
	•	Call a method
	•	cargo run -- call --to <address> --fn get --args "" — thin wrapper around SDK call/send helpers.

⸻

Features & flags

The Rust SDK exposes feature flags to tailor footprint and crypto backends:
	•	pq — enable PQ signers (may require liboqs/OS packages or vendored bindings if you use native PQ in your app).
	•	wasm — if targeting WASM (browser/wasm-edge). The starter focuses on native; add this flag if you later build to wasm32.

Enable features in Cargo.toml:

[dependencies]
animica-sdk = { version = "0.1", features = ["pq"] }

If you’re developing the SDK and the starter in a single monorepo, consider a [patch.crates-io] path override to your local sdk/rust folder.

⸻

Testing

Basic smoke tests are included:

cargo test

They assume RPC_URL points to a reachable node. You can mark network tests with a -- --ignored strategy if you prefer.

⸻

Project scripts (optional)

If you like automation, add a simple justfile:

# justfile (optional)
head:
    cargo run -- head
subscribe:
    cargo run -- subscribe
deploy-counter:
    cargo run -- deploy-counter
fmt:
    cargo fmt
lint:
    cargo clippy -- -D warnings

Then run just head, just deploy-counter, etc.

⸻

Production builds

cargo build --release
# Binary lands in target/release/<crate_name>

For minimal images, use a distroless or scratch-like multi-stage Docker build with FROM rust:alpine (builder) and FROM gcr.io/distroless/cc (runner), or your org’s baseline.

⸻

Troubleshooting
	•	Cannot connect: Check RPC_URL/WS_URL, CORS/host firewall, or if your node is still booting.
	•	TLS issues: For HTTPS/WSS to remote endpoints, ensure system CA bundles are present in your container/host.
	•	PQ signer errors: If you enable pq and rely on native libs, make sure liboqs (or your chosen backend) is available for your platform, or disable the feature for non-signing clients.
	•	Async runtime panics: Ensure you run under tokio (the starter does). Avoid creating multiple runtimes unless you know you need them.

⸻

Extending the starter

Some common next steps:
	•	Add command modules: split src/main.rs subcommands into files in src/cmd/.
	•	Implement a stateful indexer: poll blocks, persist to SQLite/ClickHouse, add metrics.
	•	Build a service: expose REST/GraphQL around your Animica interactions.
	•	Wire observability: tracing, Prometheus metrics, and structured logs.

⸻

License

This template is provided under the same permissive license as the main Animica repository. See the root LICENSE in your checkout for details.

