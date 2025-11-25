//! Intel ISA-L backend (optional; `--features isal`)
//!
//! High-throughput Reed–Solomon (GF(2^8)) encode/reconstruct/verify via
//! Intel ISA-L's erasure coding primitives. This backend mirrors the public
//! API exposed by the default Rust backend so the top-level `rs` module can
//! `pub use` one or the other depending on feature flags.
//!
//! ## Requirements
//! - Link against `isa-l` (shared or static). `build.rs` is expected to emit
//!   proper `cargo:rustc-link-lib=isal` when the `isal` feature is on.
//! - This file is only compiled when `feature = "isal"` is enabled.
//!
//! ## Design
//! - Generator matrix: Vandermonde (`gf_gen_rs_matrix`). The top `k` rows form
//!   identity (systematic code). The bottom `m` rows are used as parity rows.
//! - Encoding: single `ec_init_tables(k, m, A_parity, gftbls)` and
//!   `ec_encode_data(len, k, m, gftbls, data_ptrs, parity_ptrs)`.
//! - Reconstruction: pick any `k` present shards (data or parity), build a
//!   `k×k` submatrix `B` of generator rows, invert it (`gf_invert_matrix`).
//!   To recover missing data shard `j`, use row `j` of `B^{-1}` as coefficients
//!   with `ec_encode_data` on the `k` present shards. Parity shards (if missing)
//!   are then recomputed with a normal encode.
//!
//! Safety notes: all ISA-L calls are wrapped in small `unsafe {}` blocks and
//! arguments are validated beforehand (length checks, counts, etc.).

#![cfg(feature = "isal")]

use core::fmt;
use std::os::raw::c_int;

/* ------------------------------- FFI (ISA-L) ------------------------------ */

// On most platforms `build.rs` will provide the link flags; the explicit
// #[link] isn't strictly necessary but is harmless when present.
#[cfg_attr(
    any(target_os = "linux", target_os = "macos", target_os = "freebsd"),
    link(name = "isal")
)]
extern "C" {
    fn gf_gen_rs_matrix(a: *mut u8, rows: c_int, k: c_int);
    fn gf_invert_matrix(input: *const u8, output: *mut u8, k: c_int) -> c_int;

    fn ec_init_tables(k: c_int, rows: c_int, a: *const u8, gftbls: *mut u8);
    fn ec_encode_data(
        len: c_int,
        k: c_int,
        rows: c_int,
        gftbls: *const u8,
        data: *const *const u8,
        coding: *mut *mut u8,
    );
}

/* --------------------------------- Types --------------------------------- */

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

/// Backend-agnostic error type, kept isomorphic with the default backend.
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

/// ISA-L powered RS codec (GF(256)).
#[derive(Debug)]
pub struct Codec {
    params: Params,
    /// Parity coefficient matrix (m × k), contiguous row-major.
    a_parity: Vec<u8>,
    /// Precomputed tables for encode: 32 * k * m bytes.
    gftbls: Vec<u8>,
}

impl Codec {
    /// Create a codec for RS(k+m, k), precomputing parity rows and tables.
    pub fn new(data_shards: usize, parity_shards: usize) -> Result<Self, Error> {
        let p = Params { data_shards, parity_shards };
        p.validate()?;

        // ISA-L expects a (k+m) × k matrix for RS; top k rows are identity,
        // bottom m rows are parity coefficients.
        let rows_total = p.total();
        let mut full = vec![0u8; rows_total * p.data_shards];
        unsafe {
            gf_gen_rs_matrix(full.as_mut_ptr(), rows_total as c_int, p.data_shards as c_int);
        }
        let a_parity = full[p.data_shards * p.data_shards..].to_vec(); // m × k

        // Precompute encode tables for all parity rows at once.
        let mut gftbls = vec![0u8; 32 * p.data_shards * p.parity_shards];
        unsafe {
            ec_init_tables(
                p.data_shards as c_int,
                p.parity_shards as c_int,
                a_parity.as_ptr(),
                gftbls.as_mut_ptr(),
            );
        }

        Ok(Self { params: p, a_parity, gftbls })
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

        let data_len = ensure_equal_len(&shards[..p.data_shards])?;
        for s in &mut shards[p.data_shards..] {
            if s.len() != data_len {
                s.resize(data_len, 0);
            }
        }
        let _ = ensure_equal_len(&*shards)?; // parity also equal length now

        if data_len as u64 > i32::MAX as u64 {
            return Err(Error::InvalidArg("shard length exceeds i32::MAX"));
        }

        // Build pointer arrays.
        let data_ptrs: Vec<*const u8> = shards[..p.data_shards].iter().map(|v| v.as_ptr()).collect();
        let mut parity_ptrs: Vec<*mut u8> = shards[p.data_shards..]
            .iter_mut()
            .map(|v| v.as_mut_ptr())
            .collect();

        unsafe {
            ec_encode_data(
                data_len as c_int,
                p.data_shards as c_int,
                p.parity_shards as c_int,
                self.gftbls.as_ptr(),
                data_ptrs.as_ptr(),
                parity_ptrs.as_mut_ptr(),
            );
        }
        Ok(())
    }

