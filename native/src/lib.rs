//! animica_native — fast primitives and optional Python/C entrypoints.
//!
//! Features:
//! - `simd`   : let upstream libs pick SIMD code paths when available
//! - `rayon`  : parallel helpers (hash many, etc.)
//! - `isal`   : ISA-L accelerated erasure coding (stubbed here if disabled)
//! - `c_keccak`: prefer a C backend for SHA3/Keccak (portable fallback provided)
//! - `python` : expose a PyO3 module with safe error mapping
//!
//! Build notes:
//! - FFI (C ABI) symbols are always exported for a minimal surface.
//! - Python module is exported only with `--features python`.
//!
//! Safety notes:
//! - All `extern "C"` entrypoints validate pointers, lengths, and output buffers.
//! - Errors map to stable integer codes (see `ffi` section below).

#![forbid(unsafe_op_in_unsafe_fn)]
#![deny(rust_2018_idioms, unused_must_use)]

use core::fmt;
use core::slice;

#[cfg(feature = "rayon")]
use rayon::prelude::*;

//
// Error type and mapping
//

/// Library error type (mapped to both Python exceptions and FFI codes).
#[derive(Debug, Clone)]
pub enum Error {
    /// Caller provided invalid argument(s).
    InvalidArgument(&'static str),
    /// Optional feature not compiled in or runtime capability unavailable.
    FeatureUnavailable(&'static str),
    /// Cryptographic failure (rare; indicates misuse or internal error).
    CryptoError(&'static str),
    /// Unexpected internal failure.
    Internal(&'static str),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::InvalidArgument(s) => write!(f, "invalid argument: {s}"),
            Error::FeatureUnavailable(s) => write!(f, "feature unavailable: {s}"),
            Error::CryptoError(s) => write!(f, "crypto error: {s}"),
            Error::Internal(s) => write!(f, "internal error: {s}"),
        }
    }
}

impl std::error::Error for Error {}

//
// Hashing primitives
//

/// Compute BLAKE3 hash (32 bytes).
pub fn blake3_hash(input: &[u8]) -> [u8; 32] {
    let hash = blake3::hash(input);
    *hash.as_bytes()
}

/// Compute SHA3-256 hash (32 bytes).
///
/// If `c_keccak` is enabled, a C backend can be wired via build.rs; this function
/// still provides a portable RustCrypto fallback to keep builds hermetic.
pub fn sha3_256_hash(input: &[u8]) -> [u8; 32] {
    #[cfg(feature = "c_keccak")]
    {
        // If you provide a C wrapper in build.rs, you can swap this block to call it.
        // The portable fallback below remains correct and constant-time in Rust.
    }

    use sha3::{Digest, Sha3_256};
    let mut hasher = Sha3_256::new();
    hasher.update(input);
    let out = hasher.finalize();
    let mut arr = [0u8; 32];
    arr.copy_from_slice(&out);
    arr
}

/// Hash each chunk with BLAKE3 and return digests.
///
/// With `rayon`, this is parallel; otherwise it’s sequential.
pub fn blake3_hash_chunks<'a, I>(chunks: I) -> Vec<[u8; 32]>
where
    I: IntoIterator<Item = &'a [u8]>,
{
    let iter = chunks.into_iter();

    #[cfg(feature = "rayon")]
    {
        return iter.par_bridge().map(blake3_hash).collect();
    }

    #[allow(unreachable_code)]
    iter.map(blake3_hash).collect()
}

//
// (Stub) Erasure coding surface (ISA-L)
//

/// Example API for RS encode using ISA-L; returns `FeatureUnavailable` if `isal` is off.
/// This is a placeholder to define the error surface and callsites; real implementation
/// should live behind the `isal` gate and link to ISA-L via a Rust binding or FFI.
pub fn rs_encode_shards(
    _data_shards: &[&[u8]],
    _parity_shards: usize,
) -> Result<Vec<Vec<u8>>, Error> {
    #[cfg(feature = "isal")]
    {
        // Replace with actual ISA-L accelerated implementation.
        return Err(Error::Internal("rs_encode_shards: implementation missing"));
    }

    Err(Error::FeatureUnavailable("isal"))
}

//
// Feature introspection
//

/// Return a static list of compile-time feature flags that were enabled.
pub fn enabled_features() -> &'static [&'static str] {
    const FEATS: &[&str] = &[
        #[cfg(feature = "simd")]
        "simd",
        #[cfg(feature = "rayon")]
        "rayon",
        #[cfg(feature = "isal")]
        "isal",
        #[cfg(feature = "c_keccak")]
        "c_keccak",
        #[cfg(feature = "python")]
        "python",
    ];
    FEATS
}

//
// -----------------------------
// C FFI (stable C ABI)
// -----------------------------
//
// Return codes for FFI functions. Keep these stable for downstreams.
//

/// FFI return codes (mirrored as `i32`).
pub mod ffi_codes {
    pub const OK: i32 = 0;
    pub const INVALID_ARGUMENT: i32 = 1;
    pub const FEATURE_UNAVAILABLE: i32 = 2;
    pub const CRYPTO_ERROR: i32 = 3;
    pub const INTERNAL: i32 = 255;
}

fn map_err_to_code(err: Error) -> i32 {
    use ffi_codes::*;
    match err {
        Error::InvalidArgument(_) => INVALID_ARGUMENT,
        Error::FeatureUnavailable(_) => FEATURE_UNAVAILABLE,
        Error::CryptoError(_) => CRYPTO_ERROR,
        Error::Internal(_) => INTERNAL,
    }
}

fn result_to_code<T>(r: Result<T, Error>) -> i32 {
    match r {
        Ok(_) => ffi_codes::OK,
        Err(e) => map_err_to_code(e),
    }
}

fn check_nonnull<'a>(ptr: *const u8, len: usize) -> Result<&'a [u8], Error> {
    if ptr.is_null() && len > 0 {
        return Err(Error::InvalidArgument("null input pointer with nonzero length"));
    }
    // SAFETY: caller promises `ptr` is valid for `len` bytes; we guard null + len>0 above.
    let slice = unsafe { slice::from_raw_parts(ptr, len) };
    Ok(slice)
}

fn check_outbuf<'a>(ptr: *mut u8, out_len: usize, needed: usize) -> Result<&'a mut [u8], Error> {
    if ptr.is_null() {
        return Err(Error::InvalidArgument("null output pointer"));
    }
    if out_len < needed {
        return Err(Error::InvalidArgument("output buffer too small"));
    }
    // SAFETY: caller promises `ptr` is valid for `out_len` bytes; we bounded by `needed`.
    let slice = unsafe { slice::from_raw_parts_mut(ptr, needed) };
    Ok(slice)
}

/// Compute BLAKE3(input) -> 32 bytes.
///
/// # Safety
/// - `data` must be either NULL with `len==0` or a valid pointer to `len` bytes.
/// - `out32` must point to a buffer of at least 32 bytes.
/// - This function never panics; it returns a nonzero error code on failure.
#[no_mangle]
pub extern "C" fn animica_blake3_hash(
    data: *const u8,
    len: usize,
    out32: *mut u8,
    out_len: usize,
) -> i32 {
    let r = (|| {
        let input = check_nonnull(data, len)?;
        let out = check_outbuf(out32, out_len, 32)?;
        let digest = blake3_hash(input);
        out.copy_from_slice(&digest);
        Ok(())
    })();
    result_to_code(r)
}

/// Compute SHA3-256(input) -> 32 bytes (portable; may use C backend if wired).
///
/// # Safety
/// Same rules as `animica_blake3_hash`.
#[no_mangle]
pub extern "C" fn animica_sha3_256(
    data: *const u8,
    len: usize,
    out32: *mut u8,
    out_len: usize,
) -> i32 {
    let r = (|| {
        let input = check_nonnull(data, len)?;
        let out = check_outbuf(out32, out_len, 32)?;
        let digest = sha3_256_hash(input);
        out.copy_from_slice(&digest);
        Ok(())
    })();
    result_to_code(r)
}

/// Return a bitset of enabled features for quick probing.
///
/// Bit layout (LSB->MSB): 0:simd, 1:rayon, 2:isal, 3:c_keccak, 4:python
#[no_mangle]
pub extern "C" fn animica_features_mask() -> u32 {
    let mut m = 0u32;
    #[cfg(feature = "simd")]
    {
        m |= 1 << 0;
    }
    #[cfg(feature = "rayon")]
    {
        m |= 1 << 1;
    }
    #[cfg(feature = "isal")]
    {
        m |= 1 << 2;
    }
    #[cfg(feature = "c_keccak")]
    {
        m |= 1 << 3;
    }
    #[cfg(feature = "python")]
    {
        m |= 1 << 4;
    }
    m
}

//
// -----------------------------
// Python module (PyO3)
// -----------------------------

#[cfg(feature = "python")]
mod pyo3_mod {
    use super::*;
    use pyo3::exceptions::{PyRuntimeError, PyValueError};
    use pyo3::prelude::*;
    use pyo3::types::{PyBytes, PyList};

