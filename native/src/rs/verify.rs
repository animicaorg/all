//! Reed–Solomon verification helpers: parity checking and lightweight
//! "syndrome" style diagnostics.
//!
//! This module provides two main utilities built on top of the layout planner
//! and the underlying RS codec:
//!
//! - [`parity_check`] — returns `true` iff the provided shards form a valid
//!   codeword (i.e., parity matches data for RS(k+m, k)).
//! - [`parity_mismatch_positions`] — recomputes parity from the provided data
//!   shards and reports which **parity shard indices** (in the combined
//!   `[0..k+m)` space) do not match the expected bytes. This acts like a very
//!   lightweight "syndrome" that points to inconsistent parity without doing a
//!   full error/erasure location solve.
//!
//! Notes:
//! - We intentionally avoid mutating the caller's buffers; encoding work is
//!   done on a temporary copy for comparison.
//! - This does **not** attempt to identify corrupt data shards (only parity
//!   mismatches). If both data and parity are corrupted but consistent, a
//!   naive recompute comparison could pass; for robust detection we also use
//!   the codec's `verify` when available.

use core::fmt;

use crate::rs::layout::{Layout, LayoutError};
use reed_solomon_erasure::galois_8::ReedSolomon;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VerifyError {
    InvalidArg(&'static str),
    Layout(LayoutError),
    CodecInit,
    CodecOp,
}

impl From<LayoutError> for VerifyError {
    fn from(e: LayoutError) -> Self {
        VerifyError::Layout(e)
    }
}

impl fmt::Display for VerifyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        use VerifyError::*;
        match self {
            InvalidArg(s) => write!(f, "invalid argument: {s}"),
            Layout(le) => write!(f, "layout error: {le}"),
            CodecInit => write!(f, "failed to initialize reed-solomon codec"),
            CodecOp => write!(f, "reed-solomon operation failed"),
        }
    }
}
impl std::error::Error for VerifyError {}

/// Return `true` iff `shards` passes RS parity verification for `(k, m)`.
///
/// This first validates shapes via `layout`, then calls the codec's `verify`.
/// When `verify` returns an error (shouldn't in normal conditions), we fall
/// back to recomputing parity and comparing bytes.
pub fn parity_check(layout: &Layout, shards: &[Vec<u8>]) -> Result<bool, VerifyError> {
    layout.validate_shards(shards)?;
    if layout.data_shards + layout.parity_shards != shards.len() {
        return Err(VerifyError::InvalidArg("shards.len() must be k+m"));
    }

    let rs = ReedSolomon::new(layout.data_shards, layout.parity_shards)
        .map_err(|_| VerifyError::CodecInit)?;

    // reed-solomon-erasure::verify takes &[Option<Shard>]; provide all shards.
    let opt: Vec<Option<Vec<u8>>> = shards.iter().cloned().map(Some).collect();
    match rs.verify(&opt) {
        Ok(ok) => Ok(ok),
        Err(_) => {
            // Fallback: recompute parity and compare.
            let mismatches = parity_mismatch_positions(layout, shards)?;
            Ok(mismatches.is_empty())
        }
    }
}

/// Compare existing parity shards to parity re-encoded from provided data,
/// returning the absolute indices `[k .. k+m)` that mismatch.
///
/// This does **not** mutate the caller's buffers.
pub fn parity_mismatch_positions(layout: &Layout, shards: &[Vec<u8>]) -> Result<Vec<usize>, VerifyError> {
    layout.validate_shards(shards)?;
    let k = layout.data_shards;
    let m = layout.parity_shards;
    if shards.len() != k + m {
        return Err(VerifyError::InvalidArg("shards.len() must be k+m"));
    }

    // Prepare temporary shards: clone data, allocate zeroed parity slots.
    let mut tmp: Vec<Vec<u8>> = Vec::with_capacity(k + m);
    for s in &shards[..k] {
        tmp.push(s.clone());
    }
    for _ in 0..m {
        tmp.push(vec![0u8; layout.shard_len]);
    }

    let rs = ReedSolomon::new(k, m).map_err(|_| VerifyError::CodecInit)?;
    rs.encode(&mut tmp).map_err(|_| VerifyError::CodecOp)?;

    // Compare recomputed parity (tmp[k..]) with provided parity (shards[k..]).
    let mut bad = Vec::new();
    for (idx, (want, have)) in tmp[k..].iter().zip(&shards[k..]).enumerate() {
        if want.len() != have.len() || !consttime_eq(want, have) {
            bad.push(k + idx);
        }
    }
    Ok(bad)
}

/// A minimal constant-time equality for same-length slices (early length check
/// is not constant-time; the byte-wise compare is).
#[inline]
fn consttime_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for i in 0..a.len() {
        diff |= a[i] ^ b[i];
    }
    diff == 0
}

/* --------------------------------- Tests -------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use rand::{rngs::StdRng, Rng, SeedableRng};

    fn make_random_shards(rng: &mut StdRng, payload_len: usize, k: usize, m: usize) -> (Layout, Vec<Vec<u8>>) {
        let layout = Layout::with_default_align(payload_len, k, m).unwrap();
        let mut payload = vec![0u8; payload_len];
        rng.fill(&mut payload[..]);

        let mut shards = layout.shardify(&payload).unwrap();

        // Fill parity via RS so we start from a valid codeword.
        let rs = ReedSolomon::new(k, m).unwrap();
        rs.encode(&mut shards).unwrap();
        (layout, shards)
    }

    #[test]
    fn parity_check_valid() {
        let mut rng = StdRng::seed_from_u64(1234);
        for _ in 0..10 {
            let k = 4 + (rng.gen::<usize>() % 5); // 4..8
            let m = 2 + (rng.gen::<usize>() % 4); // 2..5
            let len = rng.gen_range(1_000..10_000);
            let (layout, shards) = make_random_shards(&mut rng, len, k, m);

            assert!(parity_check(&layout, &shards).unwrap());
            assert!(parity_mismatch_positions(&layout, &shards).unwrap().is_empty());
        }
    }

    #[test]
    fn parity_check_detects_parity_corruption() {
        let mut rng = StdRng::seed_from_u64(999);
        let k = 5;
        let m = 3;
        let (layout, mut shards) = make_random_shards(&mut rng, 8192, k, m);

        // Flip some bytes in parity shards.
        shards[k + 0][10] ^= 0xAA;
        shards[k + 2][200] ^= 0x55;

        let ok = parity_check(&layout, &shards).unwrap();
        assert!(!ok);

        let bad = parity_mismatch_positions(&layout, &shards).unwrap();
        assert!(bad.contains(&(k + 0)));
        assert!(bad.contains(&(k + 2)));
        assert_eq!(bad.len(), 2);
    }

    #[test]
    fn parity_check_detects_data_corruption_via_mismatched_parity() {
        let mut rng = StdRng::seed_from_u64(42);
        let k = 6;
        let m = 3;
        let (layout, mut shards) = make_random_shards(&mut rng, 12_345, k, m);

        // Corrupt a data shard byte; parity now mismatches data.
        shards[2][123] ^= 0xFF;

        let ok = parity_check(&layout, &shards).unwrap();
        assert!(!ok);

        let bad = parity_mismatch_positions(&layout, &shards).unwrap();
        // We expect at least one parity shard to disagree after corruption.
        assert!(!bad.is_empty());
        for idx in bad {
            assert!(idx >= k && idx < k + m);
        }
    }
}
