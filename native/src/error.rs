//! Error types and cross-language mappings for animica_native.
//!
//! This module defines a lightweight `NativeError` that can be:
//! - returned in Rust results,
//! - losslessly mapped to Python exceptions (via PyO3 when the `python` feature is enabled),
//! - converted to stable C FFI integer status codes.
//!
//! Keep this surface minimal and stable—external callers rely on the codes and
//! exception shapes remaining consistent across versions.

use core::fmt;

/// Canonical error for the native crate.
#[derive(Debug, Clone)]
#[non_exhaustive]
pub enum NativeError {
    /// Caller provided invalid argument(s).
    InvalidArgument(&'static str),
    /// Optional feature not compiled in, or runtime capability unavailable.
    FeatureUnavailable(&'static str),
    /// Cryptographic failure (misuse or internal error).
    CryptoError(&'static str),
    /// Unexpected internal failure.
    Internal(&'static str),
}

impl fmt::Display for NativeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            NativeError::InvalidArgument(s) => write!(f, "invalid argument: {s}"),
            NativeError::FeatureUnavailable(s) => write!(f, "feature unavailable: {s}"),
            NativeError::CryptoError(s) => write!(f, "crypto error: {s}"),
            NativeError::Internal(s) => write!(f, "internal error: {s}"),
        }
    }
}

impl std::error::Error for NativeError {}

/// Stable C FFI status codes.
///
/// Keep these values stable across releases—they're part of the public ABI.
pub mod ffi_codes {
    pub const OK: i32 = 0;
    pub const INVALID_ARGUMENT: i32 = 1;
    pub const FEATURE_UNAVAILABLE: i32 = 2;
    pub const CRYPTO_ERROR: i32 = 3;
    pub const INTERNAL: i32 = 255;
}

impl NativeError {
    /// Convert to a stable C FFI status code.
    pub fn to_ffi_code(&self) -> i32 {
        use ffi_codes::*;
        match self {
            NativeError::InvalidArgument(_) => INVALID_ARGUMENT,
            NativeError::FeatureUnavailable(_) => FEATURE_UNAVAILABLE,
            NativeError::CryptoError(_) => CRYPTO_ERROR,
            NativeError::Internal(_) => INTERNAL,
        }
    }
}

/// Helper to map `Result<T, NativeError>` into a C status code.
/// Returns `ffi_codes::OK` on `Ok(_)`, or the mapped error code on `Err`.
pub fn result_to_code<T>(res: Result<T, NativeError>) -> i32 {
    match res {
        Ok(_) => ffi_codes::OK,
        Err(e) => e.to_ffi_code(),
    }
}

//
// Python (PyO3) mapping
//

#[cfg(feature = "python")]
mod py {
    use super::NativeError;
    use pyo3::exceptions::{PyRuntimeError, PyValueError};
    use pyo3::PyErr;

    impl From<NativeError> for PyErr {
        fn from(e: NativeError) -> Self {
            match e {
                // Argument issues are surfaced as ValueError for idiomatic Python.
                NativeError::InvalidArgument(msg) => PyValueError::new_err(msg),
                // Operational issues become RuntimeError.
                NativeError::FeatureUnavailable(msg)
                | NativeError::CryptoError(msg)
                | NativeError::Internal(msg) => PyRuntimeError::new_err(msg),
            }
        }
    }

    /// Explicit helper when you prefer a method-style conversion.
    impl NativeError {
        pub fn into_pyerr(self) -> PyErr {
            self.into()
        }
    }
}

//
// Interop with the crate-local error (if present)
//

#[allow(dead_code)]
impl From<NativeError> for String {
    fn from(e: NativeError) -> Self {
        e.to_string()
    }
}

/// Optional conversion from a broader crate error to `NativeError`.
/// If the crate defines a different error enum, you can add a `From<crate::Error>`
/// implementation here (or the reverse). This block is harmless if such a type
/// does not exist yet.
#[allow(dead_code)]
impl From<&'static str> for NativeError {
    fn from(msg: &'static str) -> Self {
        NativeError::Internal(msg)
    }
}

/// Convenience alias for results that use `NativeError`.
pub type NativeResult<T> = Result<T, NativeError>;

