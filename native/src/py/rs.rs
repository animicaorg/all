//! Python-facing Reed–Solomon (RS) helpers.
//!
//! Exposes in the `animica_native.rs` submodule (registered by `py/mod.rs`):
//!
//! - `rs_encode(data: bytes, data_shards: int, parity_shards: int) -> list[bytes]`
//!     Split `data` into `data_shards` pieces, compute `parity_shards` parity
//!     pieces, and return a list of `data_shards + parity_shards` fixed-size
//!     shards (bytes). Shard sizing and length-prefix/padding are handled by
//!     the native layout so the original payload can be losslessly recovered.
//!
//! - `rs_reconstruct(shards: Sequence[Optional[bytes]]) -> bytes`
//!     Reconstruct the original payload from a set of shards where some entries
//!     may be `None` (missing). As long as at least `data_shards` total shards
//!     are present, the function returns the exact original `data` (padding and
//!     length are handled by the native layout).
//!
//! Notes
//! -----
//! * `rs_encode` returns all shards in order: first the `data_shards`, then the
//!   `parity_shards`. Each shard has identical length.
//! * `rs_reconstruct` accepts a list/tuple where each element is either
//!   a `bytes` shard (from `rs_encode`) or `None`. The total length of the
//!   input sequence must equal the original shard count. The function
//!   reconstructs any missing shards internally and returns the original data.
//! * Validation (e.g., shard length consistency, count, corruption checks) is
//!   performed by the native RS implementation. Errors are raised as
//!   `ValueError` on the Python side.
//!
//! This module is a thin wrapper over `crate::rs` (encode/layout/verify).

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBytes, PyList, PySequence};

use crate::rs as rs_native;

fn py_err<S: AsRef<str>>(s: S) -> PyErr {
    PyValueError::new_err(s.as_ref().to_owned())
}

/// Split payload into Reed–Solomon shards (data + parity).
///
/// Python:
///     rs_encode(data: bytes, data_shards: int, parity_shards: int) -> list[bytes]
#[pyfunction(name = "rs_encode")]
pub fn py_rs_encode<'py>(
    py: Python<'py>,
    data: &[u8],
    data_shards: usize,
    parity_shards: usize,
) -> PyResult<&'py PyList> {
    if data_shards == 0 {
        return Err(py_err("data_shards must be > 0"));
    }
    if parity_shards == 0 {
        return Err(py_err("parity_shards must be > 0"));
    }

    // Delegate to native implementation (handles layout & padding).
    let shards = rs_native::encode(data, data_shards, parity_shards)
        .map_err(|e| py_err(format!("rs_encode failed: {e}")))?;

    // Materialize as Python list[bytes]
    let out = PyList::empty(py);
    for s in shards {
        out.append(PyBytes::new(py, &s))?;
    }
    Ok(out)
}

/// Reconstruct the original payload from a full shard set containing holes.
///
/// The `shards` argument is a sequence where each element is either `bytes` or
/// `None`. The total number of elements must match the original total shard
/// count (data + parity).
///
/// Python:
///     rs_reconstruct(shards: Sequence[Optional[bytes]]) -> bytes
#[pyfunction(name = "rs_reconstruct")]
pub fn py_rs_reconstruct<'py>(py: Python<'py>, shards: &PyAny) -> PyResult<&'py PyBytes> {
    let seq: &PySequence = shards.downcast()?;
    let n = seq.len()?.max(0) as usize;
    if n == 0 {
        return Err(py_err("shards must be a non-empty sequence"));
    }

    // Build Vec<Option<Vec<u8>>> expected by native
    let mut opt_shards: Vec<Option<Vec<u8>>> = Vec::with_capacity(n);
    for item in seq.iter()? {
        let obj = item?;
        if obj.is_none() {
            opt_shards.push(None);
            continue;
        }
        // Accept any bytes-like object
        if let Ok(b) = obj.extract::<&[u8]>() {
            opt_shards.push(Some(b.to_vec()));
        } else if let Ok(pb) = obj.downcast::<PyBytes>() {
            opt_shards.push(Some(pb.as_bytes().to_vec()));
        } else {
            return Err(py_err("each shard must be bytes-like or None"));
        }
    }

    // Delegate to native reconstruct (handles padding removal via layout metadata).
    let data = rs_native::reconstruct(&opt_shards)
        .map_err(|e| py_err(format!("rs_reconstruct failed: {e}")))?;

    Ok(PyBytes::new(py, &data))
}

/* ---------------------------- registration --------------------------- */

/// Register functions into a provided Python module (`animica_native.rs`).
pub fn register(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_rs_encode, m)?)?;
    m.add_function(wrap_pyfunction!(py_rs_reconstruct, m)?)?;

    m.add("__doc__", "Reed–Solomon helpers (encode shards, reconstruct payload).")?;
    m.add("__all__", vec!["rs_encode", "rs_reconstruct"])?;
    Ok(())
}
