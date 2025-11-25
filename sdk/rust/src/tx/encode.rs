//! Canonical CBOR encoding for transactions and signing.
//!
//! - **Sign bytes** are the canonical CBOR of the unsigned transaction body.
//! - Domain separation is handled by the signer (see `wallet::signer`); use
//!   `TX_SIGN_DOMAIN` below when calling a signer.
//! - We use an **integer-keyed map** for the transaction body to guarantee
//!   cross-language stability and canonical ordering across SDKs.
//!
//! ## Transaction map layout (integer keys)
//! ```text
//! 0: chain_id      (u64)
//! 1: nonce         (u64)
//! 2: gas_price     (u64)
//! 3: gas_limit     (u64)
//! 4: from          (string, bech32m "anim1...")
//! 5: to            (string or null)
//! 6: value         (u128; CBOR bignum tag 2 when > u64)
//! 7: data          (bytes)
//! 8: access_list   (array of [address, [storageKeyBytes...]])
//! 9: kind          (u8: 0=Transfer, 1=Call, 2=Deploy)
//! ```
//!
//! ## Signed envelope (optional helper)
//! For convenience we also expose `encode_signed_envelope`, which returns an
//! array `[ TxMap, SigMap ]` where `SigMap` uses integer keys as well:
//! ```text
//! 0: alg_id    (u16)
//! 1: public_key(bytes)
//! 2: signature(bytes)
//! ```
//!
//! These shapes match the Python and TypeScript SDKs.

use crate::error::{Error, Result};
use crate::types::{AccessListItem, Tx, TxKind};
use serde::ser::{Serialize, SerializeSeq};
use serde_cbor::ser::Serializer;

/// Domain string to pass into wallet signers for transaction signatures.
pub const TX_SIGN_DOMAIN: &[u8] = b"animica/tx/sign/v1";

#[inline]
fn ser_err<E: core::fmt::Display>(e: E) -> Error {
    Error::Serde(format!("CBOR encode error: {e}"))
}

/// Encode the unsigned transaction body into **canonical CBOR** (sign bytes).
pub fn encode_sign_bytes(tx: &Tx) -> Result<Vec<u8>> {
    encode_tx_map(tx)
}

/// Encode the transaction body as an **integer-keyed map** with canonical ordering.
pub fn encode_tx_map(tx: &Tx) -> Result<Vec<u8>> {
    let mut buf = Vec::with_capacity(256);
    let mut ser = Serializer::new(&mut buf);
    ser.self_describe()?;
    ser.canonical(); // enforce canonical key ordering

    // We always emit exactly 10 keys (0..=9)
    ser.serialize_map(Some(10))
        .map_err(|e| ser_err(e))?;

    // 0: chain_id
    ser.serialize_u8(0).map_err(ser_err)?;
    ser.serialize_u64(tx.chain_id).map_err(ser_err)?;

    // 1: nonce
    ser.serialize_u8(1).map_err(ser_err)?;
    ser.serialize_u64(tx.nonce).map_err(ser_err)?;

    // 2: gas_price
    ser.serialize_u8(2).map_err(ser_err)?;
    ser.serialize_u64(tx.gas_price).map_err(ser_err)?;

    // 3: gas_limit
    ser.serialize_u8(3).map_err(ser_err)?;
    ser.serialize_u64(tx.gas_limit).map_err(ser_err)?;

    // 4: from (string)
    ser.serialize_u8(4).map_err(ser_err)?;
    ser.serialize_str(&tx.from).map_err(ser_err)?;

    // 5: to (string or null)
    ser.serialize_u8(5).map_err(ser_err)?;
    if let Some(to) = &tx.to {
        ser.serialize_str(to).map_err(ser_err)?;
    } else {
        ser.serialize_none().map_err(ser_err)?;
    }

    // 6: value (u128 → CBOR bignum when needed)
    ser.serialize_u8(6).map_err(ser_err)?;
    ser.serialize_u128(tx.value).map_err(ser_err)?;

    // 7: data (bytes)
    ser.serialize_u8(7).map_err(ser_err)?;
    ser.serialize_bytes(&tx.data).map_err(ser_err)?;

    // 8: access_list (array of [address, [keys...]])
    ser.serialize_u8(8).map_err(ser_err)?;
    encode_access_list(&mut ser, &tx.access_list)?;

    // 9: kind (u8)
    ser.serialize_u8(9).map_err(ser_err)?;
    ser.serialize_u8(kind_to_u8(tx.kind)).map_err(ser_err)?;

    // end map
    ser.end().map_err(ser_err)?;
    Ok(buf)
}

