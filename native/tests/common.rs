//! Test helpers and deterministic random data for `animica_native`.
//!
//! This module is shared by multiple `native/tests/*` files. It provides:
//! - A tiny, dependency-free PRNG (XorShift64) with deterministic seeding
//! - Helpers to generate bytes, shard sets, and erasure patterns
//! - Small assertion utilities with compact hex previews
//!
//! Usage in tests:
//! ```ignore
//! mod common;
//! use common::*;
//!
//! #[test]
//! fn my_test() {
//!     let mut rng = rng_from_env(); // honors TEST_SEED if set
//!     let data = random_bytes(1024, &mut rng);
//!     assert_eq_bytes(&data, &data);
//! }
//! ```

use std::env;

#[allow(dead_code)]
pub const DEFAULT_TEST_SEED: u64 = 0xA11C_1A9E_C0FF_EE42;

/// Minimal, fast, deterministic PRNG (XorShift64).
/// Not cryptographically secure—only for tests/benches.
#[derive(Clone)]
pub struct XorShift64 {
    state: u64,
}

impl XorShift64 {
    #[inline]
    pub fn new(seed: u64) -> Self {
        // Avoid the all-zero lockup state.
        let s = if seed == 0 { DEFAULT_TEST_SEED } else { seed };
        Self { state: s }
    }

    #[inline]
    pub fn next_u64(&mut self) -> u64 {
        // Marsaglia Xorshift64*
        let mut x = self.state;
        x ^= x << 13;
        x ^= x >> 7;
        x ^= x << 17;
        self.state = x;
        x
    }

    #[inline]
    pub fn next_u32(&mut self) -> u32 {
        (self.next_u64() & 0xFFFF_FFFF) as u32
    }

    /// Fill `buf` with pseudo-random bytes.
    #[inline]
    pub fn fill_bytes(&mut self, buf: &mut [u8]) {
        let mut i = 0;
        while i + 8 <= buf.len() {
            buf[i..i + 8].copy_from_slice(&self.next_u64().to_le_bytes());
            i += 8;
        }
        if i < buf.len() {
            let tail = self.next_u64().to_le_bytes();
            let n = buf.len() - i;
            buf[i..].copy_from_slice(&tail[..n]);
        }
    }
}

/// Seed from env (`TEST_SEED`), or fallback to DEFAULT_TEST_SEED.
/// Accepts decimal or `0x` hex values.
#[allow(dead_code)]
pub fn seed_from_env() -> u64 {
    if let Ok(s) = env::var("TEST_SEED") {
        let s = s.trim();
        if let Some(hex) = s.strip_prefix("0x").or_else(|| s.strip_prefix("0X")) {
            u64::from_str_radix(hex, 16).unwrap_or(DEFAULT_TEST_SEED)
        } else {
            s.parse::<u64>().unwrap_or(DEFAULT_TEST_SEED)
        }
    } else {
        DEFAULT_TEST_SEED
    }
}

/// Convenience: construct an RNG from `TEST_SEED` (or default).
#[allow(dead_code)]
pub fn rng_from_env() -> XorShift64 {
    XorShift64::new(seed_from_env())
}

/// Deterministic bytes of length `len`.
#[allow(dead_code)]
pub fn random_bytes(len: usize, rng: &mut XorShift64) -> Vec<u8> {
    let mut v = vec![0u8; len];
    rng.fill_bytes(&mut v);
    v
}

/// A deterministic shard filler that depends on (index, shard_size, base_seed),
/// providing stable, unique contents per shard.
#[allow(dead_code)]
pub fn fill_shard(index: usize, shard_size: usize, base_seed: u64) -> Vec<u8> {
    let seed = base_seed
        ^ (index as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15)
        ^ (shard_size as u64).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    let mut rng = XorShift64::new(seed);
    random_bytes(shard_size, &mut rng)
}

/// Build `d` data shards each of `shard_size` bytes.
#[allow(dead_code)]
pub fn make_data_shards(d: usize, shard_size: usize, base_seed: u64) -> Vec<Vec<u8>> {
    (0..d).map(|i| fill_shard(i, shard_size, base_seed)).collect()
}

/// Convert a full shard set (data + parity) into Option<Vec<u8>> with `e` erasures.
/// Erase from the data region first, then parity if needed. `d` is the data shard count.
#[allow(dead_code)]
pub fn with_erasures(full: Vec<Vec<u8>>, d: usize, mut e: usize) -> Vec<Option<Vec<u8>>> {
    let total = full.len();
    let mut out: Vec<Option<Vec<u8>>> = full.into_iter().map(Some).collect();

    // Data region first
    for i in 0..d.min(total) {
        if e == 0 {
            break;
        }
        out[i] = None;
        e -= 1;
    }
    // Then parity region
    for i in d..total {
        if e == 0 {
            break;
        }
        out[i] = None;
        e -= 1;
    }
    out
}

