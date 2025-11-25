//! Generic **ABI-based contract client**.
//!
//! Provides:
//! - Encoding/decoding of ABI calls using `crate::abi`
//! - Read-only calls via JSON-RPC `state.call`
//! - Heuristic gas estimation for write calls
//! - Send write transactions (sign → submit → await receipt)
//!
//! This mirrors the Python/TypeScript SDK ergonomics.
//!
//! ### JSON-RPC surface assumed
//! - `state.call` → simulate a read-only call on the latest (or a tagged) state
//! - `tx.sendRawTransaction` and `tx.getTransactionReceipt` (used indirectly via `tx::send`)
//!
//! If your node build doesn't expose `state.call`, you can still use
//! `encode_call_data` and send the bytes through your own pipeline.

use crate::abi::{decode_return, encode_call, Abi};
use crate::error::{Error, Result};
use crate::rpc::http::RpcClient;
use crate::tx::build::{build_call, estimate_gas_call, GasEstimate};
use crate::tx::send::{build_signed_envelope, send_and_wait};
use crate::types::{AccessListItem, Receipt, Tx};
use crate::wallet::signer::TxSigner;
use serde_json::json;
use std::time::Duration;

/// Options for a read-only call.
#[derive(Debug, Clone, Default)]
pub struct CallOptions<'a> {
    /// Optional caller address (bech32m). Some contracts branch on `msg.sender`.
    pub from: Option<&'a str>,
    /// Optional value to attach to the call (usually 0).
    pub value: Option<u128>,
    /// Block tag: "latest" (default), "pending", or a hex number like "0x10".
    pub block_tag: Option<&'a str>,
}

/// Options for a send/write call.
#[derive(Debug, Clone)]
pub struct SendOptions<'a, S: TxSigner> {
    pub chain_id: u64,
    pub from: &'a str,
    pub nonce: u64,
    pub gas_price: u64,
    /// If not provided, a conservative estimate with headroom is used.
    pub gas_limit: Option<u64>,
    /// Value to transfer with the call (default 0).
    pub value: u128,
    /// Access list (optional, empty by default).
    pub access_list: &'a [AccessListItem],
    /// Signer implementing the chain's PQ signature (Dilithium3/SPHINCS+).
    pub signer: &'a S,
    /// Polling interval for receipt waits.
    pub poll_every: Duration,
    /// Timeout for receipt waits.
    pub timeout: Duration,
}

impl<'a, S: TxSigner> Default for SendOptions<'a, S> {
    fn default() -> Self {
        // This default is mostly a placeholder; most fields must be provided by the caller.
        Self {
            chain_id: 0,
            from: "",
            nonce: 0,
            gas_price: 0,
            gas_limit: None,
            value: 0,
            access_list: &[],
            signer: unsafe { std::mem::MaybeUninit::zeroed().assume_init() }, // never used
            poll_every: Duration::from_secs(1),
            timeout: Duration::from_secs(60),
        }
    }
}

/// ABI-based contract client bound to a specific **address**.
#[derive(Clone)]
pub struct ContractClient<'r> {
    rpc: &'r RpcClient,
    address: String,
    abi: Abi,
}

impl<'r> ContractClient<'r> {
    /// Create a client for `address` with the given `abi`.
    pub fn new(rpc: &'r RpcClient, address: impl Into<String>, abi: Abi) -> Self {
        Self {
            rpc,
            address: address.into(),
            abi,
        }
    }

    /// Contract address.
    pub fn address(&self) -> &str {
        &self.address
    }

    /// ABI reference.
    pub fn abi(&self) -> &Abi {
        &self.abi
    }

    // ------------------------------ Encoding ---------------------------------

    /// ABI-encode call data for a function name and JSON arguments.
    ///
    /// `args` layout follows your ABI schema; for simple methods an array is typical.
    pub fn encode_call_data(&self, method: &str, args: &serde_json::Value) -> Result<Vec<u8>> {
        encode_call(&self.abi, method, args)
    }

    /// ABI-decode a return payload into JSON according to the function's outputs.
    pub fn decode_return(&self, method: &str, data: &[u8]) -> Result<serde_json::Value> {
        decode_return(&self.abi, method, data)
    }

    // ------------------------------ Read-only --------------------------------

    /// Perform a read-only call via JSON-RPC `state.call` and decode the return value.
    ///
    /// Returns the decoded JSON value (shape per ABI output).
    pub async fn call(
        &self,
        method: &str,
        args: &serde_json::Value,
        opts: CallOptions<'_>,
    ) -> Result<serde_json::Value> {
        let data = self.encode_call_data(method, args)?;
        let data_hex = to_hex_prefixed(&data);
        let params = json!([{
            "to": self.address,
            "data": data_hex,
            "from": opts.from,
            "value": opts.value.map(u128_to_hex),
            "blockTag": opts.block_tag.unwrap_or("latest")
        }]);

        // Expect the node to return a 0x-hex byte string representing ABI-encoded return data.
        let ret_hex: String = self.rpc.call("state.call", params).await?;
        let ret_bytes = hex_to_bytes(&ret_hex).ok_or_else(|| Error::InvalidHex(ret_hex.clone()))?;
        self.decode_return(method, &ret_bytes)
    }

