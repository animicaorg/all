# Rust SDK (`animica-sdk`) — Usage Patterns

Production-minded recipes for building Rust apps against an Animica node:
HTTP/WS RPC, PQ signing, transactions, contracts, events, Data Availability,
AICF (AI/Quantum), randomness beacon, and light-client verification.

- Crate: `sdk/rust` → **animica-sdk**
- Schemas: `spec/openrpc.json`, `spec/abi.schema.json`
- Shared vectors: `sdk/common/test_vectors/*`

---

## 1) Install

In your **Cargo.toml**:

```toml
[package]
name = "my-animica-app"
version = "0.1.0"
edition = "2021"

[dependencies]
animica-sdk = { path = "./sdk/rust", default-features = false, features = ["pq"] }
tokio = { version = "1", features = ["rt-multi-thread", "macros"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
anyhow = "1"

Use the pq feature if you want built-in post-quantum signers (via optional oqs).
If you publish to crates.io, replace path = "./sdk/rust" with a version spec.

⸻

2) Quick HTTP JSON-RPC

use animica_sdk::rpc::http::HttpClient;
use anyhow::Result;

#[tokio::main]
async fn main() -> Result<()> {
    let rpc = HttpClient::new("https://rpc.animica.dev")?;
    let head: serde_json::Value = rpc.call("chain.getHead", serde_json::json!([])).await?;
    println!("height={} hash={}", head["height"], head["hash"]);
    Ok(())
}

	•	HttpClient keeps connections alive and retries safe idempotent calls.
	•	You can set request timeout per-call: rpc.call_with_timeout(..., std::time::Duration::from_secs(10)).await.

⸻

3) Wallets & PQ Signers

a) Mnemonic → Keystore (AES-GCM)

use animica_sdk::wallet::{mnemonic, keystore::Keystore, signer::Signer};
use animica_sdk::address::bech32_address;
use anyhow::Result;

#[tokio::main]
async fn main() -> Result<()> {
    let words = "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about";
    let seed = mnemonic::mnemonic_to_seed(words)?;
    let mut ks = Keystore::create("./keystore.json", "strong-passphrase")?;
    ks.import_seed(&seed)?;

    // PQ signer (Dilithium3 by default with `pq` feature)
    let signer = Signer::from_keystore(&ks, Some("dilithium3"))?;
    let addr = bech32_address(&signer.public_key(), "anim")?;
    println!("Address: {addr}");
    Ok(())
}

b) Ephemeral in-memory signer (tests)

use animica_sdk::wallet::signer::InMemorySigner;
let signer = InMemorySigner::from_seed(&seed, Some("dilithium3"))?;


⸻

4) Build & Send a Transfer

use animica_sdk::{
    rpc::http::HttpClient,
    tx::{build, send::send_tx},
    address::bech32_address,
    wallet::{mnemonic, keystore::Keystore, signer::Signer},
};
use anyhow::Result;

#[tokio::main]
async fn main() -> Result<()> {
    let rpc = HttpClient::new("https://rpc.animica.dev")?;

    // wallet
    let seed = mnemonic::mnemonic_to_seed("... 24 words ...")?;
    let mut ks = Keystore::create("./ks.json", "pw")?;
    ks.import_seed(&seed)?;
    let signer = Signer::from_keystore(&ks, Some("dilithium3"))?;
    let from = bech32_address(&signer.public_key(), "anim")?;

    // nonce
    let nonce: u64 = rpc.call("state.getNonce", serde_json::json!([from])).await?
        .as_u64().unwrap();

    // build
    let tx = build::build_transfer(build::Transfer {
        chain_id: 1,
        from_addr: from.clone(),
        to_addr: "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqv3c9xv".into(),
        amount: "1000000".into(),
        gas_price: "1200".into(),
        gas_limit: "120000".into(),
        nonce,
        memo: None,
    });

    // sign & send
    let signed = signer.sign_tx(&tx)?;
    let res = send_tx(&rpc, &signed).await?;
    println!("txHash={} status={}", res.tx_hash, res.receipt.as_ref().map(|r| r.status).unwrap_or_default());
    Ok(())
}

	•	Sign bytes are canonical & domain-separated (SignBytes matches core/encoding).
	•	send_tx returns { tx_hash, receipt: Option<Receipt> }.

⸻

5) Contracts

a) Generic client (ABI JSON)

use animica_sdk::contracts::client::ContractClient;
use serde_json::json;

let abi: serde_json::Value = serde_json::from_reader(std::fs::File::open("./counter_abi.json")?)?;
let client = ContractClient::new(rpc.clone(), abi, "anim1xyz...".into());

// Read (free)
let value: serde_json::Value = client.call("get", json!([])).await?;

// Write
let tx = client.build_tx("inc", json!([1]), /*from*/ from.clone(), 1, "150000", "1200")?;
let signed = signer.sign_tx(&tx)?;
let receipt = animica_sdk::tx::send::send_tx(&rpc, &signed).await?.receipt.unwrap();
println!("gasUsed={}", receipt.gas_used);

b) Codegen (typed stubs)

# Python codegen tool emits Rust stubs too (via templates in sdk/codegen/rust)
python -m sdk.codegen.cli --lang rs --abi ./counter_abi.json --out ./generated-rs

use generated_rs::counter::Counter;
let counter = Counter::new(rpc.clone(), "anim1...".into());
counter.inc(&signer, 1).await?;
let n = counter.get().await?;

c) Events

use animica_sdk::contracts::events::decode_events;
let rcpt = rpc.call("tx.getTransactionReceipt", serde_json::json!([tx_hash])).await?;
for evt in decode_events(&rcpt["logs"], &abi)? {
    println!("{} {:?}", evt.name, evt.args);
}


⸻

6) WebSocket subscriptions (async)

use animica_sdk::rpc::ws::WsClient;
use anyhow::Result;

#[tokio::main]
async fn main() -> Result<()> {
    let mut ws = WsClient::connect("wss://rpc.animica.dev/ws").await?;
    let mut sub = ws.subscribe("newHeads", serde_json::json!([])).await?;

    while let Some(msg) = sub.next().await {
        let head = msg?;
        println!("height={} hash={}", head["height"], head["hash"]);
        if head["height"].as_u64() == Some(3) { break; }
    }
    Ok(())
}

WsClient yields typed JSON values (serde_json::Value). Bring StreamExt from futures if needed.

⸻

7) Data Availability (DA)

use animica_sdk::da::client::DAClient;

let da = DAClient::new("https://rpc.animica.dev");
let put = da.put_blob(24, b"hello").await?;
println!("commitment={}", put.commitment);

let blob = da.get_blob(&put.commitment).await?;
let proof = da.get_proof(&put.commitment).await?;
let ok = da.verify_proof(&proof)?;
assert!(ok);


⸻

8) AICF (AI/Quantum)

use animica_sdk::aicf::client::AICFClient;

let aicf = AICFClient::new("https://rpc.animica.dev");
let job = aicf.enqueue_ai("tiny-demo", "hello world", "500000", &from, &signer).await?;
let maybe = aicf.get_result(&job.task_id).await?;
if let Some(r) = maybe {
    if r.status == "completed" {
        println!("output: {}", r.output);
    }
}


⸻

9) Randomness Beacon

use animica_sdk::randomness::client::RandomnessClient;
use animica_sdk::utils::hash::sha3_256_hex;

let rand = RandomnessClient::new(rpc.clone());
let round = rand.get_round().await?;

let salt = vec![0u8; 32];
let payload = sha3_256_hex(b"my-entropy");
rand.commit(&salt, &payload, &signer).await?;
// ... later:
rand.reveal(&salt, &payload, &signer).await?;

let beacon = rand.get_beacon().await?;
println!("beacon={}", beacon.output);


⸻

10) Light Client Verify (Header + DA samples)

use animica_sdk::light_client::verify::verify_light;

let header = rpc.call("chain.getBlockByNumber", serde_json::json!([12345, false])).await?;
let samples: serde_json::Value = serde_json::from_reader(std::fs::File::open("./fixtures/light_samples.json")?)?;
let ok = verify_light(&serde_json::json!({ "header": header, "samples": samples }))?;
println!("light verify: {ok}");


⸻

11) Errors

All SDK functions return Result<T, animica_sdk::error::Error>.

use animica_sdk::error::Error;

match rpc.call("state.getBalance", serde_json::json!([from])).await {
    Ok(v) => println!("balance={}", v),
    Err(Error::Rpc(e)) => eprintln!("rpc error: {e}"),
    Err(Error::Tx(e))  => eprintln!("tx error: {e:?}"),
    Err(e)             => eprintln!("other: {e}"),
}

Error kinds include:
	•	Error::Rpc — network/JSON-RPC shape issues
	•	Error::Tx — mempool/execution rejections (FeeTooLow, NonceGap, etc.)
	•	Error::Abi — ABI encode/decode mismatches
	•	Error::Verify — proof/validation failures

⸻

12) Testing (Tokio + mock HTTP)

#[tokio::test]
async fn head_parses() {
    use animica_sdk::rpc::http::HttpClient;

    let rpc = HttpClient::with_base_and_client(
        "http://localhost:12345",
        reqwest::Client::new()
    ).unwrap();

    // In tests you can inject a mock server or stub `HttpClient::_call_once`.
    // See sdk/rust/tests for examples.
    let _ = rpc; // compile check
}


⸻

13) Performance Tips
	•	Reuse HttpClient (keep-alive).
	•	Initialize signers once (PQ backends warm-up).
	•	Use WS subscriptions for heads/events; avoid polling.
	•	Batch lookups (balance/nonce) and cache ABIs.

⸻

14) Security Notes
	•	Always set/verify chain_id when building/signing.
	•	Protect keystore passphrases; avoid logging seeds/keys.
	•	Validate ABI inputs; bound gas and amounts.
	•	Treat all node responses as untrusted; verify proofs when applicable.

⸻

15) Minimal E2E (compile-ready)

use animica_sdk::{
    rpc::http::HttpClient,
    wallet::{mnemonic, keystore::Keystore, signer::Signer},
    address::bech32_address,
    tx::{build, send::send_tx},
};
use anyhow::Result;

#[tokio::main]
async fn main() -> Result<()> {
    let rpc = HttpClient::new("https://rpc.animica.dev")?;

    let seed = mnemonic::mnemonic_to_seed("... 24 words ...")?;
    let mut ks = Keystore::create("./ks.json", "pw")?;
    ks.import_seed(&seed)?;
    let signer = Signer::from_keystore(&ks, Some("dilithium3"))?;
    let from = bech32_address(&signer.public_key(), "anim")?;

    let nonce: u64 = rpc.call("state.getNonce", serde_json::json!([from])).await?
        .as_u64().unwrap();

    let tx = build::build_transfer(build::Transfer{
        chain_id: 1,
        from_addr: from.clone(),
        to_addr: "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqv3c9xv".into(),
        amount: "1000000".into(),
        gas_price: "1200".into(),
        gas_limit: "100000".into(),
        nonce,
        memo: None
    });

    let signed = signer.sign_tx(&tx)?;
    let res = send_tx(&rpc, &signed).await?;
    println!("{} {:?}", res.tx_hash, res.receipt.map(|r| r.status));
    Ok(())
}


⸻

16) Reference Map
	•	rpc/http.rs, rpc/ws.rs — HTTP & WS clients
	•	wallet/mnemonic.rs, wallet/keystore.rs, wallet/signer.rs
	•	tx/build.rs, tx/encode.rs, tx/send.rs
	•	contracts/client.rs, contracts/events.rs, codegen templates in sdk/codegen/rust
	•	da/client.rs, aicf/client.rs, randomness/client.rs
	•	light_client/verify.rs, proofs/*, utils/*

