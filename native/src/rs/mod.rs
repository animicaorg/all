//! Reed–Solomon (RS) erasure coding — public API.
//!
//! This module exposes a thin, ergonomic wrapper around a Galois(2^8)
//! Reed–Solomon backend, providing three core operations commonly used by
//! Animica's DA layer and tests/benches:
//!
//! - `encode_in_place`: compute parity shards for a set of data shards.
//! - `reconstruct`: recover missing shards in-place given enough survivors.
//! - `verify`: check that the parity matches the data.
//!
//! ## Design notes
//! - The API is **backend-agnostic**; by default we use the `reed-solomon-erasure`
//!   pure-Rust backend. If the crate is compiled with the `isal` feature, we
//!   still fall back to the same backend unless a faster one is wired in.
//!   (This keeps the public API stable while allowing future drop-in backends.)
//! - Shards are **flat byte slices** of equal length. For convenience, the
//!   encode function will `resize` parity shards to the proper size.
//! - All functions operate **in place** to avoid extra allocations/copies.
//!
//! ## Safety & constraints
//! - All shards must have identical lengths (except parity shards that may be
//!   empty prior to `encode_in_place`, which will be resized).
//! - You must supply exactly `k + m` shards where `k=data_shards` and
//!   `m=parity_shards`. The first `k` are data, the last `m` are parity.
//! - For `reconstruct`, supply a `Vec<Option<Vec<u8>>>` where `None` marks a
//!   missing shard. At least `k` shards must be `Some` to recover the rest.

use core::fmt;

/// Parameters for an RS( k + m, k ) code over GF(2^8).
#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub struct RsParams {
    /// Number of data shards (k).
    pub data_shards: usize,
    /// Number of parity shards (m).
    pub parity_shards: usize,
}

impl RsParams {
    /// Total shard count (k + m).
    #[inline]
    pub const fn total(&self) -> usize {
        self.data_shards + self.parity_shards
    }

    /// Basic validity checks.
    #[inline]
    pub fn validate(&self) -> Result<(), RsError> {
        if self.data_shards == 0 {
            return Err(RsError::InvalidArg("data_shards must be > 0"));
        }
        if self.parity_shards == 0 {
            return Err(RsError::InvalidArg("parity_shards must be > 0"));
        }
        Ok(())
    }
}

/// Minimal error type local to the RS module (keeps us dependency-light).
#[derive(Debug, Clone)]
pub enum RsError {
    InvalidArg(&'static str),
    ShardLenMismatch,
    NotEnoughShards,     // fewer than k available for reconstruct
    BackendError(String) // wrapped backend error
}

impl fmt::Display for RsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        use RsError::*;
        match self {
            InvalidArg(s) => write!(f, "invalid argument: {s}"),
            ShardLenMismatch => write!(f, "all shards must have identical length"),
            NotEnoughShards => write!(f, "not enough shards to reconstruct"),
            BackendError(e) => write!(f, "backend error: {e}"),
        }
    }
}

impl std::error::Error for RsError {}

#[inline]
fn ensure_all_equal_len<'a, T: AsRef<[u8]> + 'a>(shards: impl IntoIterator<Item=&'a T>) -> Result<usize, RsError> {
    let mut it = shards.into_iter();
    let Some(first) = it.next() else { return Ok(0) };
    let len0 = first.as_ref().len();
    for s in it {
        if s.as_ref().len() != len0 {
            return Err(RsError::ShardLenMismatch);
        }
    }
    Ok(len0)
}

/* ------------------------------ Backend glue ----------------------------- */

mod backend {
    // Default (and "isal" placeholder) backend: reed-solomon-erasure over GF(2^8).
    pub use reed_solomon_erasure::galois_8 as gf256;
    pub type Rs = gf256::ReedSolomon;
}

fn build_rs(params: RsParams) -> Result<backend::Rs, RsError> {
    params.validate()?;
    backend::Rs::new(params.data_shards, params.parity_shards)
        .map_err(|e| RsError::BackendError(format!("{e}")))
}

/* --------------------------------- API ---------------------------------- */

