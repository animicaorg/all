//! {{crate_name}} — a tiny convenience wrapper around `animica-sdk`
//!
//! This library is meant for starter projects. It provides:
//! - A `Config` loader (env → strongly typed)
//! - A minimal `NodeClient` with handy helpers for common RPCs
//! - A polling `await_receipt` utility for quick demos
//!
//! You can grow this crate in any direction: add higher-level flows,
//! contract-specific clients (codegen), indexing helpers, etc.

use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use serde::de::DeserializeOwned;
use serde_json::json;
use tracing::{debug, info, instrument};

use animica_sdk::{
    // The Rust SDK exposes HTTP & WS clients and typed core objects.
    rpc::http::Client as HttpClient,
    types, // re-exported structs (Tx/Receipt/Block/Head, etc.)
};

/// Public re-exports for downstream crates.
///
/// This keeps consumer `use` lines short:
/// ```rust
/// use {{crate_name}}::prelude::*;
/// ```
pub mod prelude {
    pub use super::{Config, NodeClient};
    pub use animica_sdk::types;
}

/// Basic runtime configuration for your app.
///
/// Values are typically sourced from environment variables (see `from_env`).
#[derive(Clone, Debug)]
pub struct Config {
    /// JSON-RPC base URL, e.g. `http://127.0.0.1:8545` or `https://rpc.devnet.animica.org`.
    pub rpc_url: String,
    /// Chain ID you expect to talk to (guards against cross-chain mistakes).
    pub chain_id: u64,
    /// Default request timeout applied by the underlying client where supported.
    pub default_timeout: Duration,
}

impl Config {
    /// Load config from environment with sensible defaults.
    ///
    /// Recognized variables (in priority order):
    /// - `ANIMICA_RPC_URL` or `RPC_URL`
    /// - `ANIMICA_CHAIN_ID` or `CHAIN_ID`
    /// - `ANIMICA_TIMEOUT_SECS` (optional; default 20)
    pub fn from_env() -> Result<Self> {
        // Load .env if present (no error if missing)
        let _ = dotenvy::dotenv();

        fn getenv(keys: &[&str]) -> Option<String> {
            for k in keys {
                if let Ok(v) = std::env::var(k) {
                    if !v.trim().is_empty() {
                        return Some(v);
                    }
                }
            }
            None
        }

        let rpc_url = getenv(&["ANIMICA_RPC_URL", "RPC_URL"])
            .context("Missing RPC URL (set ANIMICA_RPC_URL or RPC_URL)")?;

        let chain_id_str = getenv(&["ANIMICA_CHAIN_ID", "CHAIN_ID"])
            .context("Missing CHAIN_ID (set ANIMICA_CHAIN_ID or CHAIN_ID)")?;
        let chain_id = chain_id_str
            .parse::<u64>()
            .with_context(|| format!("Invalid CHAIN_ID: {chain_id_str}"))?;

        let timeout_secs = getenv(&["ANIMICA_TIMEOUT_SECS"])
            .and_then(|s| s.parse::<u64>().ok())
            .unwrap_or(20);

        Ok(Self {
            rpc_url,
            chain_id,
            default_timeout: Duration::from_secs(timeout_secs),
        })
    }
}

/// Thin, async JSON-RPC client built on top of `animica-sdk`.
///
/// This wrapper provides a few typed helpers and a generic `call` method
/// so you can reach any method that isn't wrapped yet.
#[derive(Clone)]
pub struct NodeClient {
    cfg: Config,
    http: HttpClient,
}

impl std::fmt::Debug for NodeClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("NodeClient")
            .field("rpc_url", &self.cfg.rpc_url)
            .field("chain_id", &self.cfg.chain_id)
            .finish()
    }
}

impl NodeClient {
    /// Build a new client from a `Config`.
    pub fn new(cfg: Config) -> Result<Self> {
        let http = HttpClient::new(&cfg.rpc_url)
            .with_timeout(cfg.default_timeout);
        Ok(Self { cfg, http })
    }

    /// Access the loaded configuration.
    pub fn config(&self) -> &Config {
        &self.cfg
    }

    /// Generic JSON-RPC call helper.
    ///
    /// Use this when you need a method not (yet) wrapped as a dedicated function.
    /// `params` is any `serde_json::Value` (e.g. `json!([...])` or `json!({ ... })`).
    #[instrument(level = "debug", skip(self, params))]
    pub async fn call<T: DeserializeOwned>(
        &self,
        method: &str,
        params: serde_json::Value,
    ) -> Result<T> {
        debug!(%method, "rpc.call");
        let out: T = self
            .http
            .call(method, params)
            .await
            .with_context(|| format!("RPC call failed: {method}"))?;
        Ok(out)
    }

