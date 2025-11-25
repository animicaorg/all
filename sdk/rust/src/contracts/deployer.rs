//! Contract **package deployer** (manifest + code).
//!
//! This helper bundles a manifest (JSON) and code bytes into a canonical CBOR
//! payload, estimates gas, builds a **Deploy** transaction, signs, submits, and
//! waits for the receipt.
//!
//! ### Deploy payload (CBOR, canonical, integer-keyed map)
//! We encode the deploy package as a 2-field map to keep cross-SDK stability:
//! ```text
//! 0: manifest  (JSON object; validated by the node against spec/manifest.schema.json)
//! 1: code      (bytes; VM-specific, e.g. Python-VM IR bytes)
//! ```
//!
//! This mirrors the Python & TypeScript SDKs.

use crate::error::{Error, Result};
use crate::rpc::http::RpcClient;
use crate::tx::build::{build_deploy, estimate_gas_deploy, GasEstimate};
use crate::tx::send::{build_signed_envelope, send_and_wait};
use crate::types::{AccessListItem, Receipt, Tx};
use crate::utils::hash::sha3_256;
use crate::wallet::signer::TxSigner;
use serde::Serialize;
use serde_cbor::ser::Serializer;
use serde_json::Value as JsonValue;
use std::time::Duration;

/// Options for a deploy (write) transaction.
#[derive(Debug, Clone)]
pub struct DeployOptions<'a, S: TxSigner> {
    pub chain_id: u64,
    pub from: &'a str,         // bech32m "anim1..."
    pub nonce: u64,
    pub gas_price: u64,
    /// If `None`, we'll estimate and add headroom.
    pub gas_limit: Option<u64>,
    /// Value to send with deployment (usually 0).
    pub value: u128,
    /// Optional access-list (builder may construct from manifest hints later).
    pub access_list: &'a [AccessListItem],
    /// PQ signer (Dilithium3 / SPHINCS+).
    pub signer: &'a S,
    /// Poll frequency for receipt.
    pub poll_every: Duration,
    /// Overall timeout for receipt.
    pub timeout: Duration,
}

impl<'a, S: TxSigner> DeployOptions<'a, S> {
    pub fn with_defaults(
        chain_id: u64,
        from: &'a str,
        nonce: u64,
        gas_price: u64,
        signer: &'a S,
    ) -> Self {
        Self {
            chain_id,
            from,
            nonce,
            gas_price,
            gas_limit: None,
            value: 0,
            access_list: &[],
            signer,
            poll_every: Duration::from_secs(1),
            timeout: Duration::from_secs(60),
        }
    }
}

/// Deployer bound to an RPC client.
#[derive(Clone)]
pub struct Deployer<'r> {
    rpc: &'r RpcClient,
}

impl<'r> Deployer<'r> {
    pub fn new(rpc: &'r RpcClient) -> Self {
        Self { rpc }
    }

    /// Encode `{0: manifest, 1: code}` as canonical CBOR bytes.
    pub fn encode_payload(&self, manifest: &JsonValue, code: &[u8]) -> Result<Vec<u8>> {
        // Canonical CBOR with self-describe tag for easy debugging.
        let mut buf = Vec::with_capacity(code.len() + 256);
        let mut ser = Serializer::new(&mut buf);
        ser.self_describe()?;
        ser.canonical();

        // 2-entry map
        ser.serialize_map(Some(2))?;

        // key 0 → manifest (serialize JSON as CBOR via serde)
        ser.serialize_u8(0)?;
        manifest.serialize(&mut ser).map_err(|e| Error::Serde(e.to_string()))?;

        // key 1 → code (bytes)
        ser.serialize_u8(1)?;
        ser.serialize_bytes(code)?;

        ser.end()?;
        Ok(buf)
    }

    /// Compute code hash (SHA3-256) as bytes.
    pub fn code_hash(&self, code: &[u8]) -> [u8; 32] {
        sha3_256(code)
    }

    /// Hex form of code hash (0x-prefixed).
    pub fn code_hash_hex(&self, code: &[u8]) -> String {
        let h = self.code_hash(code);
        let mut s = String::with_capacity(2 + h.len() * 2);
        s.push_str("0x");
        s.push_str(&hex::encode(h));
        s
    }

