//! Python-facing hashing helpers (one-shot and streaming).
//!
//! Exposes in the `animica_native.hash` submodule (once registered by the
//! top-level `py/mod.rs`):
//!
//! One-shot (bytes-in → bytes-out):
//! - `blake3_hash(data: bytes) -> bytes`
//! - `keccak256(data: bytes) -> bytes`
//! - `sha256(data: bytes) -> bytes`
//!
//! Streaming classes (incremental):
//! - `Blake3`: `.update(b"...")`, `.digest() -> bytes`, `.hexdigest() -> str`,
//!             `.digest_xof(len: int) -> bytes`, `.reset()`, `.copy()`
//! - `Keccak256`: `.update`, `.digest`, `.hexdigest`, `.reset`, `.copy`
//! - `Sha256`: `.update`, `.digest`, `.hexdigest`, `.reset`, `.copy`
//!
//! NOTE: This file provides a `register(py, m)` function to add the functions
//! and classes to a given `PyModule`. Ensure the top-level `py/mod.rs` calls
//! `py::hash::register(py, hash_module)` when constructing `animica_native.hash`.

use pyo3::prelude::*;
use pyo3::types::PyBytes;
use pyo3::exceptions::PyValueError;

use blake3 as blake3_crate;
use sha2::{Digest as ShaDigest, Sha256};
use tiny_keccak::{Hasher as TkHasher, Keccak};

/// blake3 (one-shot) — bytes in, 32-byte digest out.
#[pyfunction]
pub fn blake3_hash<'py>(py: Python<'py>, data: &[u8]) -> PyResult<&'py PyBytes> {
    let mut hasher = blake3_crate::Hasher::new();
    hasher.update(data);
    let out = hasher.finalize();
    Ok(PyBytes::new(py, out.as_bytes()))
}

/// keccak256 (one-shot) — bytes in, 32-byte digest out.
#[pyfunction]
pub fn keccak256<'py>(py: Python<'py>, data: &[u8]) -> PyResult<&'py PyBytes> {
    let mut k = Keccak::v256();
    k.update(data);
    let mut out = [0u8; 32];
    k.finalize(&mut out);
    Ok(PyBytes::new(py, &out))
}

/// sha256 (one-shot) — bytes in, 32-byte digest out.
#[pyfunction]
pub fn sha256<'py>(py: Python<'py>, data: &[u8]) -> PyResult<&'py PyBytes> {
    let mut h = Sha256::new();
    h.update(data);
    let out = h.finalize();
    Ok(PyBytes::new(py, &out))
}

/* ----------------------------- streaming ----------------------------- */

/// Streaming BLAKE3 hasher.
#[pyclass(name = "Blake3")]
#[derive(Clone)]
pub struct PyBlake3 {
    inner: blake3_crate::Hasher,
}

#[pymethods]
impl PyBlake3 {
    #[new]
    pub fn new() -> Self {
        Self { inner: blake3_crate::Hasher::new() }
    }

    /// Update internal state with more bytes.
    pub fn update(&mut self, data: &[u8]) {
        self.inner.update(data);
    }

    /// Return the 32-byte digest (does not consume the hasher).
    pub fn digest<'py>(&self, py: Python<'py>) -> PyResult<&'py PyBytes> {
        let mut tmp = self.inner.clone();
        let out = tmp.finalize();
        Ok(PyBytes::new(py, out.as_bytes()))
    }

    /// Return the hex-encoded 32-byte digest (lowercase).
    pub fn hexdigest(&self) -> PyResult<String> {
        let mut tmp = self.inner.clone();
        let out = tmp.finalize();
        Ok(hex::encode(out.as_bytes()))
    }

    /// eXtendable-Output Function — return `len` bytes of output.
    pub fn digest_xof<'py>(&self, py: Python<'py>, len: usize) -> PyResult<&'py PyBytes> {
        if len == 0 {
            return Err(PyValueError::new_err("len must be > 0"));
        }
        let mut tmp = self.inner.clone();
        let mut reader = tmp.finalize_xof();
        let mut out = vec![0u8; len];
        reader.fill(&mut out);
        Ok(PyBytes::new(py, &out))
    }

    /// Reset the hasher to its initial state.
    pub fn reset(&mut self) {
        self.inner = blake3_crate::Hasher::new();
    }

    /// Return a copy (clone) of this hasher.
    pub fn copy(&self) -> Self {
        self.clone()
    }
}

/// Streaming Keccak-256 hasher.
#[pyclass(name = "Keccak256")]
#[derive(Clone)]
pub struct PyKeccak256 {
    inner: Keccak,
}

#[pymethods]
impl PyKeccak256 {
    #[new]
    pub fn new() -> Self {
        Self { inner: Keccak::v256() }
    }

    pub fn update(&mut self, data: &[u8]) {
        self.inner.update(data);
    }

    pub fn digest<'py>(&self, py: Python<'py>) -> PyResult<&'py PyBytes> {
        let mut tmp = self.inner.clone();
        let mut out = [0u8; 32];
        tmp.finalize(&mut out);
        Ok(PyBytes::new(py, &out))
    }

    pub fn hexdigest(&self) -> PyResult<String> {
        let mut tmp = self.inner.clone();
        let mut out = [0u8; 32];
        tmp.finalize(&mut out);
        Ok(hex::encode(out))
    }

    pub fn reset(&mut self) {
        self.inner = Keccak::v256();
    }

    pub fn copy(&self) -> Self {
        self.clone()
    }
}

/// Streaming SHA-256 hasher.
#[pyclass(name = "Sha256")]
#[derive(Clone)]
pub struct PySha256 {
    inner: Sha256,
}

#[pymethods]
impl PySha256 {
    #[new]
    pub fn new() -> Self {
        Self { inner: Sha256::new() }
    }

    pub fn update(&mut self, data: &[u8]) {
        self.inner.update(data);
    }

    pub fn digest<'py>(&self, py: Python<'py>) -> PyResult<&'py PyBytes> {
        let out = self.inner.clone().finalize();
        Ok(PyBytes::new(py, &out))
    }

    pub fn hexdigest(&self) -> PyResult<String> {
        let out = self.inner.clone().finalize();
        Ok(hex::encode(out))
    }

    pub fn reset(&mut self) {
        self.inner = Sha256::new();
    }

    pub fn copy(&self) -> Self {
        self.clone()
    }
}

/* ---------------------------- registration --------------------------- */

/// Register functions and classes into a provided Python module.
/// Intended to be called by `py/mod.rs` when constructing `animica_native.hash`.
pub fn register(py: Python<'_>, m: &PyModule) -> PyResult<()> {
    // One-shot fns
    m.add_function(wrap_pyfunction!(blake3_hash, m)?)?;
    m.add_function(wrap_pyfunction!(keccak256, m)?)?;
    m.add_function(wrap_pyfunction!(sha256, m)?)?;

    // Streaming classes
    m.add_class::<PyBlake3>()?;
    m.add_class::<PyKeccak256>()?;
    m.add_class::<PySha256>()?;

    // Niceties
    m.add("__doc__", "Hash functions (one-shot) and streaming hashers.")?;
    m.add(
        "__all__",
        vec![
            "blake3_hash",
            "keccak256",
            "sha256",
            "Blake3",
            "Keccak256",
            "Sha256",
        ],
    )?;

    // Quick self-check (import-time) is avoided to keep import fast.
    // Users can rely on property tests / benches instead.

    Ok(())
}
