//! Submit signed transactions and await receipts (HTTP poll; WS optional).
//!
//! This module provides:
//! - `build_signed_envelope` → produce canonical CBOR bytes ready for RPC
//! - `send_raw_envelope`     → call `tx.sendRawTransaction`
//! - `wait_for_receipt`      → poll `tx.getTransactionReceipt` until found/timeout
//! - `send_and_wait`         → convenience: sign → send → await receipt
//!
//! The raw envelope bytes are hex-encoded with `0x` when sent over JSON-RPC,
//! matching the Animica node's `tx.sendRawTransaction` method.

use crate::error::{Error, Result};
use crate::rpc::http::RpcClient;
use crate::tx::encode::{encode_sign_bytes, encode_signed_envelope, TX_SIGN_DOMAIN};
use crate::types::Receipt;
use crate::types::Tx;
use crate::wallet::signer::TxSigner;
use serde::de::DeserializeOwned;
use serde_json::json;
use std::time::Duration;
use tokio::time::{sleep, Instant};

/// Build a signed CBOR envelope from an unsigned `Tx` using a `TxSigner`.
///
/// The signer is responsible for producing the signature in the correct domain.
/// We pass the canonical sign-bytes and the `TX_SIGN_DOMAIN` constant.
pub fn build_signed_envelope<S: TxSigner>(tx: &Tx, signer: &S) -> Result<Vec<u8>> {
    let sign_bytes = encode_sign_bytes(tx)?;
    let sig = signer.sign(TX_SIGN_DOMAIN, &sign_bytes)?;
    let env = encode_signed_envelope(tx, signer.alg_id(), signer.public_key(), &sig)?;
    Ok(env)
}

/// Submit a pre-built signed envelope (CBOR bytes) via JSON-RPC `tx.sendRawTransaction`.
///
/// Returns the canonical transaction hash as a `0x`-prefixed hex string.
pub async fn send_raw_envelope(client: &RpcClient, envelope_cbor: &[u8]) -> Result<String> {
    let raw_hex = to_hex_prefixed(envelope_cbor);
    // Node returns the tx hash as 0x-hex string.
    call_rpc::<String>(client, "tx.sendRawTransaction", json!([raw_hex])).await
}

/// Poll `tx.getTransactionReceipt` until a receipt is found or `timeout` elapses.
///
/// * `poll_every` controls the interval between polls (e.g., 1s–2s is typical).
/// * On timeout, returns `Err(Error::Timeout(timeout))`.
pub async fn wait_for_receipt(
    client: &RpcClient,
    tx_hash: &str,
    poll_every: Duration,
    timeout: Duration,
) -> Result<Receipt> {
    let deadline = Instant::now() + timeout;
    loop {
        // Try fetch
        let maybe: Option<Receipt> =
            call_rpc(client, "tx.getTransactionReceipt", json!([tx_hash])).await?;
        if let Some(rcpt) = maybe {
            return Ok(rcpt);
        }
        // Check timeout
        let now = Instant::now();
        if now >= deadline {
            return Err(Error::Timeout(timeout));
        }
        // Sleep up to remaining time
        let sleep_dur = std::cmp::min(poll_every, deadline - now);
        sleep(sleep_dur).await;
    }
}

/// Convenience: sign → send → wait for receipt.
///
/// Returns `(tx_hash, receipt)`.
pub async fn send_and_wait<S: TxSigner>(
    client: &RpcClient,
    tx: &Tx,
    signer: &S,
    poll_every: Duration,
    timeout: Duration,
) -> Result<(String, Receipt)> {
    let env = build_signed_envelope(tx, signer)?;
    let tx_hash = send_raw_envelope(client, &env).await?;
    let rcpt = wait_for_receipt(client, &tx_hash, poll_every, timeout).await?;
    Ok((tx_hash, rcpt))
}

/// Small helper to make RPC calls with typed result.
async fn call_rpc<R: DeserializeOwned>(client: &RpcClient, method: &str, params: serde_json::Value) -> Result<R> {
    client.call::<R>(method, params).await
}

/// Encode bytes as lowercase `0x`-prefixed hex.
fn to_hex_prefixed(b: &[u8]) -> String {
    let mut s = String::with_capacity(2 + b.len() * 2);
    s.push_str("0x");
    s.push_str(&hex::encode(b));
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{AccessListItem, TxKind};

    // A minimal fake signer for tests
    struct DummySigner;
    impl TxSigner for DummySigner {
        fn alg_id(&self) -> u16 { 0x0103 } // Dilithium3
        fn public_key(&self) -> &[u8] { b"pk" }
        fn sign(&self, domain: &[u8], msg: &[u8]) -> Result<Vec<u8>> {
            // Non-cryptographic test signature = sha3_256(domain || msg)
            use sha3::{Digest, Sha3_256};
            let mut h = Sha3_256::new();
            h.update(domain);
            h.update(msg);
            Ok(h.finalize().to_vec())
        }
    }

    fn sample_tx() -> Tx {
        Tx {
            chain_id: 1,
            nonce: 1,
            gas_price: 1_000,
            gas_limit: 50_000,
            from: "anim1sender...".into(),
            to: Some("anim1dest...".into()),
            value: 0,
            data: vec![],
            access_list: vec![AccessListItem { address: "anim1x...".into(), storage_keys: vec![] }],
            kind: TxKind::Transfer,
        }
    }

    #[test]
    fn builds_signed_envelope() {
        let tx = sample_tx();
        let signer = DummySigner;
        let env = build_signed_envelope(&tx, &signer).expect("envelope");
        assert!(!env.is_empty());
        // Must be CBOR (starts with 0xd9 d9 f7 self-describe tag when using `self_describe`)
        // but serde_cbor::Serializer::self_describe writes the tag at the start of each top-level,
        // so the first byte is 0xd9.
        assert_eq!(env[0], 0xd9);
    }

    #[test]
    fn hex_prefix_helper() {
        let s = to_hex_prefixed(&[0xde, 0xad, 0xbe, 0xef]);
        assert_eq!(s, "0xdeadbeef");
    }
}
