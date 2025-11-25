//! Thin, allocation-light helpers used by benches/tests to exercise hashing
//! backends with consistent semantics (with/without DS prefix), plus simple
//! bulk and repeat drivers. Keep this file dependency-light to avoid skewing
//! perf results.
//!
//! ## Provided helpers
//! - `Algo` — select {Blake3, Keccak256, Sha256}
//! - `oneshot(algo, data)` — raw digest of a single buffer
//! - `oneshot_ds(algo, tag, data)` — same, but with a domain-separation prefix
//! - `repeat{,_ds}(algo, data, iters)` — hash the (mutated) buffer `iters` times
//! - `bulk(algo, inputs)` — hash many independent messages (parallel if `rayon`)
//! - `gen_input(len, seed)` — deterministic pseudo-random bytes for fixtures
//!
//! Notes:
//! - All digests are 32 bytes. Blake3 uses its 256-bit default output.
//! - DS prelude matches the convention used across Animica:
//!   `b"animica.ds." || ALGO || b":" || tag.context() || 0x00`
//!   where `ALGO ∈ {"blake3","keccak256","sha256"}`.

use super::{Digest32, DsTag};

/// Hash algorithm choices exposed to benches.
#[derive(Clone, Copy, Debug)]
pub enum Algo {
    Blake3,
    Keccak256,
    Sha256,
}

impl Algo {
    #[inline]
    fn label(self) -> &'static str {
        match self {
            Algo::Blake3 => "blake3",
            Algo::Keccak256 => "keccak256",
            Algo::Sha256 => "sha256",
        }
    }
}

/// Produce the Animica DS prefix for a given algorithm + tag.
///
/// Bytes layout:
/// `b"animica.ds." || algo || b":" || tag.context().as_bytes() || [0x00]`
#[inline]
pub fn ds_prefix(algo: Algo, tag: DsTag) -> Vec<u8> {
    let mut out = Vec::with_capacity(12 + algo.label().len() + tag.context().len() + 1);
    out.extend_from_slice(b"animica.ds.");
    out.extend_from_slice(algo.label().as_bytes());
    out.push(b':');
    out.extend_from_slice(tag.context().as_bytes());
    out.push(0);
    out
}

/// One-shot digest of a single buffer (no DS).
#[inline]
pub fn oneshot(algo: Algo, data: &[u8]) -> Digest32 {
    match algo {
        Algo::Blake3 => crate::hash::blake3::blake3(data),
        Algo::Keccak256 => crate::hash::keccak::keccak256(data),
        Algo::Sha256 => crate::hash::sha256::sha256(data),
    }
}

/// One-shot digest with DS prefix absorbed before `data`.
#[inline]
pub fn oneshot_ds(algo: Algo, tag: DsTag, data: &[u8]) -> Digest32 {
    let pre = ds_prefix(algo, tag);
    // Avoid extra allocation for small inputs by hashing in two updates when possible.
    match algo {
        Algo::Blake3 => {
            crate::hash::blake3::blake3_many([pre.as_slice(), data].into_iter())
        }
        Algo::Keccak256 => {
            crate::hash::keccak::keccak256_many([pre.as_slice(), data].into_iter())
        }
        Algo::Sha256 => {
            crate::hash::sha256::sha256_many([pre.as_slice(), data].into_iter())
        }
    }
}

/// Repeat hashing `iters` times, mutating the buffer between rounds with the
/// previous digest to defeat trivial DCE/constant-folding in microbenches.
/// Returns the final digest (useful for golden checks).
pub fn repeat(algo: Algo, mut buf: Vec<u8>, iters: u64) -> Digest32 {
    let mut last = [0u8; 32];
    let n = buf.len().min(32);
    for _ in 0..iters {
        // Mix the previous digest into the head of the buffer.
        for i in 0..n {
            buf[i] ^= last[i];
        }
        last = oneshot(algo, &buf);
    }
    last
}

/// Same as `repeat`, but absorbs a DS prefix each iteration.
pub fn repeat_ds(algo: Algo, tag: DsTag, mut buf: Vec<u8>, iters: u64) -> Digest32 {
    let mut last = [0u8; 32];
    let n = buf.len().min(32);
    for _ in 0..iters {
        for i in 0..n {
            buf[i] ^= last[i];
        }
        last = oneshot_ds(algo, tag, &buf);
    }
    last
}

/// Hash many independent messages. When the `rayon` feature is enabled, this
/// will parallelize across inputs; otherwise, it runs serially.
pub fn bulk<'a>(algo: Algo, inputs: impl IntoIterator<Item = &'a [u8]>) -> Vec<Digest32> {
    let v: Vec<&[u8]> = inputs.into_iter().collect();
    #[cfg(feature = "rayon")]
    {
        use rayon::prelude::*;
        return v.par_iter().map(|m| oneshot(algo, m)).collect();
    }
    #[cfg(not(feature = "rayon"))]
    {
        return v.iter().map(|m| oneshot(algo, m)).collect();
    }
}

/// Deterministic pseudo-random byte generator (XorShift64) for fixtures.
pub fn gen_input(len: usize, seed: u64) -> Vec<u8> {
    struct XS(u64);
    impl XS {
        #[inline] fn next(&mut self) -> u64 {
            let mut x = self.0;
            x ^= x << 13;
            x ^= x >> 7;
            x ^= x << 17;
            self.0 = x;
            x
        }
    }
    let mut rng = XS(seed ^ 0x9E3779B97F4A7C15);
    let mut out = vec![0u8; len];
    let mut i = 0;
    while i + 8 <= len {
        out[i..i + 8].copy_from_slice(&rng.next().to_le_bytes());
        i += 8;
    }
    if i < len {
        out[i..].copy_from_slice(&rng.next().to_le_bytes()[..(len - i)]);
    }
    out
}

/* ---------------------------------- Tests ----------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hash::DsTag as Tag;

    #[test]
    fn ds_changes_digest_for_all_algos() {
        for algo in [Algo::Blake3, Algo::Keccak256, Algo::Sha256] {
            let plain = oneshot(algo, b"hello world");
            let tagged = oneshot_ds(algo, Tag::Generic, b"hello world");
            assert_ne!(plain, tagged, "DS must affect digest for {:?}", algo);
        }
    }

    #[test]
    fn repeat_produces_stable_final_digest() {
        let buf = gen_input(1 << 16, 0x1234_5678);
        let a = repeat(Algo::Sha256, buf.clone(), 8);
        let b = repeat(Algo::Sha256, buf, 8);
        assert_eq!(a, b, "repeat should be deterministic");
    }

    #[test]
    fn bulk_roundtrips_len() {
        let ins = vec![b"a".as_ref(), b"bb".as_ref(), b"ccc".as_ref()];
        let out = bulk(Algo::Blake3, ins.iter().cloned());
        assert_eq!(out.len(), 3);
    }
}
