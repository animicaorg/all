//! Transaction builders for the three canonical kinds: **transfer**, **call**, **deploy**,
//! plus light-weight intrinsic gas estimation helpers.
//!
//! These builders return strongly-typed `Tx` objects defined in `crate::types`,
//! ready to be CBOR-encoded and signed by higher layers (see `tx::encode` and wallet modules).
//!
//! ### Address format
//! Use Bech32m `"anim1..."` strings consistently across SDKs. Validation can be
//! performed with `crate::utils::bech32` (if exposed) or `crate::address::Address::is_valid`.
//!
//! ### Intrinsic gas (heuristic)
//! We provide conservative estimates that align with the execution spec:
//! - Base costs per kind
//! - Data bytes: `G_DATA_ZERO` vs `G_DATA_NONZERO`
//! - Access list entries (optional, if you use them)
//!
//! You can ignore these helpers and provide explicit `gas_limit`/`gas_price`
//! if you rely on node-side estimation.
//!
//! ### Example
//! ```ignore
//! use animica_sdk::tx::build::{build_transfer, estimate_gas_transfer};
//! let tx = build_transfer(
//!     /*chain_id=*/1,
//!     /*from=*/"anim1...sender",
//!     /*to=*/"anim1...recipient",
//!     /*value=*/1_000_000u128,
//!     /*nonce=*/7,
//!     /*gas_price=*/1_000,
//!     /*gas_limit=*/estimate_gas_transfer().suggested_limit,
//! );
//! ```

use crate::types::{AccessListItem, Tx, TxKind};
use crate::utils::bytes::hex_to_vec_opt;

/// Gas constants (heuristic, chosen to be compatible with execution/spec tests).
/// If the chain evolves, client-side estimates can be adjusted without affecting consensus.
pub const G_BASE_TRANSFER: u64 = 21_000;
pub const G_BASE_CALL: u64 = 25_000;
pub const G_BASE_DEPLOY: u64 = 100_000;

pub const G_DATA_ZERO: u64 = 4;
pub const G_DATA_NONZERO: u64 = 16;

pub const G_ACCESS_LIST_ADDRESS: u64 = 2_400;
pub const G_ACCESS_LIST_STORAGE_KEY: u64 = 1_900;

/// Result of a simple intrinsic-gas calculation, including a padded suggestion.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GasEstimate {
    /// Raw intrinsic gas for this payload.
    pub intrinsic: u64,
    /// A conservative limit (intrinsic + headroom).
    pub suggested_limit: u64,
}

/// Build a **transfer** transaction.
///
/// * `from`/`to`: bech32m addresses (`anim1...`)
/// * `value`: the amount to transfer (chain's base unit)
/// * `gas_price`/`gas_limit`: policy-specific; you can use `estimate_gas_transfer()`.
pub fn build_transfer(
    chain_id: u64,
    from: &str,
    to: &str,
    value: u128,
    nonce: u64,
    gas_price: u64,
    gas_limit: u64,
) -> Tx {
    Tx {
        chain_id,
        nonce,
        gas_price,
        gas_limit,
        from: from.to_string(),
        to: Some(to.to_string()),
        value,
        data: Vec::new(),
        access_list: Vec::new(),
        kind: TxKind::Transfer,
    }
}

/// Build a **call** transaction (invoke a method on an existing contract).
///
/// * `to`: target contract address (bech32m)
/// * `data`: ABI-encoded call data (hex `0x...` or raw bytes)
/// * `value`: optional value transfer (usually `0`)
pub fn build_call(
    chain_id: u64,
    from: &str,
    to: &str,
    value: u128,
    nonce: u64,
    gas_price: u64,
    gas_limit: u64,
    data: impl AsRef<[u8]>,
    access_list: Vec<AccessListItem>,
) -> Tx {
    Tx {
        chain_id,
        nonce,
        gas_price,
        gas_limit,
        from: from.to_string(),
        to: Some(to.to_string()),
        value,
        data: data.as_ref().to_vec(),
        access_list,
        kind: TxKind::Call,
    }
}

/// Build a **deploy** transaction (create a new contract).
///
/// * `code`: compiled VM bytecode/IR package (per chain's format)
/// * `constructor_data`: optional ABI-encoded constructor args (may be empty)
pub fn build_deploy(
    chain_id: u64,
    from: &str,
    value: u128,
    nonce: u64,
    gas_price: u64,
    gas_limit: u64,
    code: impl AsRef<[u8]>,
    constructor_data: impl AsRef<[u8]>,
    access_list: Vec<AccessListItem>,
) -> Tx {
    let mut payload = Vec::with_capacity(code.as_ref().len() + constructor_data.as_ref().len() + 1);
    // Simple concatenation layout: 0x00 separator | code | constructor_data
    // (The canonical layout is defined by spec/tx_format.cddl; this is SDK-side
    //  packing for convenience. The encode/sign layer will CBOR the struct.)
    payload.push(0x00);
    payload.extend_from_slice(code.as_ref());
    payload.extend_from_slice(constructor_data.as_ref());

    Tx {
        chain_id,
        nonce,
        gas_price,
        gas_limit,
        from: from.to_string(),
        to: None, // deploy has no `to`
        value,
        data: payload,
        access_list,
        kind: TxKind::Deploy,
    }
}

