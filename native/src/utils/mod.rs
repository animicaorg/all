//! Misc low-level helpers used across `animica_native`.
//!
//! Goals:
//! - Zero-alloc utilities for tight loops (hashing, encoding, DA RS, etc.).
//! - Safe(ish) wrappers around common `unsafe` slice casts.
//! - Constant-time primitives for sensitive comparisons.
//! - Tiny runtime CPU feature probe with caching.
//! - Optional parallel helpers (when the `rayon` feature is enabled).
//!
//! This module is intentionally dependency-light and `no_std`-friendly in style
//! (though the crate itself uses `std`). Keep APIs stable—downstream crates and
//! the Python bindings may rely on these names and semantics.

use core::{mem, ptr, slice, sync::atomic::{compiler_fence, Ordering}};
use std::sync::OnceLock;

use crate::error::{NativeError, NativeResult};

/// Round `len` up to the next multiple of `alignment` (must be > 0).
#[inline]
pub fn round_up_to(len: usize, alignment: usize) -> usize {
    debug_assert!(alignment > 0);
    if alignment == 0 { return len; }
    (len + alignment - 1) / alignment * alignment
}

/// Alias for clarity when padding buffers to a specific alignment boundary.
#[inline]
pub fn align_len(len: usize, alignment: usize) -> usize {
    round_up_to(len, alignment)
}

/// Return whether the pointer is aligned to `alignment` bytes.
#[inline]
pub fn is_aligned(ptr: *const u8, alignment: usize) -> bool {
    (ptr as usize) & (alignment - 1) == 0
}

/// Return `true` if two memory ranges do not overlap.
///
/// # Safety
/// This only checks addresses; it does not validate the pointers are valid.
#[inline]
pub unsafe fn non_overlapping(a: *const u8, a_len: usize, b: *const u8, b_len: usize) -> bool {
    let a_start = a as usize;
    let a_end = a_start.saturating_add(a_len);
    let b_start = b as usize;
    let b_end = b_start.saturating_add(b_len);
    a_end <= b_start || b_end <= a_start
}

/// Convert a typed slice to a byte slice without copying.
///
/// # Safety
/// This is safe if `T` has no padding you care about exposing. For plain-old-data
/// types used internally (e.g., fixed-layout structs) this is acceptable.
#[inline]
pub fn as_u8_slice<T>(vals: &[T]) -> &[u8] {
    let len = vals.len() * mem::size_of::<T>();
    unsafe { slice::from_raw_parts(vals.as_ptr() as *const u8, len) }
}

/// Mutable variant of [`as_u8_slice`].
#[inline]
pub fn as_u8_slice_mut<T>(vals: &mut [T]) -> &mut [u8] {
    let len = vals.len() * mem::size_of::<T>();
    unsafe { slice::from_raw_parts_mut(vals.as_mut_ptr() as *mut u8, len) }
}

/// Best-effort secure zero for a byte slice.
///
/// Uses `write_volatile` and a compiler fence to reduce the chance of elision.
#[inline]
pub fn memzero(bytes: &mut [u8]) {
    for b in bytes {
        unsafe { ptr::write_volatile(b, 0u8); }
    }
    compiler_fence(Ordering::SeqCst);
}

/// Constant-time comparison of two byte slices.
///
/// Returns `false` if lengths differ. Otherwise performs a branchless XOR/OR fold.
#[inline]
pub fn ct_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff: u8 = 0;
    for i in 0..a.len() {
        // Branchless: accumulates any difference.
        diff |= a[i] ^ b[i];
    }
    diff == 0
}

/// XOR `src` into `dst` in place. Lengths must match.
#[inline]
pub fn xor_in_place(dst: &mut [u8], src: &[u8]) -> NativeResult<()> {
    if dst.len() != src.len() {
        return Err(NativeError::InvalidArgument("xor_in_place length mismatch"));
    }
    // Optional SIMD path (x86_64 AVX2) for large buffers.
    #[cfg(all(target_arch = "x86_64"))]
    {
        if cpu_features().avx2 && dst.len() >= 64 {
            // SAFETY: We fall back to scalar path if alignment is poor; we only
            // use wide ops on aligned chunks.
            unsafe { xor_in_place_avx2(dst, src); return Ok(()); }
        }
    }
    // Portable scalar path.
    for (d, s) in dst.iter_mut().zip(src.iter()) {
        *d ^= *s;
    }
    Ok(())
}

/// XOR `a` and `b` into `dst`. All lengths must match.
#[inline]
pub fn xor3_into(dst: &mut [u8], a: &[u8], b: &[u8]) -> NativeResult<()> {
    if dst.len() != a.len() || a.len() != b.len() {
        return Err(NativeError::InvalidArgument("xor3_into length mismatch"));
    }
    for i in 0..dst.len() {
        dst[i] = a[i] ^ b[i];
    }
    Ok(())
}

#[cfg(all(target_arch = "x86_64"))]
unsafe fn xor_in_place_avx2(dst: &mut [u8], src: &[u8]) {
    use core::arch::x86_64::*;
    let mut i = 0usize;
    let n = dst.len();
    const W: usize = 32; // 256-bit
    // Main loop on 32B lanes.
    while i + W <= n {
        let d_ptr = dst.as_mut_ptr().add(i) as *mut __m256i;
        let s_ptr = src.as_ptr().add(i) as *const __m256i;
        // Unaligned loads/stores are fine with AVX2, may cost a cycle if crossing cache lines.
        let dv = _mm256_loadu_si256(d_ptr);
        let sv = _mm256_loadu_si256(s_ptr);
        let x = _mm256_xor_si256(dv, sv);
        _mm256_storeu_si256(d_ptr, x);
        i += W;
    }
    // Tail
    while i < n {
        *dst.get_unchecked_mut(i) ^= *src.get_unchecked(i);
        i += 1;
    }
}