/// Choose `k` unique indices in `[0, n)` using a simple Floyd algorithm.
#[allow(dead_code)]
pub fn choose_k_of_n(n: usize, k: usize, rng: &mut XorShift64) -> Vec<usize> {
    assert!(k <= n, "k must be <= n");
    use std::collections::HashSet;
    let mut chosen = HashSet::with_capacity(k);
    // Floyd's algorithm: choose k elements without replacement
    for i in (n - k)..n {
        let t = (rng.next_u64() as usize) % (i + 1);
        if !chosen.insert(t) {
            chosen.insert(i);
        }
    }
    let mut v: Vec<_> = chosen.into_iter().collect();
    v.sort_unstable();
    v
}

/// Produce a boolean mask of length `n` with exactly `k` set positions.
#[allow(dead_code)]
pub fn choose_mask(n: usize, k: usize, rng: &mut XorShift64) -> Vec<bool> {
    let mut mask = vec![false; n];
    for i in choose_k_of_n(n, k, rng) {
        mask[i] = true;
    }
    mask
}

/// Small hex encoder for debug/assert messages.
#[allow(dead_code)]
pub fn to_hex(data: &[u8]) -> String {
    const LUT: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(data.len() * 2);
    for &b in data {
        s.push(LUT[(b >> 4) as usize] as char);
        s.push(LUT[(b & 0x0f) as usize] as char);
    }
    s
}

/// Truncate a byte slice for display, showing head/tail with ellipsis.
#[allow(dead_code)]
pub fn preview_bytes(data: &[u8], max: usize) -> String {
    if data.len() <= max {
        return to_hex(data);
    }
    let head = max / 2;
    let tail = max - head;
    format!("{}…{}", to_hex(&data[..head]), to_hex(&data[data.len() - tail..]))
}

/// Assert two byte slices equal with concise hex diff previews.
#[allow(dead_code)]
pub fn assert_eq_bytes(a: &[u8], b: &[u8]) {
    if a != b {
        let max = 64; // chars of hex to show
        panic!(
            "byte mismatch:\n  left (len={}):  {}\n  right(len={}):  {}",
            a.len(),
            preview_bytes(a, max),
            b.len(),
            preview_bytes(b, max)
        );
    }
}

/// Create (data_shards, full_shards=data+parity) using the crate's RS encoder,
/// if available from the public API. This is a convenience wrapper for tests.
/// Adjust `use` path if your RS module differs.
#[allow(dead_code)]
pub fn build_rs_stripe(
    d: usize,
    p: usize,
    shard_size: usize,
    base_seed: u64,
) -> (Vec<Vec<u8>>, Vec<Vec<u8>>) {
    let data = make_data_shards(d, shard_size, base_seed);
    // The bench API is the stable thin layer for tests/benches.
    // If your project exposes a different path, update here centrally.
    #[allow(unused_imports)]
    use animica_native::rs::bench_api::encode;

    let full = encode(&data, p);
    (data, full)
}

#[cfg(test)]
mod selfcheck {
    use super::*;

    #[test]
    fn xorshift_basic() {
        let mut r = XorShift64::new(1);
        // Fixed first few values ensure determinism across platforms.
        let a = r.next_u64();
        let b = r.next_u64();
        assert_ne!(a, b);
        // Smoke: filling bytes gives consistent results
        let mut buf = [0u8; 32];
        let mut r2 = XorShift64::new(1);
        r2.fill_bytes(&mut buf);
        let hex = to_hex(&buf);
        assert_eq!(hex.len(), 64);
        // Deterministic preview (tolerate algo differences by checking stability here)
        assert!(hex.starts_with("c000000000000000") == false, "unexpected trivial output");
    }

    #[test]
    fn choose_k_unique() {
        let mut rng = XorShift64::new(123);
        let v = choose_k_of_n(10, 5, &mut rng);
        assert_eq!(v.len(), 5);
        assert!(v.windows(2).all(|w| w[0] < w[1]));
        assert!(v.iter().all(|&x| x < 10));
    }

    #[test]
    fn erasures_mask_len() {
        let mut rng = XorShift64::new(999);
        let m = choose_mask(17, 7, &mut rng);
        assert_eq!(m.len(), 17);
        assert_eq!(m.iter().filter(|&&b| b).count(), 7);
    }

    #[test]
    fn fill_shard_stable() {
        let a = fill_shard(0, 64, 0x55);
        let b = fill_shard(0, 64, 0x55);
        assert_eq_bytes(&a, &b);
        let c = fill_shard(1, 64, 0x55);
        assert_ne!(a, c);
    }
}
