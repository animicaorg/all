// Deploy + Call quickstart for the Animica Rust SDK.
// --------------------------------------------------
// This example shows how to:
//
// 1) Connect to a node via JSON-RPC
// 2) Create a (dev/test) PQ signer
// 3) Deploy the sample Counter contract
// 4) Send a couple of `inc()` calls
//
// Requirements
// - A local devnet node running and funded test account OR a faucet
// - The SDK compiled with the `pq` feature enabled (for Dilithium3/Sphincs+)
// - Env overrides are supported; sensible defaults are provided
//
// Run:
//   cargo run --example quickstart
//
// Optional env:
//   ANIMICA_RPC_URL   (default: http://127.0.0.1:8545)
//   ANIMICA_CHAIN_ID  (default: 1337)
//   ANIMICA_MNEMONIC  (default: deterministic test phrase; DO NOT USE IN PROD)
//
// Notes:
// - This example uses the high-level contract deploy & client helpers.
// - It expects the included Counter manifest/ABI from sdk/common/examples/*.

#[cfg(not(feature = "pq"))]
fn main() {
    eprintln!("This example requires the `pq` feature (Dilithium3/SPHINCS+). Rebuild with: `cargo run --example quickstart --features pq`");
}

#[cfg(feature = "pq")]
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    use std::env;
    use std::time::Duration;

    use animica_sdk::contracts::client::ContractClient;
    use animica_sdk::contracts::deployer::Deployer;
    use animica_sdk::rpc::http::HttpClient;
    use animica_sdk::tx::send::{await_receipt, SubmitResult};
    use animica_sdk::wallet::mnemonic::Mnemonic;
    use animica_sdk::wallet::signer::{Dilithium3Signer, Signer};

    // ------------------------------------------------------------------------
    // 0) Config & inputs
    // ------------------------------------------------------------------------
    let rpc_url = env::var("ANIMICA_RPC_URL").unwrap_or_else(|_| "http://127.0.0.1:8545".to_string());
    let chain_id: u64 = env::var("ANIMICA_CHAIN_ID")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(1337);

    // Deterministic test mnemonic; replace / set ANIMICA_MNEMONIC in real use.
    let mnemonic = env::var("ANIMICA_MNEMONIC").unwrap_or_else(|_| {
        // 24 words for better entropy in dev; DO NOT use in production.
        "shoot island position soft burden budget tooth cruel issue economy destroy above \
         holiday palm squirrel cute swamp rubber era cost blouse trouble below frost"
            .to_string()
    });

    // Include the sample Counter contract manifest & ABI.
    // Paths are relative to this file (sdk/rust/examples).
    let counter_manifest_json = include_str!("../../common/examples/counter_manifest.json");
    let counter_abi_json = include_str!("../../common/examples/counter_abi.json");

    // ------------------------------------------------------------------------
    // 1) RPC client & signer
    // ------------------------------------------------------------------------
    let client = HttpClient::new(&rpc_url, Some(Duration::from_secs(30)));

    // Create a Dilithium3 signer from mnemonic. Index 0 for the first address.
    let m = Mnemonic::from_phrase(&mnemonic)?;
    let mut signer = Dilithium3Signer::from_mnemonic(&m, 0)?;
    let from_addr = signer.address();

    println!("RPC URL      : {rpc_url}");
    println!("Chain ID     : {chain_id}");
    println!("From address : {from_addr}");

    // ------------------------------------------------------------------------
    // 2) Deploy Counter
    // ------------------------------------------------------------------------
    let deployer = Deployer::new(client.clone(), chain_id);
    println!("Deploying Counter…");

    let deploy_submit: SubmitResult = deployer
        .deploy_contract(&mut signer, counter_manifest_json)
        .await?;

    println!("Deploy tx hash: {}", deploy_submit.tx_hash);

    // Wait for a receipt (with a reasonable timeout)
    let deploy_receipt = await_receipt(
        client.clone(),
        &deploy_submit.tx_hash,
        Duration::from_secs(30),
        Duration::from_millis(500),
    )
    .await?
    .ok_or("Timed out waiting for deploy receipt")?;

    if deploy_receipt.status != 1 {
        return Err(format!("Deploy failed: {:?}", deploy_receipt).into());
    }

    let contract_addr = deploy_receipt
        .contract_address
        .clone()
        .ok_or("Deploy receipt missing contract_address")?;

    println!("Counter deployed at: {contract_addr}");

    // ------------------------------------------------------------------------
    // 3) Build a ContractClient & call inc()
    // ------------------------------------------------------------------------
    let counter = ContractClient::new(client.clone(), chain_id, &contract_addr, counter_abi_json)?;

    // Send `inc()` twice as state-changing transactions.
    for i in 1..=2 {
        println!("Calling inc() — attempt #{i} …");
        let submit = counter
            .send(&mut signer, "inc", serde_json::json!([]), None)
            .await?;
        println!("inc() tx hash: {}", submit.tx_hash);

        let r = await_receipt(
            client.clone(),
            &submit.tx_hash,
            Duration::from_secs(20),
            Duration::from_millis(400),
        )
        .await?
        .ok_or("Timed out waiting for inc() receipt")?;

        if r.status != 1 {
            return Err(format!("inc() failed on attempt #{i}: {:?}", r).into());
        }
        println!("inc() #{i} mined in block {}", r.block_number.unwrap_or_default());
    }

    // Optional: simulate/read `get()` if your node exposes a read path.
    // Many deployments provide a simulation endpoint; SDK client maps it when available.
    match counter.call("get", serde_json::json!([])).await {
        Ok(val) => println!("Counter.get() → {}", val),
        Err(e) => eprintln!("Read call get() not available on this node: {e}"),
    }

    println!("Quickstart complete ✔");
    Ok(())
}
