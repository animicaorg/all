// RPC round-trip smoke tests for the Animica Rust SDK.
//
// These tests are *ignored by default* so they don't fail CI when no
// local node is running. To run them, start a node and execute:
//
//   ANIMICA_RPC_URL=http://127.0.0.1:8545 \
//   cargo test -p animica-sdk --tests rpc_roundtrip -- --ignored --nocapture
//
// Optional env:
//   ANIMICA_RPC_URL   (default: http://127.0.0.1:8545)
//   ANIMICA_CHAIN_ID  (expected chain id; test will only warn on mismatch)

use std::env;
use std::time::Duration;

use animica_sdk::rpc::http::HttpClient;
use serde_json::{json, Value as JsonValue};

fn rpc_url() -> String {
    env::var("ANIMICA_RPC_URL").unwrap_or_else(|_| "http://127.0.0.1:8545".to_string())
}

fn expected_chain_id() -> Option<u64> {
    env::var("ANIMICA_CHAIN_ID").ok().and_then(|v| v.parse().ok())
}

#[tokio::test]
#[ignore]
async fn http_chain_id_and_head_roundtrip() -> Result<(), Box<dyn std::error::Error>> {
    let url = rpc_url();
    let client = HttpClient::new(&url, Some(Duration::from_secs(10)));

    // 1) chain.getChainId (if exposed) — fall back gracefully if not supported.
    let chain_id_res: Result<u64, _> = client.call("chain.getChainId", JsonValue::Null).await;
    if let Ok(actual) = chain_id_res {
        if let Some(expected) = expected_chain_id() {
            if actual != expected {
                eprintln!(
                    "[warn] chainId mismatch: expected {}, got {} (URL: {})",
                    expected, actual, url
                );
            }
        }
        println!("chain.getChainId → {}", actual);
    } else {
        eprintln!("[info] chain.getChainId not available on this node; skipping check");
    }

    // 2) chain.getHead — required by spec/openrpc.json
    let head: JsonValue = client.call("chain.getHead", JsonValue::Null).await?;
    println!("chain.getHead → {}", head);

    // Be resilient to different field names used by various builds.
    let height = head
        .get("number")
        .or_else(|| head.get("height"))
        .and_then(|v| v.as_u64())
        .ok_or("head missing number/height")?;

    let hash = head
        .get("hash")
        .or_else(|| head.get("headerHash"))
        .and_then(|v| v.as_str())
        .ok_or("head missing hash/headerHash")?;

    assert!(height >= 0, "height should be non-negative");
    assert!(hash.starts_with("0x") && hash.len() >= 10, "hash should be hex-like");

    Ok(())
}

#[tokio::test]
#[ignore]
async fn http_params_surface_exists() -> Result<(), Box<dyn std::error::Error>> {
    let url = rpc_url();
    let client = HttpClient::new(&url, Some(Duration::from_secs(10)));

    // 3) chain.getParams — basic shape check (Θ, Γ or gas tables present)
    let params: JsonValue = client.call("chain.getParams", json!({})).await?;
    println!("chain.getParams → {}", params);

    // Expect at least one of these commonly-present keys.
    let has_any = ["theta", "Θ", "gas", "gasTable", "blockLimits", "consensus"]
        .iter()
        .any(|k| params.get(*k).is_some());

    assert!(
        has_any,
        "unexpected params surface; none of the expected keys were found"
    );

    Ok(())
}