    impl From<Error> for PyErr {
        fn from(e: Error) -> Self {
            match e {
                Error::InvalidArgument(msg) => PyValueError::new_err(msg),
                Error::FeatureUnavailable(msg) => PyRuntimeError::new_err(msg),
                Error::CryptoError(msg) => PyRuntimeError::new_err(msg),
                Error::Internal(msg) => PyRuntimeError::new_err(msg),
            }
        }
    }

    #[pyfunction]
    fn blake3(py: Python<'_>, data: &[u8]) -> PyResult<PyObject> {
        let digest = super::blake3_hash(data);
        Ok(PyBytes::new(py, &digest).into_py(py))
    }

    #[pyfunction]
    fn sha3_256(py: Python<'_>, data: &[u8]) -> PyResult<PyObject> {
        let digest = super::sha3_256_hash(data);
        Ok(PyBytes::new(py, &digest).into_py(py))
    }

    #[pyfunction]
    fn blake3_chunks(py: Python<'_>, chunks: &PyList) -> PyResult<PyObject> {
        // Accept any bytes-like elements.
        let mut vec: Vec<[u8; 32]> = Vec::with_capacity(chunks.len());
        for item in chunks.iter() {
            let b: &pyo3::types::PyAny = item;
            let view = pyo3::buffer::PyBuffer::<u8>::get(b)?;
            // SAFETY: copy the view into an owned Rust slice.
            let slice = view.to_vec()?;
            vec.push(super::blake3_hash(&slice));
        }
        let out = PyList::new_bound(
            py,
            vec.iter().map(|d| PyBytes::new(py, d).into_py(py)),
        );
        Ok(out.into_py(py))
    }

    #[pyfunction]
    fn features() -> PyResult<Vec<&'static str>> {
        Ok(super::enabled_features().to_vec())
    }

    /// Python module name must match the library name defined for pyo3/maturin.
    #[pymodule]
    fn animica_native_py(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(blake3, m)?)?;
        m.add_function(wrap_pyfunction!(sha3_256, m)?)?;
        m.add_function(wrap_pyfunction!(blake3_chunks, m)?)?;
        m.add_function(wrap_pyfunction!(features, m)?)?;
        Ok(())
    }
}


// --- Animica Namespace Merkle Tree (NMT) module -----------------------------
// This wires the `src/nmt/` implementation into the public crate so that both
// Rust callers and tests can use `animica_native::nmt::*`.
pub mod nmt;

// --- Animica hash utilities (BLAKE3, SHA3, etc.) ----------------------------
// This exposes the `src/hash` implementation so internal modules like NMT and
// external callers/tests can use `crate::hash::*`.
pub mod hash;

// --- Animica Reed–Solomon erasure coding -----------------------------------
// Expose the RS implementation so callers and tests can use `animica_native::rs`.
pub mod rs;
