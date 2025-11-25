//! Byte-oriented helpers: zero-copy typed views, hex encode/decode,
//! and small alignment utilities.
//!
//! These functions avoid allocations where possible and validate alignment
//!/length constraints before performing `unsafe` casts.

use core::{mem, ptr, slice};
use crate::error::{NativeError, NativeResult};

use super::{is_aligned, round_up_to};

/// Attempt to view a `&[u8]` as `&[T]` **without copying**.
///
/// Requirements:
/// - Pointer must be aligned for `T`
/// - `bytes.len()` must be a multiple of `size_of::<T>()`
///
/// Returns an error if requirements are not met.
#[inline]
pub fn cast_slice<T>(bytes: &[u8]) -> NativeResult<&[T]> {
    let size = mem::size_of::<T>();
    if size == 0 {
        // Zero-sized types are weird; reject to simplify invariants.
        return Err(NativeError::InvalidArgument("cast_slice: zero-sized type"));
    }
    if bytes.len() % size != 0 {
        return Err(NativeError::InvalidArgument("cast_slice: length not multiple of T"));
    }
    let ptr = bytes.as_ptr();
    if !is_aligned(ptr, mem::align_of::<T>()) {
        return Err(NativeError::InvalidArgument("cast_slice: misaligned pointer for T"));
    }
    let len = bytes.len() / size;
    // SAFETY: We validated alignment and that the length is a multiple of T.
    Ok(unsafe { slice::from_raw_parts(ptr as *const T, len) })
}

/// Mutable variant of [`cast_slice`].
#[inline]
pub fn cast_slice_mut<T>(bytes: &mut [u8]) -> NativeResult<&mut [T]> {
    let size = mem::size_of::<T>();
    if size == 0 {
        return Err(NativeError::InvalidArgument("cast_slice_mut: zero-sized type"));
    }
    if bytes.len() % size != 0 {
        return Err(NativeError::InvalidArgument("cast_slice_mut: length not multiple of T"));
    }
    let ptr = bytes.as_mut_ptr();
    if !is_aligned(ptr, mem::align_of::<T>()) {
        return Err(NativeError::InvalidArgument("cast_slice_mut: misaligned pointer for T"));
    }
    let len = bytes.len() / size;
    // SAFETY: We validated alignment and divisibility.
    Ok(unsafe { slice::from_raw_parts_mut(ptr as *mut T, len) })
}

/// Produce an aligned typed view in the middle of a byte slice without copying.
///
/// Returns `(head_bytes, typed_mid, tail_bytes)` where:
/// - `head_bytes` is any leading unaligned/extra bytes
/// - `typed_mid` is a `&[T]` aligned and sized to `T`
/// - `tail_bytes` is trailing remainder bytes (if total len not multiple of `T`)
///
/// This never errors; the `typed_mid` may be empty if alignment/size don't permit more.
#[inline]
pub fn aligned_view<T>(bytes: &[u8]) -> (&[u8], &[T], &[u8]) {
    let align = mem::align_of::<T>();
    let size  = mem::size_of::<T>();
    assert!(size > 0, "ZST not supported");

    let base = bytes.as_ptr() as usize;
    let offset = (align - (base & (align - 1))) & (align - 1);
    let offset = offset.min(bytes.len());

    // After skipping misaligned prefix, cap mid length to a multiple of T.
    let mid_len_bytes_total = bytes.len().saturating_sub(offset);
    let mid_len_bytes = mid_len_bytes_total - (mid_len_bytes_total % size);

    let (head, rest) = bytes.split_at(offset);
    let (mid_bytes, tail) = rest.split_at(mid_len_bytes);

    let mid_t_len = mid_len_bytes / size;
    let mid_t = if mid_t_len == 0 {
        // Empty view—return a canonical empty typed slice reference.
        // SAFETY: Using a well-formed empty slice.
        unsafe { slice::from_raw_parts(ptr::NonNull::<T>::dangling().as_ptr(), 0) }
    } else {
        // SAFETY: `mid_bytes` begins at an aligned address for T, and its len is a multiple of T.
        unsafe { slice::from_raw_parts(mid_bytes.as_ptr() as *const T, mid_t_len) }
    };

    (head, mid_t, tail)
}