    /// Verify that data+parity are consistent by re-encoding and comparing.
    pub fn verify(&self, shards: &[Vec<u8>]) -> Result<bool, Error> {
        let p = self.params;
        if shards.len() != p.total() {
            return Err(Error::InvalidArg("shards.len() must equal k + m"));
        }
        let data_len = ensure_equal_len(&*shards)?;
        if data_len as u64 > i32::MAX as u64 {
            return Err(Error::InvalidArg("shard length exceeds i32::MAX"));
        }

        // Compute expected parity into scratch and compare.
        let data_ptrs: Vec<*const u8> = shards[..p.data_shards].iter().map(|v| v.as_ptr()).collect();
        let mut scratch: Vec<Vec<u8>> = (0..p.parity_shards).map(|_| vec![0u8; data_len]).collect();
        let mut scratch_ptrs: Vec<*mut u8> = scratch.iter_mut().map(|v| v.as_mut_ptr()).collect();

        unsafe {
            ec_encode_data(
                data_len as c_int,
                p.data_shards as c_int,
                p.parity_shards as c_int,
                self.gftbls.as_ptr(),
                data_ptrs.as_ptr(),
                scratch_ptrs.as_mut_ptr(),
            );
        }

        for (exp, got) in scratch.iter().zip(shards[p.data_shards..].iter()) {
            if exp.as_slice() != got.as_slice() {
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// Reconstruct missing shards **in place**.
    ///
    /// * `shards` length must be `k+m`.
    /// * At least `k` shards must be present (Some).
    /// * Present shard lengths must be identical.
    /// * Missing shards will be allocated and filled to the correct length.
    pub fn reconstruct(&self, shards: &mut [Option<Vec<u8>>]) -> Result<(), Error> {
        let p = self.params;
        if shards.len() != p.total() {
            return Err(Error::InvalidArg("shards.len() must equal k + m"));
        }

        // Gather present shard indices and validate lengths.
        let mut present_idxs: Vec<usize> = Vec::with_capacity(p.total());
        let mut present_len: Option<usize> = None;
        for (i, s) in shards.iter().enumerate() {
            if let Some(v) = s {
                if let Some(len0) = present_len {
                    if v.len() != len0 {
                        return Err(Error::ShardLenMismatch);
                    }
                } else {
                    present_len = Some(v.len());
                }
                present_idxs.push(i);
            }
        }
        if present_idxs.len() < p.data_shards {
            return Err(Error::NotEnoughShards);
        }
        let len = present_len.unwrap_or(0);
        if len as u64 > i32::MAX as u64 {
            return Err(Error::InvalidArg("shard length exceeds i32::MAX"));
        }

        // Allocate missing shards (filled with zeros for now).
        for slot in shards.iter_mut() {
            if slot.is_none() {
                *slot = Some(vec![0u8; len]);
            }
        }

        // Fast path: if all data shards present, just recompute parity.
        if (0..p.data_shards).all(|i| shards[i].is_some()) {
            let mut owned: Vec<Vec<u8>> = shards.iter_mut().map(|o| o.take().unwrap()).collect();
            self.encode_in_place(&mut owned)?;
            for (dst, v) in shards.iter_mut().zip(owned.into_iter()) {
                *dst = Some(v);
            }
            return Ok(());
        }

        // Build B (k×k) from any k present shards.
        let chosen: Vec<usize> = present_idxs.into_iter().take(p.data_shards).collect();
        let mut b = vec![0u8; p.data_shards * p.data_shards];
        for (row, &idx) in chosen.iter().enumerate() {
            // Generator row for shard `idx`
            if idx < p.data_shards {
                // Identity row e_idx
                b[row * p.data_shards + idx] = 1;
            } else {
                // Parity row from A_parity[(idx-k)]
                let parity_row = idx - p.data_shards;
                let src = &self.a_parity[parity_row * p.data_shards .. (parity_row + 1) * p.data_shards];
                b[row * p.data_shards .. (row + 1) * p.data_shards].copy_from_slice(src);
            }
        }

        // Invert B → B_inv (k×k).
        let mut b_inv = vec![0u8; p.data_shards * p.data_shards];
        let inv_rc = unsafe {
            gf_invert_matrix(
                b.as_ptr(),
                b_inv.as_mut_ptr(),
                p.data_shards as c_int
            )
        };
        if inv_rc != 0 {
            return Err(Error::Backend("gf_invert_matrix failed (singular?)".into()));
        }

        // Pointers to the chosen k present shard buffers.
        let chosen_ptrs: Vec<*const u8> = chosen
            .iter()
            .map(|&i| shards[i].as_ref().unwrap().as_ptr())
            .collect();

        // Recover each missing data shard j using row j of B_inv as coefficients.
        for j in 0..p.data_shards {
            if shards[j].as_ref().unwrap().iter().any(|&b| b != 0) {
                // Heuristic: if not missing, skip. (We allocated zeros for None earlier.)
                continue;
            }
            if let Some(slot) = shards.get_mut(j) {
                let out = slot.as_mut().unwrap();
                // Row j coefficients (k bytes).
                let coeff_row = &b_inv[j * p.data_shards .. (j + 1) * p.data_shards];

                let mut row_tbls = vec![0u8; 32 * p.data_shards * 1];
                unsafe {
                    ec_init_tables(
                        p.data_shards as c_int,
                        1,
                        coeff_row.as_ptr(),
                        row_tbls.as_mut_ptr(),
                    );

                    // Single-row encode into `out`.
                    let mut out_ptr: [*mut u8; 1] = [out.as_mut_ptr()];
                    ec_encode_data(
                        len as c_int,
                        p.data_shards as c_int,
                        1,
                        row_tbls.as_ptr(),
                        chosen_ptrs.as_ptr(),
                        out_ptr.as_mut_ptr(),
                    );
                }
            }
        }

        // Recompute parity for completeness (covers any missing parity shards).
        let mut owned: Vec<Vec<u8>> = shards.iter_mut().map(|o| o.take().unwrap()).collect();
        self.encode_in_place(&mut owned)?;
        for (dst, v) in shards.iter_mut().zip(owned.into_iter()) {
            *dst = Some(v);
        }
        Ok(())
    }
}

/* ------------------------------- Utilities ------------------------------- */

#[inline]
fn ensure_equal_len<'a, T: AsRef<[u8]> + 'a>(
    shards: impl IntoIterator<Item = &'a T>
) -> Result<usize, Error> {
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

#[cfg(all(test, feature = "isal"))]
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
        let (codec, mut shards) = make_random_shards(6, 3, 2048, 1);
        codec.encode_in_place(&mut shards).unwrap();
        assert!(codec.verify(&shards).unwrap());
    }

    #[test]
    fn reconstruct_mixed_missing() {
        let (codec, mut shards) = make_random_shards(5, 3, 4096, 7);
        codec.encode_in_place(&mut shards).unwrap();

        // Drop two data shards and one parity shard.
        let mut opt: Vec<Option<Vec<u8>>> = shards.into_iter().map(Some).collect();
        opt[0] = None; // data
        opt[3] = None; // data
        opt[6] = None; // parity (index 5..7 are parity)

        codec.reconstruct(&mut opt).unwrap();
        let rebuilt: Vec<Vec<u8>> = opt.into_iter().map(|o| o.unwrap()).collect();
        assert!(codec.verify(&rebuilt).unwrap());
    }

    #[test]
    fn rejects_length_mismatch() {
        let codec = Codec::new(3, 2).unwrap();
        let mut shards = vec![vec![1u8; 10], vec![2u8; 11], vec![], vec![], vec![]];
        let err = codec.encode_in_place(&mut shards).unwrap_err();
        matches!(err, Error::ShardLenMismatch);
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
