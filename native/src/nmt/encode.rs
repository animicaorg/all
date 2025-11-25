//! NMT leaf encoding helpers (namespace + length prefix).
//!
//! This module defines a compact, unambiguous encoding for **leaves** of the
//! Namespaced Merkle Tree (NMT). The encoding is used by the NMT hashers when
//! producing a leaf node tuple `(min_ns, max_ns, hash)` where, for leaves,
//! `min_ns == max_ns == ns`.
//!
//! ## Format
//! ```text
//! LEAF := NS(8 bytes, BE) || LEN(4 bytes, BE) || DATA[LEN]
//! ```
//!
//! - **NS** is the leaf's namespace as a big-endian `u64`.
//! - **LEN** is the byte length of `DATA` as an unsigned big-endian `u32`.
//! - **DATA** is the raw leaf payload.
//!
//! This scheme is simple, stable, and safe for concatenation, and avoids
//! collisions between different `(ns, data)` pairs.

use super::types::NamespaceId;

/// Number of bytes used to encode a namespace.
pub const NS_ENC_LEN: usize = 8;
/// Number of bytes used to encode the data length.
pub const LEN_ENC_LEN: usize = 4;

/// Compute the final encoded length for a leaf with `data_len`.
#[inline]
pub const fn encoded_leaf_len(data_len: usize) -> usize {
    NS_ENC_LEN + LEN_ENC_LEN + data_len
}

/// Encode a leaf (namespace + length prefix + data) into the provided buffer.
///
/// The buffer is *appended to*; existing contents are preserved.
#[inline]
pub fn encode_leaf_to(ns: NamespaceId, data: &[u8], out: &mut Vec<u8>) {
    out.reserve(encoded_leaf_len(data.len()));

    // Namespace (u64 big-endian)
    out.extend_from_slice(&ns.to_be_bytes());

    // Length (u32 big-endian). We cap at u32::MAX to keep the format fixed-size.
    let len_u32 = u32::try_from(data.len())
        .expect("leaf too large to encode (length exceeds u32::MAX)");
    out.extend_from_slice(&len_u32.to_be_bytes());

    // Payload
    out.extend_from_slice(data);
}

/// Encode a leaf and return a freshly allocated `Vec<u8>`.
#[inline]
pub fn encode_leaf(ns: NamespaceId, data: &[u8]) -> Vec<u8> {
    let mut v = Vec::with_capacity(encoded_leaf_len(data.len()));
    encode_leaf_to(ns, data, &mut v);
    v
}

/// Errors returned when *decoding* an encoded leaf.
#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum DecodeError {
    #[error("buffer too short for namespace and length prefix")]
    TooShort,
    #[error("declared length exceeds u32::MAX or available buffer")]
    LengthInvalid,
}

/// Decode a leaf from the given buffer, returning the `(ns, data_slice)` pair.
///
/// The returned `data` is a **borrowed** subslice of `buf` (zero-copy).
#[inline]
pub fn decode_leaf(buf: &[u8]) -> Result<(NamespaceId, &[u8]), DecodeError> {
    if buf.len() < NS_ENC_LEN + LEN_ENC_LEN {
        return Err(DecodeError::TooShort);
    }

    // Read namespace
    let mut ns_bytes = [0u8; NS_ENC_LEN];
    ns_bytes.copy_from_slice(&buf[..NS_ENC_LEN]);
    let ns = u64::from_be_bytes(ns_bytes);

    // Read length
    let mut len_bytes = [0u8; LEN_ENC_LEN];
    len_bytes.copy_from_slice(&buf[NS_ENC_LEN..NS_ENC_LEN + LEN_ENC_LEN]);
    let len = u32::from_be_bytes(len_bytes) as usize;

    let start = NS_ENC_LEN + LEN_ENC_LEN;
    let end = start.checked_add(len).ok_or(DecodeError::LengthInvalid)?;
    if end > buf.len() {
        return Err(DecodeError::LengthInvalid);
    }

    Ok((ns, &buf[start..end]))
}

/* --------------------------------- Tests -------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use super::super::types::ns_from_u64;

    #[test]
    fn encode_len_matches() {
        let d = vec![0u8; 13];
        assert_eq!(encoded_leaf_len(d.len()), 8 + 4 + 13);
        let enc = encode_leaf(42, &d);
        assert_eq!(enc.len(), 25);
    }

    #[test]
    fn roundtrip_small_payload() {
        let ns: NamespaceId = ns_from_u64(0xAABBCCDD00112233);
        let data = b"hello world";
        let enc = encode_leaf(ns, data);
        let (dec_ns, dec_data) = decode_leaf(&enc).expect("decode ok");
        assert_eq!(dec_ns, ns);
        assert_eq!(dec_data, data);
    }

    #[test]
    fn decode_rejects_short() {
        let buf = [0u8; 5];
        assert_eq!(decode_leaf(&buf).unwrap_err(), DecodeError::TooShort);
    }

    #[test]
    fn decode_rejects_overflow() {
        // Correct prefix but declare absurd length that exceeds buffer.
        let ns = 7u64.to_be_bytes();
        let len = (1024u32).to_be_bytes();
        let mut buf = Vec::new();
        buf.extend_from_slice(&ns);
        buf.extend_from_slice(&len);
        buf.extend_from_slice(&[0u8; 10]); // only 10 bytes, but 1024 claimed
        assert_eq!(decode_leaf(&buf).unwrap_err(), DecodeError::LengthInvalid);
    }

    #[test]
    fn encode_to_appends() {
        let ns = 9u64;
        let data = [1u8, 2, 3];
        let mut dst = vec![0xFF]; // sentinel
        encode_leaf_to(ns, &data, &mut dst);
        assert_eq!(dst[0], 0xFF);
        // Check that the rest decodes properly.
        let (dec_ns, dec_data) = decode_leaf(&dst[1..]).unwrap();
        assert_eq!(dec_ns, ns);
        assert_eq!(dec_data, &data);
    }
}