// ----------------------------- Gas Estimators --------------------------------

/// Estimate gas for a **transfer** (no data, no access list).
pub fn estimate_gas_transfer() -> GasEstimate {
    let intrinsic = G_BASE_TRANSFER;
    GasEstimate {
        intrinsic,
        suggested_limit: intrinsic + 3_000, // small headroom for bookkeeping
    }
}

/// Estimate gas for a **call** given payload size and access list sizing.
pub fn estimate_gas_call(data: &[u8], access_list: &[AccessListItem]) -> GasEstimate {
    let mut intrinsic = G_BASE_CALL + cost_data_bytes(data) + cost_access_list(access_list);
    // Add a modest safety margin (ABI decoding + event writes varies)
    let suggested = intrinsic.saturating_add(50_000);
    GasEstimate {
        intrinsic,
        suggested_limit: suggested,
    }
}

/// Estimate gas for a **deploy** given code + constructor payload and access list sizing.
pub fn estimate_gas_deploy(code: &[u8], constructor_data: &[u8], access_list: &[AccessListItem]) -> GasEstimate {
    let mut intrinsic =
        G_BASE_DEPLOY + cost_data_bytes(code) + cost_data_bytes(constructor_data) + cost_access_list(access_list);
    let suggested = intrinsic.saturating_add(150_000); // extra margin for init execution
    GasEstimate {
        intrinsic,
        suggested_limit: suggested,
    }
}

#[inline]
fn cost_data_bytes(data: &[u8]) -> u64 {
    let mut zeros = 0u64;
    let mut nonzeros = 0u64;
    for &b in data {
        if b == 0 {
            zeros += 1;
        } else {
            nonzeros += 1;
        }
    }
    zeros * G_DATA_ZERO + nonzeros * G_DATA_NONZERO
}

#[inline]
fn cost_access_list(access_list: &[AccessListItem]) -> u64 {
    let mut total = 0u64;
    for item in access_list {
        // 1 address + N storage keys
        total = total
            .saturating_add(G_ACCESS_LIST_ADDRESS)
            .saturating_add((item.storage_keys.len() as u64) * G_ACCESS_LIST_STORAGE_KEY);
    }
    total
}

// ----------------------------- Convenience -----------------------------------

/// Helper: accept `0x`-prefixed hex for `data` in `build_call`.
///
/// If `maybe_hex` starts with `0x`, it is decoded; otherwise, bytes are used as-is.
pub fn build_call_hexdata(
    chain_id: u64,
    from: &str,
    to: &str,
    value: u128,
    nonce: u64,
    gas_price: u64,
    gas_limit: u64,
    data_or_hex: &str,
    access_list: Vec<AccessListItem>,
) -> Tx {
    let data = if let Some(bytes) = hex_to_vec_opt(data_or_hex) {
        bytes
    } else {
        data_or_hex.as_bytes().to_vec()
    };
    build_call(
        chain_id,
        from,
        to,
        value,
        nonce,
        gas_price,
        gas_limit,
        data,
        access_list,
    )
}

// ---------------------------------- Tests ------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::AccessListItem;

    #[test]
    fn transfer_builder_minimal() {
        let tx = build_transfer(1, "anim1sender...", "anim1dest...", 123u128, 9, 1_000, 25_000);
        assert!(matches!(tx.kind, TxKind::Transfer));
        assert_eq!(tx.to.as_deref(), Some("anim1dest..."));
        assert_eq!(tx.value, 123u128);
        assert_eq!(tx.chain_id, 1);
        assert_eq!(tx.nonce, 9);
    }

    #[test]
    fn call_and_gas_estimate() {
        let al = vec![
            AccessListItem { address: "anim1abc...".into(), storage_keys: vec![] },
            AccessListItem { address: "anim1def...".into(), storage_keys: vec![vec![1u8; 32], vec![2u8; 32]] },
        ];
        let data = vec![0u8, 1, 2, 3, 0, 0, 4];
        let est = estimate_gas_call(&data, &al);
        assert!(est.intrinsic >= G_BASE_CALL);
        assert!(est.suggested_limit > est.intrinsic);

        let tx = build_call(1, "anim1sender...", "anim1contract...", 0, 10, 1_000, est.suggested_limit, &data, al);
        assert!(matches!(tx.kind, TxKind::Call));
        assert_eq!(tx.to.as_deref(), Some("anim1contract..."));
        assert_eq!(tx.data.len(), 7);
    }

    #[test]
    fn deploy_and_gas_estimate() {
        let code = vec![1u8; 200];
        let ctor = vec![0u8; 16];
        let al = Vec::<AccessListItem>::new();
        let est = estimate_gas_deploy(&code, &ctor, &al);
        assert!(est.suggested_limit > est.intrinsic);
        let tx = build_deploy(1, "anim1sender...", 0, 1, 1_000, est.suggested_limit, &code, &ctor, al);
        assert!(matches!(tx.kind, TxKind::Deploy));
        assert!(tx.to.is_none());
        assert!(tx.data.len() >= 1 + code.len() + ctor.len());
        assert_eq!(tx.data[0], 0x00);
    }
}
