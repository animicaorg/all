# Animica SDK (Rust)

A fast, typed Rust SDK for building clients, tools, and services that talk to the **Animica** network. It includes:

- **JSON-RPC HTTP/WS clients** with retries and structured errors
- **Typed core models** (Tx, Receipt, Block, Head, ABI)
- **Wallet helpers** (mnemonic/keystore) and optional **post-quantum signers** (Dilithium3 / SPHINCS+) via `liboqs`
- **TX pipeline** (build → encode → send → await receipt)
- **Contracts** (ABI client, deployer, events)
- **DA / AICF / Randomness** service clients
- **Light verification** utilities (headers + DA light proofs)
- **Native** and **WASM** targets

> Crate name: `animica-sdk` • MSRV: 1.74+ • License: Apache-2.0 OR MIT

---

## Features

| Feature | Default | What it does |
|---|:---:|---|
| `native` | ✓ | Native networking: `reqwest` (HTTP), `tokio-tungstenite` (WS) |
| `wasm` |  | Browser/WASM networking via `gloo-net` and `wasm-bindgen` |
| `pq` |  | Post-quantum signers via `liboqs` (optional) |

You can combine `pq` with `native` or `wasm`.

---

## Install

```toml
# Cargo.toml
[dependencies]
animica-sdk = { path = "../sdk/rust", features = ["native"] } # or use git/release tag
# animica-sdk = { version = "0.1", features = ["native"] }

Enable PQ signers:

animica-sdk = { version = "0.1", features = ["native", "pq"] }

Target the browser (WASM):

animica-sdk = { version = "0.1", features = ["wasm"] }


⸻

Quickstart (HTTP RPC)

use animica_sdk::rpc::http::Client;
use animica_sdk::types::Head;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let rpc = Client::new("http://127.0.0.1:8545")?;
    let head: Head = rpc.get_head().await?;
    println!("height={} hash={}", head.number, head.hash);
    Ok(())
}

The HTTP client automatically applies bounded retries with jitter (idempotent GET-style methods).

⸻

Subscribe to newHeads (WebSocket)

use animica_sdk::rpc::ws::WsClient;
use futures::StreamExt;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let mut ws = WsClient::connect("ws://127.0.0.1:8546").await?;
    let mut sub = ws.subscribe_new_heads().await?;
    while let Some(head) = sub.next().await {
        println!("new head: {} @{}", head.hash, head.number);
    }
    Ok(())
}


⸻

Wallet & Addresses

use animica_sdk::wallet::mnemonic::Mnemonic;
use animica_sdk::wallet::keystore::Keystore;
use animica_sdk::address::Address; // bech32m anim1… addresses

fn main() -> anyhow::Result<()> {
    // Create mnemonic and derive the default account
    let m = Mnemonic::generate_24();
    let seed = m.to_seed("optional-passphrase");
    let (alg_id, pk, sk) = animica_sdk::wallet::signer::derive_default(&seed)?; // supports pq (if enabled)

    // Turn public key into an Animica address
    let addr = Address::from_pubkey(alg_id, &pk)?;
    println!("address: {}", addr.to_string());

    // Store encrypted keystore on disk
    let mut ks = Keystore::new_default_dir()?;
    ks.import("my-main", &sk, &addr, "strong-password")?;
    Ok(())
}

The exact key derivation & address rules follow the repo’s pq/ and spec/domains.yaml. If pq is not enabled, the signer APIs are present with stubs that return Unsupported errors.

⸻

Build and Send a Transaction

use animica_sdk::rpc::http::Client;
use animica_sdk::tx::{build, encode};
use animica_sdk::wallet::keystore::Keystore;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let rpc = Client::new("http://127.0.0.1:8545")?;
    let params = rpc.get_params().await?; // chain params (gas tables, limits)
    let chain_id = rpc.get_chain_id().await?;

    // Load key (Dilithium3 by default if pq is enabled)
    let ks = Keystore::new_default_dir()?;
    let (addr, signer) = ks.unlock("my-main", "strong-password")?;

    // Build transfer
    let tx = build::transfer()
        .from(&addr)
        .to("anim1xyz...")         // recipient
        .value(1_000)              // smallest units
        .nonce(rpc.get_nonce(&addr).await?)
        .gas_price(1)
        .gas_limit(50_000)
        .chain_id(chain_id)
        .finish()?;

    // Encode SignBytes and sign
    let sign_bytes = encode::sign_bytes(&tx, chain_id)?;
    let sig = signer.sign_domain_separated(&sign_bytes)?;

    // Submit
    let tx_hash = rpc.send_raw_transaction(&encode::to_cbor(&tx, &sig)?).await?;
    println!("submitted {}", tx_hash);

    // Await receipt
    let receipt = rpc.wait_for_receipt(&tx_hash).await?;
    println!("status={:?} gasUsed={}", receipt.status, receipt.gas_used);
    Ok(())
}


⸻

Contracts: Deploy & Call

use animica_sdk::contracts::{deployer::Deployer, client::ContractClient};
use animica_sdk::rpc::http::Client;
use animica_sdk::wallet::keystore::Keystore;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let rpc = Client::new("http://127.0.0.1:8545")?;
    let chain_id = rpc.get_chain_id().await?;
    let ks = Keystore::new_default_dir()?;
    let (addr, signer) = ks.unlock("my-main", "pw")?;

    // Deploy a package (manifest + code)
    let mut dep = Deployer::new(rpc.clone(), chain_id, addr.clone(), signer.clone());
    let deploy_res = dep.deploy_file("counter.manifest.json", "counter.contract.py").await?;
    println!("deployed at {}", deploy_res.address);

    // Bind ABI client
    let counter = ContractClient::from_manifest(rpc.clone(), &deploy_res.address, "counter.manifest.json")?;

    // Write call (inc)
    let _txh = counter
        .method("inc")?
        .args(serde_json::json!({ "delta": 1 }))
        .send(&addr, &signer)
        .await?;

    // Read call (get)
    let value: i64 = counter
        .method("get")?
        .call_json(serde_json::json!({}))
        .await?;
    println!("counter = {}", value);
    Ok(())
}


⸻

Data Availability (DA), AICF, Randomness

use animica_sdk::da::client::DaClient;
use animica_sdk::aicf::client::AicfClient;
use animica_sdk::randomness::client::RandClient;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let da = DaClient::new("http://127.0.0.1:8787")?;
    let blob = b"hello world";
    let put = da.post_blob(24, blob).await?;
    let proof = da.get_proof(&put.commitment).await?;
    println!("DA root: {}", proof.da_root);

    let aicf = AicfClient::new("http://127.0.0.1:8788")?;
    let job = aicf.enqueue_ai("gpt-small", serde_json::json!({"prompt":"ping"})).await?;
    let res = aicf.wait_result(&job.id).await?;
    println!("AICF result digest: {}", res.output_digest);

    let rand = RandClient::new("http://127.0.0.1:8789")?;
    let beacon = rand.get_beacon(None).await?;
    println!("rand round {} out {}", beacon.round, beacon.output_hex);
    Ok(())
}


⸻

Light Verification

use animica_sdk::light_client::verify::{LightVerifier, DaLightProof};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let v = LightVerifier::default();
    // header + DA light proof obtained from RPC/services
    let header = /* fetch */ unimplemented!();
    let da_proof: DaLightProof = /* fetch */ unimplemented!();
    v.verify_header_and_da(&header, &da_proof)?;
    println!("light verification passed");
    Ok(())
}


⸻

Browser / WASM

When compiling for wasm32-unknown-unknown, enable the wasm feature:

rustup target add wasm32-unknown-unknown
cargo build --target wasm32-unknown-unknown --features wasm

The same rpc::http::Client API is available. Under wasm, it uses gloo-net fetch and a lightweight WS client. Avoid blocking APIs and use async everywhere.

⸻

Examples & Tests
	•	Examples live under sdk/rust/examples/:
	•	quickstart.rs — deploy + call demo
	•	subscribe.rs — WS newHeads
	•	Tests are under sdk/rust/tests/:
	•	rpc_roundtrip.rs, wallet_sign.rs, contract_codegen.rs, events_decode.rs

Run:

cargo test --features native
# or
cargo test --target wasm32-unknown-unknown --features wasm


⸻

Error Handling

All public APIs return typed errors from animica_sdk::error. For ergonomic usage, consider anyhow in your app layer, while preserving the original error chains for observability.

⸻

Security Notes
	•	Keys are never sent to remote services. Keystores are AES-GCM with authenticated context.
	•	PQ signers (pq feature) use liboqs via the oqs crate; load failures surface as explicit errors.
	•	Always validate chain IDs and domains when building sign bytes and addresses.

⸻

License

Dual-licensed under Apache-2.0 or MIT at your option.

© Animica Labs. Contributions welcome!