/// Encode parity shards **in place**.
///
/// Input:
/// - `params`: RS code parameters `(k, m)`
/// - `shards`: `&mut [Vec<u8>]` of length `k + m`
///   - First `k` entries must be **data shards** (already sized and filled)
///   - Last  `m` entries are **parity shards** (may be empty; will be resized)
///
/// Returns `Ok(())` after parity shards are written.
pub fn encode_in_place(params: RsParams, shards: &mut [Vec<u8>]) -> Result<(), RsError> {
    if shards.len() != params.total() {
        return Err(RsError::InvalidArg("shards.len() must equal k + m"));
    }
    // Determine/validate shard length from data shards (must be consistent).
    let data_len = ensure_all_equal_len(&shards[..params.data_shards])?;
    // Ensure parity shards are sized to match.
    for p in &mut shards[params.data_shards..] {
        if p.len() != data_len {
            p.resize(data_len, 0);
        }
    }
    // Now the full set should be equal length.
    let _ = ensure_all_equal_len(&*shards)?;

    let rs = build_rs(params)?;
    rs.encode(shards).map_err(|e| RsError::BackendError(format!("{e}")))
}

/// Reconstruct missing shards **in place**.
///
/// - `params`: RS code params
/// - `shards`: `&mut [Option<Vec<u8>>]` of length `k + m`
///   - `Some(Vec<u8>)` for present shards; `None` for missing
///   - Present shards must have identical lengths
///   - Missing shards will be allocated & filled
///
/// Returns `Ok(())` if reconstruction succeeds.
pub fn reconstruct(params: RsParams, shards: &mut [Option<Vec<u8>>]) -> Result<(), RsError> {
    if shards.len() != params.total() {
        return Err(RsError::InvalidArg("shards.len() must equal k + m"));
    }
    // Count present shards and infer length.
    let mut present = 0usize;
    let mut len_opt = None::<usize>;
    for s in shards.iter().flatten() {
        present += 1;
        let l = s.len();
        if let Some(x) = len_opt {
            if x != l {
                return Err(RsError::ShardLenMismatch);
            }
        } else {
            len_opt = Some(l);
        }
    }
    if present < params.data_shards {
        return Err(RsError::NotEnoughShards);
    }
    let rs = build_rs(params)?;
    rs.reconstruct(shards)
        .map_err(|e| RsError::BackendError(format!("{e}")))?;
    Ok(())
}

/// Verify that data+parity shards are consistent.
///
/// Expects a full set (`k + m`) of present shards; returns `Ok(true)` if valid.
pub fn verify(params: RsParams, shards: &[Vec<u8>]) -> Result<bool, RsError> {
    if shards.len() != params.total() {
        return Err(RsError::InvalidArg("shards.len() must equal k + m"));
    }
    let _ = ensure_all_equal_len(shards)?;
    let rs = build_rs(params)?;
    let ok = rs.verify(shards)
        .map_err(|e| RsError::BackendError(format!("{e}")))?;
    Ok(ok)
}