    /// Fetch chain parameters (mirrors `spec/params.yaml`).
    pub async fn get_params(&self) -> Result<serde_json::Value> {
        self.call("chain.getParams", json!([])).await
    }

    /// Get the current head summary: height, hash, parent, timestamp, etc.
    pub async fn get_head(&self) -> Result<types::Head> {
        self.call("chain.getHead", json!([])).await
    }

    /// Resolve the node's chain ID via RPC and assert it matches our config.
    pub async fn assert_chain_id(&self) -> Result<u64> {
        let id: u64 = self.call("chain.getChainId", json!([])).await?;
        if id != self.cfg.chain_id {
            return Err(anyhow!(
                "ChainId mismatch: node reports {id}, config expects {}",
                self.cfg.chain_id
            ));
        }
        Ok(id)
    }

    /// Submit a raw (CBOR-encoded) transaction blob.
    ///
    /// Returns the transaction hash (0x-hex string).
    pub async fn send_raw_transaction(&self, raw_cbor: &[u8]) -> Result<String> {
        let hex = format!("0x{}", hex::encode(raw_cbor));
        // JSON-RPC expects hex-encoded bytes; no base64.
        let tx_hash: String = self
            .call("tx.sendRawTransaction", json!([hex]))
            .await
            .context("sendRawTransaction failed")?;
        info!(%tx_hash, "submitted transaction");
        Ok(tx_hash)
    }

    /// Fetch a transaction receipt by hash. Returns `None` if not yet available.
    pub async fn get_receipt(&self, tx_hash: &str) -> Result<Option<types::Receipt>> {
        // Most nodes return `null` until the receipt is available.
        // We model that as Option<Receipt>.
        let receipt_opt: Option<types::Receipt> = self
            .call("tx.getTransactionReceipt", json!([tx_hash]))
            .await
            .context("tx.getTransactionReceipt failed")?;
        Ok(receipt_opt)
    }

    /// Poll for a transaction receipt until it appears or times out.
    ///
    /// This is handy for quickstarts and CLI demos. For production,
    /// prefer WS subscriptions to `pendingTxs` / `newHeads` for scalability.
    #[instrument(level = "info", skip(self))]
    pub async fn await_receipt(
        &self,
        tx_hash: &str,
        timeout: Duration,
        poll_every: Duration,
    ) -> Result<types::Receipt> {
        let start = std::time::Instant::now();
        loop {
            if let Some(r) = self.get_receipt(tx_hash).await? {
                return Ok(r);
            }
            if start.elapsed() >= timeout {
                return Err(anyhow!("timed out waiting for receipt: {tx_hash}"));
            }
            tokio::time::sleep(poll_every).await;
        }
    }
}

// --- Optional helpers behind small, focused feature flags --------------------

#[cfg(feature = "ws")]
pub mod ws {
    //! WebSocket convenience wrappers (optional).
    //!
    //! Enable the `ws` feature in your `Cargo.toml` (already enabled by default
    //! in the starter template via `animica-sdk` features).

    use super::*;
    use animica_sdk::rpc::ws;

    /// Subscribe to `newHeads` and yield them as a stream.
    ///
    /// Example:
    /// ```ignore
    /// let mut stream = client.subscribe_new_heads().await?;
    /// while let Some(head) = stream.next().await {
    ///     println!("new head: {}", head.height);
    /// }
    /// ```
    pub async fn subscribe_new_heads(cfg: &Config) -> Result<ws::HeadStream> {
        let client = ws::Client::connect(&cfg.rpc_url).await?;
        let stream = client.subscribe_new_heads().await?;
        Ok(stream)
    }
}

// --- Small, focused hex utility (kept private; used by send_raw_transaction) -

mod hex {
    // Local minimal dependency to keep the template self-contained if you choose
    // to remove the external "hex" crate later.
    pub fn encode(bytes: &[u8]) -> String {
        const LUT: &[u8; 16] = b"0123456789abcdef";
        let mut out = String::with_capacity(bytes.len() * 2);
        for &b in bytes {
            out.push(LUT[(b >> 4) as usize] as char);
            out.push(LUT[(b & 0x0f) as usize] as char);
        }
        out
    }
}

// --- Tests (unit-level smoke) ------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_from_env_defaults() {
        // Provide a minimal env to exercise parsing without making network calls.
        std::env::set_var("ANIMICA_RPC_URL", "http://localhost:8545");
        std::env::set_var("ANIMICA_CHAIN_ID", "1337");
        std::env::remove_var("ANIMICA_TIMEOUT_SECS");

        let cfg = Config::from_env().expect("config");
        assert_eq!(cfg.rpc_url, "http://localhost:8545");
        assert_eq!(cfg.chain_id, 1337);
        assert_eq!(cfg.default_timeout, Duration::from_secs(20));
    }
}