/// Encode the signed envelope: `[ TxMap, {0:alg_id,1:pk,2:sig} ]`
pub fn encode_signed_envelope(tx: &Tx, alg_id: u16, public_key: &[u8], signature: &[u8]) -> Result<Vec<u8>> {
    let tx_bytes = encode_tx_map(tx)?;
    let mut buf = Vec::with_capacity(tx_bytes.len() + 128);
    let mut ser = Serializer::new(&mut buf);
    ser.self_describe()?;
    ser.canonical();

    // Outer array of length 2
    {
        let mut seq = ser.serialize_seq(Some(2)).map_err(ser_err)?;

        // Element 0: the pre-encoded Tx map as a CBOR byte string (embedded CBOR)
        // We intentionally embed the raw CBOR bytes to keep the exact canonical form.
        // Consumers can either treat it as bytes or re-decode if preferred.
        seq.serialize_element(&serde_bytes::Bytes::new(&tx_bytes))
            .map_err(ser_err)?;

        // Element 1: signature map {0:alg_id,1:pk,2:sig}
        {
            let mut sig_map = serde_cbor::ser::Compound::map(&mut ser, Some(3)).map_err(ser_err)?;
            sig_map.serialize_entry(&0u8, &alg_id).map_err(ser_err)?;
            // public_key and signature as byte strings
            sig_map
                .serialize_entry(&1u8, &serde_bytes::Bytes::new(public_key))
                .map_err(ser_err)?;
            sig_map
                .serialize_entry(&2u8, &serde_bytes::Bytes::new(signature))
                .map_err(ser_err)?;
            serde_cbor::ser::SerializeMap::end(sig_map).map_err(ser_err)?;
        }

        serde::ser::SerializeSeq::end(seq).map_err(ser_err)?;
    }

    Ok(buf)
}

#[inline]
fn kind_to_u8(kind: TxKind) -> u8 {
    match kind {
        TxKind::Transfer => 0,
        TxKind::Call => 1,
        TxKind::Deploy => 2,
    }
}

fn encode_access_list<W: serde_cbor::ser::Write>(
    ser: &mut Serializer<W>,
    al: &[AccessListItem],
) -> Result<()> {
    // Array of items
    let mut seq = ser.serialize_seq(Some(al.len())).map_err(ser_err)?;
    for item in al {
        // Each item is an array: [address, [keys...]]
        // address: string
        // keys: array of bytes
        seq.serialize_element(&AccessListItemSer(item))
            .map_err(ser_err)?;
    }
    serde::ser::SerializeSeq::end(seq).map_err(ser_err)?;
    Ok(())
}

/// Wrapper to implement a custom serialization view for AccessListItem.
struct AccessListItemSer<'a>(&'a AccessListItem);

impl<'a> Serialize for AccessListItemSer<'a> {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> core::result::Result<S::Ok, S::Error> {
        let item = self.0;
        let mut seq = serializer.serialize_seq(Some(2))?;
        seq.serialize_element(&item.address)?;
        // storage keys as bytes arrays
        struct KeyBytes<'k>(&'k [u8]);
        impl<'k> Serialize for KeyBytes<'k> {
            fn serialize<S: serde::Serializer>(&self, serializer: S) -> core::result::Result<S::Ok, S::Error> {
                serializer.serialize_bytes(self.0)
            }
        }
        let mut keys_seq = serializer.serialize_seq(Some(item.storage_keys.len()))?;
        for k in &item.storage_keys {
            keys_seq.serialize_element(&KeyBytes(k))?;
        }
        keys_seq.end()?;
        seq.end()
    }
}

// ----------------------------------- Tests -----------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{AccessListItem, Tx};

    fn sample_tx() -> Tx {
        Tx {
            chain_id: 1,
            nonce: 7,
            gas_price: 1_000,
            gas_limit: 50_000,
            from: "anim1senderxyz...".into(),
            to: Some("anim1destabc...".into()),
            value: 123_456_789_u128,
            data: vec![0u8, 1, 2, 3, 4],
            access_list: vec![
                AccessListItem { address: "anim1a...".into(), storage_keys: vec![] },
                AccessListItem { address: "anim1b...".into(), storage_keys: vec![vec![1u8; 32], vec![2u8; 32]] },
            ],
            kind: TxKind::Call,
        }
    }

    #[test]
    fn canonical_stability() {
        let tx = sample_tx();
        let a = encode_sign_bytes(&tx).unwrap();
        let b = encode_sign_bytes(&tx).unwrap();
        assert_eq!(a, b, "encoding must be byte-for-byte stable");
        // Changes in nonce must change bytes
        let mut tx2 = tx.clone();
        tx2.nonce += 1;
        let c = encode_sign_bytes(&tx2).unwrap();
        assert_ne!(a, c);
    }

    #[test]
    fn signed_envelope_has_two_elements() {
        let tx = sample_tx();
        let env = encode_signed_envelope(&tx, 0x0103, b"pk", b"sig").unwrap();
        // Decode as generic CBOR value to sanity-check basic structure
        let val: serde_cbor::Value = serde_cbor::from_slice(&env).unwrap();
        match val {
            serde_cbor::Value::Array(mut v) => {
                assert_eq!(v.len(), 2);
                // first element is bytes (embedded cbor)
                match v.remove(0) {
                    serde_cbor::Value::Bytes(b) => assert!(!b.is_empty()),
                    _ => panic!("first element should be bytes"),
                }
                // second element is a map with 3 entries
                match v.remove(0) {
                    serde_cbor::Value::Map(m) => assert_eq!(m.len(), 3),
                    _ => panic!("second element should be a map"),
                }
            }
            _ => panic!("envelope must be an array"),
        }
    }
}
