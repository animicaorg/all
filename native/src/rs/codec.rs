//! Reed–Solomon codec wrapper (GF(2^8)).
//!
//! Thin, friendly façade around the `reed-solomon-erasure` crate that
//! centralizes size checks, in-place operation ergonomics, and a stable error
//! type for the rest of the native crate. When compiled with SIMD-enabled
//! backends, the underlying GF(256) math can use vectorized tables for
//! higher throughput (subject to the backend crate/features used).
//!
//! ## Highlights
//! - **In-place** encode/reconstruct APIs (avoid copies/allocs).
//! - **Auto-size parity** shards on encode (common pitfall avoided).
//! - **Uniform errors** decoupled from backend details.
//! - **Backend-agnostic** design: defaults to `reed-solomon-erasure`
//!   (`galois_8`); other backends can be swapped behind this wrapper later.
//!
//! ## Feature notes
//! - If your build enables a SIMD-accelerated backend for
//!   `reed-solomon-erasure` (e.g., via that crate's feature flags), this
//!   wrapper will benefit automatically. At this layer we only expose a
//!   stable API and do not bind to any specific SIMD type.
//!
//! ## Usage
//! ```ignore
//! let codec = Codec::new(6, 3)?;
//! codec.encode_in_place(&mut shards)?;
//! let ok = codec.verify(&shards)?;
//! ```

use core::fmt;

pub use reed_solomon_erasure::galois_8 as gf256;
use gf256::ReedSolomon;

/// Public parameters for RS(k+m, k) codes.
#[derive(Debug, Copy, Clone, PartialEq, Eq)]
pub struct Params {
    pub data_shards: usize,   // k
    pub parity_shards: usize, // m
}

impl Params {
    #[inline]
    pub const fn total(self) -> usize {
        self.data_shards + self.parity_shards
    }
    #[inline]
    pub fn validate(self) -> Result<(), Error> {
        if self.data_shards == 0 {
            return Err(Error::InvalidArg("data_shards must be > 0"));
        }
        if self.parity_shards == 0 {
            return Err(Error::InvalidArg("parity_shards must be > 0"));
        }
        Ok(())
    }
}

/// Codec error type (backend-agnostic).
#[derive(Debug, Clone)]
pub enum Error {
    InvalidArg(&'static str),
    ShardLenMismatch,
    NotEnoughShards,
    Backend(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        use Error::*;
        match self {
            InvalidArg(s) => write!(f, "invalid argument: {s}"),
            ShardLenMismatch => write!(f, "all present shards must have identical length"),
            NotEnoughShards => write!(f, "not enough shards (need at least k to reconstruct)"),
            Backend(e) => write!(f, "backend error: {e}"),
        }
    }
}
impl std::error::Error for Error {}

/// RS codec wrapper.
#[derive(Debug)]
pub struct Codec {
    params: Params,
    inner: ReedSolomon,
}

impl Codec {
    /// Create a codec for RS(k+m, k).
    pub fn new(data_shards: usize, parity_shards: usize) -> Result<Self, Error> {
        let params = Params { data_shards, parity_shards };
        params.validate()?;
        let inner = ReedSolomon::new(data_shards, parity_shards)
            .map_err(|e| Error::Backend(e.to_string()))?;
        Ok(Self { params, inner })
    }

    /// Parameters used by this codec.
    #[inline]
    pub fn params(&self) -> Params {
        self.params
    }

    /// Encode parity shards **in place**.
    ///
    /// * `shards` must be length `k+m`. The first `k` are data; the last `m` are parity.
    /// * Parity shards may be empty prior to call; they will be resized to the data length.
    pub fn encode_in_place(&self, shards: &mut [Vec<u8>]) -> Result<(), Error> {
        let p = self.params;
        if shards.len() != p.total() {
            return Err(Error::InvalidArg("shards.len() must equal k + m"));
        }

        // Determine data shard length and check uniformity across data shards.
        let data_len = ensure_equal_len(&shards[..p.data_shards])?;

        // Resize parity shards as needed.
        for s in &mut shards[p.data_shards..] {
            if s.len() != data_len {
                s.resize(data_len, 0);
            }
        }

        // Full set should now be equal length.
        let _ = ensure_equal_len(&*shards)?;

        self.inner.encode(shards).map_err(|e| Error::Backend(e.to_string()))
    }

