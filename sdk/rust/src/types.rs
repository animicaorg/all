//! Core chain types for Animica.
//!
//! These models are intentionally conservative and forward-compatible:
//! - Numeric fields are `u64` where practical.
//! - Hex/Hash/Bytes surfaces are `String` with `0x`-hex canonicalization left to callers.
//! - Unknown/extension fields from the node are preserved via `#[serde(flatten)]` where helpful.
//!
//! For address manipulation, see `crate::address`.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Canonical `0x`-prefixed hex string (case-insensitive).
pub type Hex = String;

/// Canonical `0x`-prefixed 32-byte Keccak/SHA3 hash.
pub type Hash = String;

/// Bech32m `anim1...` address (string form).
pub type Address = String;

/// Chain identifier.
pub type ChainId = u64;

/// Transaction kind (wire-level).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TxKind {
    Transfer,
    Deploy,
    Call,
    #[serde(other)]
    Other,
}

/// Transaction status used by receipts.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum TxStatus {
    SUCCESS,
    REVERT,
    OOG,
    #[serde(other)]
    UNKNOWN,
}

/// Minimal head snapshot returned by `chain.getHead`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Head {
    pub number: u64,
    pub hash: Hash,
    /// Seconds since epoch (node-defined block time).
    pub timestamp: u64,
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

/// Light-weight transaction view as returned by RPC methods.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TxView {
    pub hash: Hash,
    pub from: Address,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub to: Option<Address>,
    pub nonce: u64,
    pub gas_price: u64,
    pub gas_limit: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value: Option<u64>,
    pub chain_id: ChainId,
    pub kind: TxKind,
    /// For `call` this is the ABI-encoded payload (hex).
    /// For `deploy` this may be the code bytes (hex) or a content hash.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<Hex>,
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

/// Canonical transaction object used by the SDK before encoding.
///
/// Note: this mirrors `TxView` but omits the `hash` (pre-send) and keeps fields
/// that are required to build the sign-bytes domain.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Tx {
    pub from: Address,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub to: Option<Address>,
    pub nonce: u64,
    pub gas_price: u64,
    pub gas_limit: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub value: Option<u64>,
    pub chain_id: ChainId,
    pub kind: TxKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<Hex>,
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

/// Event/log entry emitted by contract execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogEvent {
    pub address: Address,
    pub topics: Vec<Hash>,
    /// ABI-encoded data (hex).
    pub data: Hex,
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

/// Transaction receipt returned by the node.
///
/// If the transaction is still pending or not found, RPC will return null (caller-side).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Receipt {
    pub tx_hash: Hash,
    pub status: TxStatus,
    pub gas_used: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub block_hash: Option<Hash>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub block_number: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub contract_address: Option<Address>,
    #[serde(default)]
    pub logs: Vec<LogEvent>,
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

/// Block header view.
///
/// Fields marked optional may be omitted by minimal RPCs or on light clients.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Header {
    pub hash: Hash,
    pub parent_hash: Hash,
    pub number: u64,
    pub timestamp: u64,
    pub chain_id: ChainId,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub state_root: Option<Hash>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub txs_root: Option<Hash>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub receipts_root: Option<Hash>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub da_root: Option<Hash>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mix_seed: Option<Hex>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub nonce: Option<Hex>,
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

/// Block view. Depending on RPC flags, transactions may be hashes or full `TxView`s.
/// This model exposes them as full views; servers that only return hashes can be mapped by callers.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Block {
    pub header: Header,
    #[serde(default)]
    pub txs: Vec<TxView>,
    #[serde(default)]
    pub receipts: Vec<Receipt>,
    #[serde(default)]
    #[serde(flatten)]
    pub extra: BTreeMap<String, serde_json::Value>,
}

impl Head {
    /// Height convenience alias.
    pub fn height(&self) -> u64 {
        self.number
    }
}

impl Tx {
    /// Convenience builder for a minimal transfer.
    pub fn transfer(from: Address, to: Address, value: u64, nonce: u64, gas_price: u64, gas_limit: u64, chain_id: ChainId) -> Self {
        Self {
            from,
            to: Some(to),
            nonce,
            gas_price,
            gas_limit,
            value: Some(value),
            chain_id,
            kind: TxKind::Transfer,
            data: None,
            extra: BTreeMap::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn serde_head_roundtrip() {
        let h = Head {
            number: 10,
            hash: "0x01".into(),
            timestamp: 123,
            extra: BTreeMap::new(),
        };
        let j = serde_json::to_string(&h).unwrap();
        let h2: Head = serde_json::from_str(&j).unwrap();
        assert_eq!(h2.number, 10);
    }

    #[test]
    fn serde_tx_roundtrip() {
        let tx = Tx {
            from: "anim1abcd...".into(),
            to: Some("anim1wxyz...".into()),
            nonce: 1,
            gas_price: 1,
            gas_limit: 21_000,
            value: Some(100),
            chain_id: 1,
            kind: TxKind::Transfer,
            data: None,
            extra: BTreeMap::new(),
        };
        let j = serde_json::to_string(&tx).unwrap();
        let _tx2: Tx = serde_json::from_str(&j).unwrap();
    }

    #[test]
    fn serde_receipt_roundtrip() {
        let r = Receipt {
            tx_hash: "0xdead".into(),
            status: TxStatus::SUCCESS,
            gas_used: 1000,
            block_hash: Some("0xbeef".into()),
            block_number: Some(2),
            contract_address: None,
            logs: vec![],
            extra: BTreeMap::new(),
        };
        let j = serde_json::to_string(&r).unwrap();
        let _r2: Receipt = serde_json::from_str(&j).unwrap();
    }
}