/// Mutable variant of [`aligned_view`].
#[inline]
pub fn aligned_view_mut<T>(bytes: &mut [u8]) -> (&mut [u8], &mut [T], &mut [u8]) {
    let align = mem::align_of::<T>();
    let size  = mem::size_of::<T>();
    assert!(size > 0, "ZST not supported");

    let base = bytes.as_ptr() as usize;
    let offset = (align - (base & (align - 1))) & (align - 1);
    let offset = offset.min(bytes.len());

    let mid_len_bytes_total = bytes.len().saturating_sub(offset);
    let mid_len_bytes = mid_len_bytes_total - (mid_len_bytes_total % size);

    let (head, rest) = bytes.split_at_mut(offset);
    let (mid_bytes, tail) = rest.split_at_mut(mid_len_bytes);

    let mid_t_len = mid_len_bytes / size;
    let mid_t = if mid_t_len == 0 {
        // SAFETY: empty mutable slice
        unsafe { slice::from_raw_parts_mut(ptr::NonNull::<T>::dangling().as_ptr(), 0) }
    } else {
        // SAFETY: aligned & multiple of T
        unsafe { slice::from_raw_parts_mut(mid_bytes.as_mut_ptr() as *mut T, mid_t_len) }
    };

    (head, mid_t, tail)
}

/// Compute how many bytes you must add to `addr` to reach the next multiple of `alignment`.
///
/// Returns a value in `[0, alignment)`. `alignment` must be a power of two.
#[inline]
pub fn align_offset(addr: usize, alignment: usize) -> usize {
    debug_assert!(alignment.is_power_of_two(), "alignment must be power of two");
    (alignment - (addr & (alignment - 1))) & (alignment - 1)
}

/// Return the smallest `len' >= len` such that the end address is aligned.
///
/// Useful when reserving capacity to ensure a following typed view ends on a boundary.
#[inline]
pub fn pad_to_end_align(start_addr: usize, len: usize, alignment: usize) -> usize {
    let end = start_addr.saturating_add(len);
    let padded_end = round_up_to(end, alignment);
    padded_end.saturating_sub(start_addr)
}

/* ----------------------- HEX ENCODE / DECODE ----------------------- */

const HEX_LOWER: &[u8; 16] = b"0123456789abcdef";
const HEX_UPPER: &[u8; 16] = b"0123456789ABCDEF";

#[inline]
fn nybble_to_hex(n: u8, upper: bool) -> u8 {
    let table = if upper { HEX_UPPER } else { HEX_LOWER };
    table[(n & 0x0F) as usize]
}

#[inline]
fn hex_to_nybble(c: u8) -> Option<u8> {
    match c {
        b'0'..=b'9' => Some(c - b'0'),
        b'a'..=b'f' => Some(10 + (c - b'a')),
        b'A'..=b'F' => Some(10 + (c - b'A')),
        _ => None,
    }
}

/// Encode `bytes` into lowercase hex string (no `0x` prefix).
#[inline]
pub fn to_hex_lower(bytes: &[u8]) -> String {
    let mut out = vec![0u8; bytes.len() * 2];
    encode_into(bytes, &mut out, false).expect("length exact");
    // SAFETY: ASCII
    unsafe { String::from_utf8_unchecked(out) }
}

/// Encode `bytes` into uppercase hex string (no `0x` prefix).
#[inline]
pub fn to_hex_upper(bytes: &[u8]) -> String {
    let mut out = vec![0u8; bytes.len() * 2];
    encode_into(bytes, &mut out, true).expect("length exact");
    unsafe { String::from_utf8_unchecked(out) }
}

/// Encode into an existing output buffer as ASCII hex (lower/upper).
/// `out.len()` must equal `2 * bytes.len()`.
#[inline]
pub fn encode_into(bytes: &[u8], out: &mut [u8], upper: bool) -> NativeResult<()> {
    if out.len() != bytes.len() * 2 {
        return Err(NativeError::InvalidArgument("encode_into: output length mismatch"));
    }
    let mut j = 0usize;
    for &b in bytes {
        out[j]   = nybble_to_hex(b >> 4, upper);
        out[j+1] = nybble_to_hex(b & 0x0F, upper);
        j += 2;
    }
    Ok(())
}

/// Strip an optional `0x`/`0X` prefix from a hex string.
#[inline]
pub fn strip_0x(s: &str) -> &str {
    if s.len() >= 2 && &s.as_bytes()[0..2] == b"0x" || s.len() >= 2 && &s.as_bytes()[0..2] == b"0X" {
        &s[2..]
    } else {
        s
    }
}