    /// Reconstruct missing shards **in place**.
    ///
    /// * At least `k` shards must be present.
    /// * Present shard lengths must be identical.
    /// * Missing shards will be allocated and filled to the correct length.
    pub fn reconstruct(&self, shards: &mut [Option<Vec<u8>>]) -> Result<(), Error> {
        let p = self.params;
        if shards.len() != p.total() {
            return Err(Error::InvalidArg("shards.len() must equal k + m"));
        }

        // Count present shards, infer length, and check uniformity.
        let mut present = 0usize;
        let mut shard_len: Option<usize> = None;
        for s in shards.iter().flatten() {
            present += 1;
            if let Some(len0) = shard_len {
                if s.len() != len0 {
                    return Err(Error::ShardLenMismatch);
                }
            } else {
                shard_len = Some(s.len());
            }
        }
        if present < p.data_shards {
            return Err(Error::NotEnoughShards);
        }
        let len = shard_len.unwrap_or(0);

        // Allocate None shards.
        for s in shards.iter_mut() {
            if s.is_none() {
                *s = Some(vec![0u8; len]);
            }
        }

        // Convert to Vec<Vec<u8>>, reconstruct, then write back.
        let mut owned: Vec<Vec<u8>> = shards.iter_mut().map(|o| o.take().expect("some")).collect();

        self.inner
            .reconstruct(&mut owned)
            .map_err(|e| Error::Backend(e.to_string()))?;

        for (slot, val) in shards.iter_mut().zip(owned.into_iter()) {
            *slot = Some(val);
        }
        Ok(())
    }

    /// Verify that data+parity are consistent.
    pub fn verify(&self, shards: &[Vec<u8>]) -> Result<bool, Error> {
        let p = self.params;
        if shards.len() != p.total() {
            return Err(Error::InvalidArg("shards.len() must equal k + m"));
        }
        let _ = ensure_equal_len(shards)?;
        self.inner.verify(shards).map_err(|e| Error::Backend(e.to_string()))
    }
}

/* ------------------------------- Utilities ------------------------------ */

#[inline]
fn ensure_equal_len<'a, T: AsRef<[u8]> + 'a>(shards: impl IntoIterator<Item = &'a T>) -> Result<usize, Error> {
    let mut it = shards.into_iter();
    let Some(first) = it.next() else { return Ok(0) };
    let len0 = first.as_ref().len();
    for s in it {
        if s.as_ref().len() != len0 {
            return Err(Error::ShardLenMismatch);
        }
    }
    Ok(len0)
}

/* --------------------------------- Tests -------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use rand::{rngs::StdRng, RngCore, SeedableRng};

    fn make_random_shards(k: usize, m: usize, len: usize, seed: u64) -> (Codec, Vec<Vec<u8>>) {
        let mut rng = StdRng::seed_from_u64(seed);
        let codec = Codec::new(k, m).unwrap();
        let mut shards = vec![vec![0u8; len]; k + m];
        for s in &mut shards[..k] {
            rng.fill_bytes(s);
        }
        (codec, shards)
    }

    #[test]
    fn encode_and_verify() {
        let (codec, mut shards) = make_random_shards(6, 3, 1536, 42);
        codec.encode_in_place(&mut shards).unwrap();
        assert!(codec.verify(&shards).unwrap());
    }

    #[test]
    fn reconstruct_two_missing() {
        let (codec, mut shards) = make_random_shards(5, 3, 2048, 7);
        codec.encode_in_place(&mut shards).unwrap();

        let mut opt: Vec<Option<Vec<u8>>> = shards.into_iter().map(Some).collect();
        opt[1] = None;
        opt[6] = None;

        codec.reconstruct(&mut opt).unwrap();

        let rebuilt: Vec<Vec<u8>> = opt.into_iter().map(|o| o.unwrap()).collect();
        assert!(codec.verify(&rebuilt).unwrap());
    }

    #[test]
    fn rejects_mismatched_lengths() {
        let codec = Codec::new(3, 2).unwrap();
        let mut shards = vec![vec![1u8; 10], vec![2u8; 11], vec![], vec![], vec![]];
        let err = codec.encode_in_place(&mut shards).unwrap_err();
        matches!(err, Error::ShardLenMismatch);
    }

    #[test]
    fn parity_auto_resize() {
        let (codec, mut shards) = make_random_shards(4, 2, 999, 101);
        for p in &mut shards[codec.params().data_shards..] {
            p.clear();
        }
        codec.encode_in_place(&mut shards).unwrap();
        assert!(codec.verify(&shards).unwrap());
        assert_eq!(shards[0].len(), 999);
        assert_eq!(shards[4].len(), 999);
        assert_eq!(shards[5].len(), 999);
    }

    #[test]
    fn not_enough_shards_error() {
        let (codec, mut shards) = make_random_shards(4, 2, 1024, 9);
        codec.encode_in_place(&mut shards).unwrap();

        let mut opt: Vec<Option<Vec<u8>>> = shards.into_iter().map(Some).collect();
        opt[0] = None;
        opt[1] = None;
        opt[4] = None; // only 3 present < k(=4)

        let err = codec.reconstruct(&mut opt).unwrap_err();
        matches!(err, Error::NotEnoughShards);
    }
}