    /// Heuristic gas estimation for deploy payload + access list.
    pub fn estimate_gas(&self, manifest: &JsonValue, code: &[u8], access_list: &[AccessListItem]) -> Result<GasEstimate> {
        let payload = self.encode_payload(manifest, code)?;
        Ok(estimate_gas_deploy(&payload, access_list))
    }

    /// Build a deploy transaction (unsigned).
    pub fn build_tx(
        &self,
        chain_id: u64,
        from: &str,
        nonce: u64,
        gas_price: u64,
        gas_limit: u64,
        value: u128,
        manifest: &JsonValue,
        code: &[u8],
        access_list: Vec<AccessListItem>,
    ) -> Result<Tx> {
        let payload = self.encode_payload(manifest, code)?;
        Ok(build_deploy(
            chain_id,
            from,
            value,
            nonce,
            gas_price,
            gas_limit,
            payload,
            access_list,
        ))
    }

    /// Deploy a package: encode → (estimate gas) → build → sign → send → await receipt.
    ///
    /// Returns `(tx_hash, receipt)`. The receipt's contract address (if any) is
    /// available via `receipt.contract_address` (per `crate::types`).
    pub async fn deploy_and_wait<S: TxSigner>(
        &self,
        manifest: &JsonValue,
        code: &[u8],
        mut opts: DeployOptions<'_, S>,
    ) -> Result<(String, Receipt)> {
        // Determine gas limit
        let gas_limit = match opts.gas_limit {
            Some(gl) => gl,
            None => self.estimate_gas(manifest, code, opts.access_list)?.suggested_limit,
        };

        // Build tx
        let tx = self.build_tx(
            opts.chain_id,
            opts.from,
            opts.nonce,
            opts.gas_price,
            gas_limit,
            opts.value,
            manifest,
            code,
            opts.access_list.to_vec(),
        )?;

        // Sign quickly to surface encoding/sign issues early
        let _env = build_signed_envelope(&tx, opts.signer)?;

        // Submit & wait
        send_and_wait(self.rpc, &tx, opts.signer, opts.poll_every, opts.timeout).await
    }
}

// ---------------------------------- Tests ------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::AccessListItem;

    #[test]
    fn payload_is_canonical_and_has_two_fields() {
        let rpc = RpcClient::new("http://localhost:0");
        let d = Deployer::new(&rpc);
        let manifest = serde_json::json!({"name":"counter","version":"1.0.0"});
        let code = vec![1u8, 2, 3, 4, 5];
        let bytes = d.encode_payload(&manifest, &code).unwrap();

        // Decode & sanity check
        let val: serde_cbor::Value = serde_cbor::from_slice(&bytes).unwrap();
        match val {
            serde_cbor::Value::Tag(_, inner) => {
                // self-describe tag wraps a map
                match *inner {
                    serde_cbor::Value::Map(m) => {
                        assert_eq!(m.len(), 2);
                    }
                    _ => panic!("expected map inside self-describe tag"),
                }
            }
            _ => panic!("expected self-describe tag"),
        }
    }

    #[test]
    fn code_hash_hex_formats() {
        let rpc = RpcClient::new("http://localhost:0");
        let d = Deployer::new(&rpc);
        let hex = d.code_hash_hex(b"hello");
        assert!(hex.starts_with("0x"));
        assert_eq!(hex.len(), 2 + 64); // 32 bytes → 64 hex chars
    }

    #[test]
    fn estimate_and_build() {
        let rpc = RpcClient::new("http://localhost:0");
        let d = Deployer::new(&rpc);
        let manifest = serde_json::json!({"name":"counter","version":"1.0.0"});
        let code = vec![0u8; 64];
        let gas = d.estimate_gas(&manifest, &code, &[]).unwrap();
        assert!(gas.suggested_limit >= gas.intrinsic);

        let tx = d
            .build_tx(1, "anim1from...", 7, 1_000, gas.suggested_limit, 0, &manifest, &code, vec![])
            .unwrap();
        assert_eq!(tx.chain_id, 1);
        assert_eq!(tx.nonce, 7);
        assert_eq!(tx.gas_price, 1_000);
        assert!(tx.data.len() > 0);
        assert!(tx.to.is_none(), "deploy tx has no 'to' address");
    }
}