/// Return `true` if `s` contains only hex digits (optionally with `0x` prefix).
#[inline]
pub fn is_hex(s: &str) -> bool {
    let s = strip_0x(s);
    s.as_bytes().iter().all(|&c| hex_to_nybble(c).is_some())
}

/// Decode hex string (with or without `0x` prefix) into a newly-allocated Vec<u8>.
/// Rejects odd-length strings.
#[inline]
pub fn from_hex(s: &str) -> NativeResult<Vec<u8>> {
    let s = strip_0x(s);
    if s.len() % 2 != 0 {
        return Err(NativeError::InvalidArgument("from_hex: odd-length input"));
    }
    let bytes = s.as_bytes();
    let mut out = vec![0u8; bytes.len() / 2];
    decode_into(bytes, &mut out)?;
    Ok(out)
}

/// Decode ASCII hex bytes into `out`. `out.len()` must equal `bytes.len()/2`.
#[inline]
pub fn decode_into(bytes: &[u8], out: &mut [u8]) -> NativeResult<()> {
    if bytes.len() % 2 != 0 {
        return Err(NativeError::InvalidArgument("decode_into: odd-length input"));
    }
    if out.len() != bytes.len() / 2 {
        return Err(NativeError::InvalidArgument("decode_into: output length mismatch"));
    }
    let mut j = 0usize;
    for i in (0..bytes.len()).step_by(2) {
        let hi = hex_to_nybble(bytes[i]).ok_or_else(|| NativeError::InvalidArgument("decode_into: invalid hex"))?;
        let lo = hex_to_nybble(bytes[i+1]).ok_or_else(|| NativeError::InvalidArgument("decode_into: invalid hex"))?;
        out[j] = (hi << 4) | lo;
        j += 1;
    }
    Ok(())
}

/* ------------------------------ TESTS ------------------------------ */

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cast_slice_roundtrip() {
        let mut raw = [0u8; 16];
        // Ensure alignment for u32 by constructing on stack (usually aligned enough).
        let view = cast_slice_mut::<u32>(&mut raw).unwrap();
        assert_eq!(view.len(), 4);
        view.iter_mut().enumerate().for_each(|(i, v)| *v = i as u32 + 1);

        let back = cast_slice::<u32>(&raw).unwrap();
        assert_eq!(back, &[1,2,3,4]);
    }

    #[test]
    fn test_aligned_view_splits() {
        let mut buf = vec![0u8; 37];
        // Choose a T with alignment > 1
        let (head, mid, tail) = aligned_view_mut::<u32>(&mut buf);
        // Verify we didn't lose bytes
        assert_eq!(head.len() + mid.len()*4 + tail.len(), 37);
        // Mutate via typed view
        for (i, x) in mid.iter_mut().enumerate() {
            *x = (i as u32) ^ 0xA5A5_A5A5;
        }
        // Sanity: mid reflects into original buffer
        let (h2, m2, t2) = aligned_view::<u32>(&buf);
        assert_eq!(head.len(), h2.len());
        assert_eq!(tail.len(), t2.len());
        assert_eq!(mid, m2);
    }

    #[test]
    fn test_hex_lower_upper() {
        let b = b"\x00\x01\xAB\xCD\xEF";
        let l = to_hex_lower(b);
        let u = to_hex_upper(b);
        assert_eq!(l, "0001abcdef");
        assert_eq!(u, "0001ABCDEF");
        assert!(is_hex(&l));
        assert!(is_hex(&u));
        assert!(is_hex("0xdeadBEEF"));
    }

    #[test]
    fn test_hex_decode() {
        let v = from_hex("0xdeadbeef").unwrap();
        assert_eq!(v, vec![0xDE,0xAD,0xBE,0xEF]);
        assert!(from_hex("abc").is_err()); // odd length
        assert!(from_hex("zz").is_err());  // invalid
    }

    #[test]
    fn test_align_math() {
        let a = 0x1003usize;
        let off = align_offset(a, 8);
        assert_eq!(off, 5);
        assert_eq!(a + off, 0x1008);

        let padded = pad_to_end_align(0x1000, 3, 8);
        assert_eq!(padded, 8); // end becomes 0x1008
    }
}
