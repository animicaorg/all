//! Byte/hex helpers used across the Rust SDK.
//!
//! Conventions:
//! - Hex strings are **lowercase** and **0x-prefixed** in canonical form.
//! - Decoders accept with/without `0x` and both cases; `canonical_hex` re-encodes canonically.
//! - Fixed-size helpers left-pad (big-endian) when shrinking into `[u8; N]`.

use crate::error::Error;

/// Whether the string starts with `0x` or `0X`.
#[inline]
pub fn has_0x(s: &str) -> bool {
    s.starts_with("0x") || s.starts_with("0X")
}

/// Strip a leading `0x`/`0X` prefix; return the original if absent.
#[inline]
pub fn strip_0x(s: &str) -> &str {
    if has_0x(s) { &s[2..] } else { s }
}

/// Ensure `0x` prefix on the given (possibly already-prefixed) hex string.
#[inline]
pub fn ensure_0x(s: &str) -> String {
    if has_0x(s) { s.to_string() } else { format!("0x{s}") }
}

/// Encode bytes to canonical lowercase `0x`-hex.
#[inline]
pub fn hex_encode<B: AsRef<[u8]>>(bytes: B) -> String {
    let b = bytes.as_ref();
    if b.is_empty() {
        // Still use "0x" for empty for consistency.
        "0x".to_string()
    } else {
        format!("0x{}", hex::encode(b))
    }
}

/// Decode hex into bytes. Accepts with/without `0x`. Allows odd-length by left-padding a zero nibble.
///
/// For strict decoding (even length, must have prefix), use [`hex_decode_strict`].
pub fn hex_decode(s: &str) -> Result<Vec<u8>, Error> {
    let mut hex = strip_0x(s).trim().to_string();
    if hex.is_empty() {
        return Ok(Vec::new());
    }
    if hex.len() % 2 == 1 {
        // Left-pad a zero to make length even.
        hex.insert(0, '0');
    }
    Ok(hex::decode(hex)?)
}

/// Strict variant: requires `0x` prefix and even length (after stripping).
pub fn hex_decode_strict(s: &str) -> Result<Vec<u8>, Error> {
    if !has_0x(s) {
        return Err(Error::InvalidParams("missing 0x prefix"));
    }
    let hex = strip_0x(s);
    if hex.len() % 2 == 1 {
        return Err(Error::InvalidParams("odd hex length"));
    }
    if hex.is_empty() {
        return Ok(Vec::new());
    }
    Ok(hex::decode(hex)?)
}

/// Canonicalize a hex string: decode leniently, then re-encode as lowercase `0x`-hex.
pub fn canonical_hex(s: &str) -> Result<String, Error> {
    let bytes = hex_decode(s)?;
    Ok(hex_encode(bytes))
}

/// Convert a byte slice into a fixed-size array `[u8; N]` by **left-padding with zeros**.
/// Returns an error if the input is longer than `N`.
pub fn left_pad_to_array<const N: usize>(bytes: &[u8]) -> Result<[u8; N], Error> {
    if bytes.len() > N {
        return Err(Error::InvalidParams("input longer than target array"));
    }
    let mut out = [0u8; N];
    let start = N - bytes.len();
    out[start..].copy_from_slice(bytes);
    Ok(out)
}

/// Convert a hex string into a fixed-size array `[u8; N]` (lenient hex parser + left-pad).
pub fn hex_to_fixed<const N: usize>(s: &str) -> Result<[u8; N], Error> {
    let bytes = hex_decode(s)?;
    left_pad_to_array::<N>(&bytes)
}

/// Join two byte slices (cheap helper).
#[inline]
pub fn concat(a: &[u8], b: &[u8]) -> Vec<u8> {
    let mut v = Vec::with_capacity(a.len() + b.len());
    v.extend_from_slice(a);
    v.extend_from_slice(b);
    v
}

/// Split a slice into `(head, tail)` at `n`. If `n` exceeds length, `tail` is empty.
#[inline]
pub fn split_at_safe<'a>(bytes: &'a [u8], n: usize) -> (&'a [u8], &'a [u8]) {
    if n > bytes.len() {
        (bytes, &[])
    } else {
        bytes.split_at(n)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_roundtrip() {
        let b = [0x01u8, 0xAB, 0x00, 0xFF];
        let h = hex_encode(&b);
        assert!(h.starts_with("0x"));
        assert_eq!(canonical_hex(&h).unwrap(), h);
        let d = hex_decode(&h).unwrap();
        assert_eq!(d, b);
    }

    #[test]
    fn hex_accepts_odd_len_and_no_prefix() {
        assert_eq!(hex_decode("f").unwrap(), vec![0x0f]);
        assert_eq!(hex_decode("0xabc").unwrap(), vec![0x0a, 0xbc]);
    }

    #[test]
    fn hex_strict_checks() {
        assert!(hex_decode_strict("ff").is_err());
        assert!(hex_decode_strict("0xfff").is_err());
        assert!(hex_decode_strict("0xff").is_ok());
    }

    #[test]
    fn fixed_left_pad() {
        let arr = left_pad_to_array::<4>(&[0x12, 0x34]).unwrap();
        assert_eq!(arr, [0, 0, 0x12, 0x34]);

        let arr2 = hex_to_fixed::<4>("0x1234").unwrap();
        assert_eq!(arr2, [0, 0, 0x12, 0x34]);

        assert!(left_pad_to_array::<2>(&[1, 2, 3]).is_err());
    }

    #[test]
    fn concat_and_split() {
        let a = [1, 2];
        let b = [3, 4, 5];
        let c = concat(&a, &b);
        assert_eq!(c, vec![1, 2, 3, 4, 5]);

        let (h, t) = split_at_safe(&c, 3);
        assert_eq!(h, &[1, 2, 3]);
        assert_eq!(t, &[4, 5]);

        let (h2, t2) = split_at_safe(&c, 99);
        assert_eq!(h2, &c[..]);
        assert!(t2.is_empty());
    }
}