/// Checked version of `split_at_mut` that returns `NativeError` instead of panicking.
#[inline]
pub fn split_at_checked<T>(buf: &mut [T], mid: usize) -> NativeResult<(&mut [T], &mut [T])> {
    if mid > buf.len() {
        return Err(NativeError::InvalidArgument("split index out of bounds"));
    }
    Ok(buf.split_at_mut(mid))
}

/// Verify two slices have equal length; return `NativeError` otherwise.
#[inline]
pub fn check_len_eq(a: usize, b: usize, context: &'static str) -> NativeResult<()> {
    if a != b {
        return Err(NativeError::InvalidArgument(context));
    }
    Ok(())
}

/// CPU feature bits cached at first use.
#[derive(Debug, Clone, Copy)]
pub struct CpuFeatures {
    /// x86_64: Advanced Vector Extensions 2 (256-bit integer ops).
    pub avx2: bool,
    /// x86_64: Intel SHA Extensions (SHA-NI). (Note: not widely used here.)
    pub sha_ni: bool,
    /// aarch64: NEON SIMD.
    pub neon: bool,
    /// aarch64: SHA2 crypto extension present.
    pub sha2: bool,
}

static CPU_FEATS: OnceLock<CpuFeatures> = OnceLock::new();

/// Probe and cache CPU features (lightweight, thread-safe).
#[inline]
pub fn cpu_features() -> &'static CpuFeatures {
    CPU_FEATS.get_or_init(|| {
        // Defaults
        let mut feats = CpuFeatures { avx2: false, sha_ni: false, neon: false, sha2: false };

        // x86_64
        #[cfg(target_arch = "x86_64")]
        {
            feats.avx2 = std::is_x86_feature_detected!("avx2");
            // "sha" for SHA-NI; returns true on CPUs with the SHA extensions.
            feats.sha_ni = std::is_x86_feature_detected!("sha");
        }

        // aarch64
        #[cfg(target_arch = "aarch64")]
        {
            feats.neon = std::is_aarch64_feature_detected!("neon");
            // Rust exposes "sha2" for ARMv8 crypto extension (SHA1/SHA2).
            feats.sha2 = std::is_aarch64_feature_detected!("sha2");
        }

        feats
    })
}

/// Heuristic: return `true` if SIMD lanes are likely beneficial.
#[inline]
pub fn prefer_simd() -> bool {
    let f = cpu_features();
    f.avx2 || f.neon
}

/// Apply `f` over mutable chunks of `buf` of size `chunk_size`.
/// If the `rayon` feature is enabled, this parallelizes across chunks.
///
/// - `chunk_size == 0` is treated as 1.
/// - The final chunk may be shorter.
pub fn for_each_chunk_mut<T, F>(buf: &mut [T], mut chunk_size: usize, f: F)
where
    T: Send,
    F: Fn(&mut [T]) + Send + Sync,
{
    if buf.is_empty() {
        return;
    }
    if chunk_size == 0 {
        chunk_size = 1;
    }

    #[cfg(feature = "rayon")]
    {
        use rayon::prelude::*;
        buf.par_chunks_mut(chunk_size).for_each(|c| f(c));
        return;
    }

    // Fallback: sequential
    for c in buf.chunks_mut(chunk_size) {
        f(c);
    }
}

/// Copy `src` into `dst`, returning an error if lengths differ.
/// Safer than `copy_from_slice` when lengths are derived from external inputs.
#[inline]
pub fn copy_checked(dst: &mut [u8], src: &[u8]) -> NativeResult<()> {
    if dst.len() != src.len() {
        return Err(NativeError::InvalidArgument("copy_checked length mismatch"));
    }
    dst.copy_from_slice(src);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_round_up_to() {
        assert_eq!(round_up_to(0, 8), 0);
        assert_eq!(round_up_to(1, 8), 8);
        assert_eq!(round_up_to(8, 8), 8);
        assert_eq!(round_up_to(9, 8), 16);
    }

    #[test]
    fn test_ct_eq() {
        assert!(ct_eq(b"abc", b"abc"));
        assert!(!ct_eq(b"abc", b"abC"));
        assert!(!ct_eq(b"abc", b"abcd"));
    }

    #[test]
    fn test_memzero() {
        let mut v = [1u8, 2, 3, 4];
        memzero(&mut v);
        assert_eq!(&v, &[0, 0, 0, 0]);
    }

    #[test]
    fn test_xor() {
        let mut d = [0xAAu8, 0x00, 0xFF];
        xor_in_place(&mut d, &[0x55, 0xFF, 0x0F]).unwrap();
        assert_eq!(&d, &[0xFF, 0xFF, 0xF0]);
    }

    #[test]
    fn test_copy_checked() {
        let mut d = [0u8; 3];
        assert!(copy_checked(&mut d, &[1,2,3]).is_ok());
        assert_eq!(d, [1,2,3]);
        assert!(copy_checked(&mut d, &[1,2]).is_err());
    }

    #[test]
    fn test_cpu_features_singleton() {
        let a = cpu_features() as *const _;
        let b = cpu_features() as *const _;
        assert_eq!(a, b, "OnceLock must cache the same pointer");
    }
}
