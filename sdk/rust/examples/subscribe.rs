// Subscribe to newHeads via WebSocket.
// ------------------------------------
// Example usage:
//   ANIMICA_WS_URL=ws://127.0.0.1:8545/ws cargo run --example subscribe
//
// Env:
//   ANIMICA_WS_URL  (default: ws://127.0.0.1:8545/ws)

use std::env;
use std::time::Duration;

use futures::{StreamExt, TryStreamExt};
use serde_json::Value as JsonValue;
use tokio::time::sleep;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let ws_url = env::var("ANIMICA_WS_URL").unwrap_or_else(|_| "ws://127.0.0.1:8545/ws".to_string());

    println!("Connecting WS: {ws_url}");

    // Prefer the SDK WS client if available.
    // Fallback is simple retry loop to handle transient disconnects.
    loop {
        match run_subscribe_once(&ws_url).await {
            Ok(_) => {
                // Normal exit (e.g., Ctrl-C).
                break;
            }
            Err(e) => {
                eprintln!("WS disconnected: {e}. Reconnecting in 2s…");
                sleep(Duration::from_secs(2)).await;
            }
        }
    }

    Ok(())
}

async fn run_subscribe_once(ws_url: &str) -> Result<(), Box<dyn std::error::Error>> {
    use animica_sdk::rpc::ws::{SubscriptionStream, WsClient};

    // Connect
    let mut client = WsClient::connect(ws_url, None).await?;

    // Subscribe to "newHeads"
    // The SDK maps to JSON-RPC WS subscribe under the hood and yields
    // a stream of `serde_json::Value` results (the Head view).
    let mut sub: SubscriptionStream<JsonValue> = client.subscribe("newHeads", JsonValue::Null).await?;

    println!("Subscribed to newHeads. Press Ctrl-C to exit.\n");

    // Handle Ctrl-C to exit cleanly.
    let mut sigint = tokio::signal::ctrl_c();

    loop {
        tokio::select! {
            _ = &mut sigint => {
                println!("Ctrl-C received, closing subscription…");
                sub.close().await.ok();
                client.close().await.ok();
                return Ok(());
            }
            maybe_item = sub.try_next() => {
                match maybe_item {
                    Ok(Some(note)) => {
                        print_head_line(&note);
                    }
                    Ok(None) => {
                        // Stream ended gracefully (server closed).
                        return Err("subscription ended".into());
                    }
                    Err(e) => {
                        // Propagate to outer loop for reconnect.
                        return Err(Box::<dyn std::error::Error + Send + Sync>::from(e));
                    }
                }
            }
        }
    }
}

/// Pretty-print a minimal line for a head JSON object.
/// Tries common field names used by the RPC `Head` view.
fn print_head_line(v: &JsonValue) {
    let height = v.get("number")
        .or_else(|| v.get("height"))
        .and_then(|x| x.as_u64())
        .unwrap_or_default();

    let hash = v.get("hash")
        .or_else(|| v.get("headerHash"))
        .and_then(|x| x.as_str())
        .unwrap_or("<unknown>");

    let theta = v.get("theta").and_then(|x| x.as_u64());
    let time  = v.get("timestamp").and_then(|x| x.as_u64());

    println!(
        "newHead #{}  hash={}  {}{}",
        height,
        hash,
        theta.map(|t| format!("Θ={} ", t)).unwrap_or_default(),
        time.map(|ts| format!("ts={}", ts)).unwrap_or_default(),
    );
}