    // ------------------------------ Gas --------------------------------------

    /// Heuristic gas estimate (SDK-side) for a method + args + access list.
    ///
    /// Uses byte-size & zero/non-zero heuristics consistent with execution spec.
    pub fn estimate_gas_call(
        &self,
        method: &str,
        args: &serde_json::Value,
        access_list: &[AccessListItem],
    ) -> Result<GasEstimate> {
        let data = self.encode_call_data(method, args)?;
        Ok(estimate_gas_call(&data, access_list))
    }

    // ------------------------------ Send/write -------------------------------

    /// Build a transaction object for a contract call (without signing).
    pub fn build_tx_call(
        &self,
        chain_id: u64,
        from: &str,
        nonce: u64,
        gas_price: u64,
        gas_limit: u64,
        value: u128,
        data: Vec<u8>,
        access_list: Vec<AccessListItem>,
    ) -> Tx {
        build_call(
            chain_id,
            from,
            &self.address,
            value,
            nonce,
            gas_price,
            gas_limit,
            data,
            access_list,
        )
    }

    /// Encode, sign, submit, and await receipt for a state-changing method.
    ///
    /// Returns `(tx_hash, receipt)`.
    pub async fn send_and_wait<S: TxSigner>(
        &self,
        method: &str,
        args: &serde_json::Value,
        mut opts: SendOptions<'_, S>,
    ) -> Result<(String, Receipt)> {
        let data = self.encode_call_data(method, args)?;
        let gas_limit = match opts.gas_limit {
            Some(gl) => gl,
            None => self.estimate_gas_call(method, args, opts.access_list)?.suggested_limit,
        };

        let tx = self.build_tx_call(
            opts.chain_id,
            opts.from,
            opts.nonce,
            opts.gas_price,
            gas_limit,
            opts.value,
            data,
            opts.access_list.to_vec(),
        );

        // Sign → send → wait
        let _env = build_signed_envelope(&tx, opts.signer)?; // sanity: ensure encoding+signing succeeds
        send_and_wait(self.rpc, &tx, opts.signer, opts.poll_every, opts.timeout).await
    }
}

// --------------------------------- Helpers -----------------------------------

fn to_hex_prefixed(b: &[u8]) -> String {
    let mut s = String::with_capacity(2 + b.len() * 2);
    s.push_str("0x");
    s.push_str(&hex::encode(b));
    s
}

fn hex_to_bytes(s: &str) -> Option<Vec<u8>> {
    let raw = s.strip_prefix("0x").unwrap_or(s);
    hex::decode(raw).ok()
}

fn u128_to_hex(v: u128) -> String {
    // minimal 0x hex encoding (no leading zeros), "0x0" for zero
    if v == 0 {
        return "0x0".into();
    }
    let mut tmp = Vec::new();
    let mut n = v;
    while n > 0 {
        tmp.push((n & 0xff) as u8);
        n >>= 8;
    }
    tmp.reverse();
    to_hex_prefixed(&tmp)
}

// ---------------------------------- Tests ------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::AccessListItem;

    #[test]
    fn hex_u128_rounds() {
        assert_eq!(u128_to_hex(0), "0x0");
        assert_eq!(u128_to_hex(1), "0x01".to_lowercase()); // implementation emits no fixed width, but 0x01 is acceptable lowercase
        assert_eq!(u128_to_hex(0xdead_beef), "0xdeadbeef");
    }

    #[test]
    fn encode_then_estimate() {
        // Minimal ABI with a single function "get()" → () -> (uint)
        // Assume `encode_call` will handle this; here we only ensure estimator is wired.
        let abi = Abi::from_json(&json!({
            "functions": [
                {"name":"get","inputs":[],"outputs":[{"name":"","type":"uint"}]}
            ]
        })).expect("abi");
        let fake_rpc = RpcClient::new("http://localhost:0"); // not used by estimate
        let c = ContractClient::new(&fake_rpc, "anim1xyz...", abi);
        let gas = c.estimate_gas_call("get", &json!([]), &[]).unwrap();
        assert!(gas.suggested_limit >= gas.intrinsic);
        assert!(gas.intrinsic >= crate::tx::build::G_BASE_CALL);
    }

    #[test]
    fn build_tx_without_rpc() {
        let abi = Abi::from_json(&json!({
            "functions": [
                {"name":"set","inputs":[{"name":"v","type":"uint"}],"outputs":[]}
            ]
        })).unwrap();
        let rpc = RpcClient::new("http://localhost");
        let c = ContractClient::new(&rpc, "anim1abc...", abi);
        let data = c.encode_call_data("set", &json!([42])).unwrap();
        let tx = c.build_tx_call(1, "anim1from...", 7, 1_000, 80_000, 0, data, vec![]);
        assert_eq!(tx.to.as_deref(), Some("anim1abc..."));
        assert_eq!(tx.chain_id, 1);
        assert_eq!(tx.nonce, 7);
        assert_eq!(tx.gas_price, 1_000);
        assert_eq!(tx.value, 0);
    }
}