/* --------------------------------- Tests -------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Clone)]
    struct TestRng {
        state: u64,
    }

    impl TestRng {
        fn new(seed: u64) -> Self {
            // Avoid all-zero lockup.
            let s = if seed == 0 { 0x1234_5678_9ABC_DEF0 } else { seed };
            Self { state: s }
        }

        fn next_u64(&mut self) -> u64 {
            // Simple xorshift64*
            let mut x = self.state;
            x ^= x >> 12;
            x ^= x << 25;
            x ^= x >> 27;
            self.state = x;
            x.wrapping_mul(0x2545_F491_4F6C_DD1D)
        }

        fn fill_bytes(&mut self, buf: &mut [u8]) {
            let mut i = 0;
            while i + 8 <= buf.len() {
                buf[i..i + 8].copy_from_slice(&self.next_u64().to_le_bytes());
                i += 8;
            }
            if i < buf.len() {
                let tail = self.next_u64().to_le_bytes();
                let remain = buf.len() - i;
                buf[i..].copy_from_slice(&tail[..remain]);
            }
        }
    }

    fn random_shards(k: usize, m: usize, len: usize, seed: u64) -> (RsParams, Vec<Vec<u8>>) {
        let params = RsParams { data_shards: k, parity_shards: m };
        let mut rng = TestRng::new(seed);
        let mut shards = vec![vec![0u8; len]; k + m];
        for s in &mut shards[..k] {
            rng.fill_bytes(s);
        }
        (params, shards)
    }

    #[test]
    fn encode_verify_roundtrip() {
        let (params, mut shards) = random_shards(6, 3, 1024, 42);
        encode_in_place(params, &mut shards).unwrap();
        assert!(verify(params, &shards).unwrap());
    }

    #[test]
    fn reconstruct_missing() {
        let (params, mut shards) = random_shards(5, 3, 2048, 7);
        encode_in_place(params, &mut shards).unwrap();
        assert!(verify(params, &shards).unwrap());

        // Remove 2 random shards (< m) and reconstruct.
        let mut opt: Vec<Option<Vec<u8>>> = shards.into_iter().map(Some).collect();
        opt[1] = None;
        opt[6] = None;

        reconstruct(params, &mut opt).unwrap();

        // Turn back to full shards and verify.
        let rebuilt: Vec<Vec<u8>> = opt.into_iter().map(|o| o.unwrap()).collect();
        assert!(verify(params, &rebuilt).unwrap());
    }

    #[test]
    fn not_enough_shards() {
        let (params, mut shards) = random_shards(4, 2, 1024, 99);
        encode_in_place(params, &mut shards).unwrap();

        // Remove 3 shards (>= parity + 1); fewer than k survivors.
        let mut opt: Vec<Option<Vec<u8>>> = shards.into_iter().map(Some).collect();
        opt[0] = None;
        opt[1] = None;
        opt[4] = None; // 3 erased with k=4 -> only 3 survive < k

        let err = reconstruct(params, &mut opt).unwrap_err();
        matches!(err, RsError::NotEnoughShards);
    }

    #[test]
    fn parity_resize_is_ok() {
        let (params, mut shards) = random_shards(3, 2, 777, 13);
        // Intentionally provide empty parity that's auto-resized.
        for p in &mut shards[params.data_shards..] {
            p.clear();
        }
        encode_in_place(params, &mut shards).unwrap();
        assert!(verify(params, &shards).unwrap());
        assert_eq!(shards[0].len(), 777);
        assert_eq!(shards[3].len(), 777);
        assert_eq!(shards[4].len(), 777);
    }

    #[test]
    fn mismatched_lengths_error() {
        let params = RsParams { data_shards: 2, parity_shards: 1 };
        let mut shards = vec![vec![1u8; 10], vec![2u8; 11], vec![]];
        let err = encode_in_place(params, &mut shards).unwrap_err();
        matches!(err, RsError::ShardLenMismatch);
    }
}

/// Thin "bench API" wrapper used by higher-level tests and benchmarks.
///
/// It exposes a simple `encode(&data_shards, parity_shards)` helper that
/// figures out `(k, m)` and calls the low-level `encode_in_place` routine.
/// This keeps public tests stable even if the internal RS API evolves.
pub mod bench_api {
    use super::{encode_in_place, RsParams};

    /// Encode parity shards for the given data shards.
    ///
    /// * `data_shards`   – the original data shards (length = k, k > 0).
    /// * `parity_shards` – number of parity shards (m > 0).
    ///
    /// Returns the full set of `k + m` shards (data + parity).
    pub fn encode(data_shards: &[Vec<u8>], parity_shards: usize) -> Vec<Vec<u8>> {
        let k = data_shards.len();
        assert!(k > 0, "bench_api::encode requires at least one data shard");
        assert!(
            parity_shards > 0,
            "bench_api::encode requires parity_shards > 0"
        );

        let params = RsParams {
            data_shards: k,
            parity_shards,
        };

        // Copy data shards and append empty parity shards. `encode_in_place`
        // will resize parity shards to match the data length and fill them in.
        let mut shards: Vec<Vec<u8>> = data_shards.to_vec();
        for _ in 0..parity_shards {
            shards.push(Vec::new());
        }

        encode_in_place(params, &mut shards)
            .expect("bench_api::encode: encode_in_place failed");

        shards
    }
}
