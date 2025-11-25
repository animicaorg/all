//! Python-facing NMT (Namespace Merkle Tree) helpers.
//!
//! Exposes in the `animica_native.nmt` submodule (once registered by the
//! top-level `py/mod.rs`):
//!
//! - `nmt_root(leaves: Sequence[bytes], ns: bytes) -> bytes`
//!     Compute an NMT root where every provided `leaf` is encoded under the
//!     same namespace id `ns` (8 bytes). Returns the 32-byte root.
//!
//! - `nmt_verify(proof, leaf: bytes, root: bytes) -> bool`
//!     Verify a single-leaf inclusion proof against a 32-byte `root`.
//!     The `proof` can be either:
//!       • a `bytes` blob in the crate's canonical binary encoding, or
//!       • a `dict` with keys:
//!            {"index": int, "namespace": bytes, "siblings": Sequence[bytes]}
//!
//! Notes
//! -----
//! * Namespace IDs are 8 bytes. A `ValueError` is raised if the length differs.
//! * `root` must be 32 bytes (hash digest length). A `ValueError` is raised
//!   otherwise.
//! * This module only wraps existing native functionality found under
//!   `crate::nmt`; it does not re-implement the tree logic.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyBytes, PyDict, PyList, PySequence};

use crate::nmt::{
    encode,
    verify,
    nmt_root as rs_nmt_root,
    types::{Leaf, NamespaceId, Proof},
};

fn py_err<S: AsRef<str>>(s: S) -> PyErr {
    PyValueError::new_err(s.as_ref().to_owned())
}

fn parse_namespace_id(ns: &[u8]) -> PyResult<NamespaceId> {
    if ns.len() != 8 {
        return Err(py_err("namespace id must be exactly 8 bytes"));
    }
    let mut arr = [0u8; 8];
    arr.copy_from_slice(ns);
    Ok(NamespaceId(arr))
}

fn parse_root32(root: &[u8]) -> PyResult<[u8; 32]> {
    if root.len() != 32 {
        return Err(py_err("root must be exactly 32 bytes"));
    }
    let mut out = [0u8; 32];
    out.copy_from_slice(root);
    Ok(out)
}

/// Compute an NMT root from raw `leaves` all tagged with the same namespace `ns` (8 bytes).
///
/// Python:
///     nmt_root(leaves: Sequence[bytes], ns: bytes) -> bytes
#[pyfunction(name = "nmt_root")]
pub fn py_nmt_root<'py>(py: Python<'py>, leaves: &PyAny, ns: &[u8]) -> PyResult<&'py PyBytes> {
    // Parse namespace
    let ns = parse_namespace_id(ns)?;

    // Convert leaves: Sequence[bytes] -> Vec<Leaf>
    let seq: &PySequence = leaves.downcast()?;
    let mut out: Vec<Leaf> = Vec::with_capacity(seq.len().unwrap_or(0).max(0) as usize);

    for item in seq.iter()? {
        let obj = item?;
        // Accept bytes-like
        let data: &PyAny = obj.as_ref(py);
        let buf: &[u8] = if let Ok(b) = data.extract::<&[u8]>() {
            b
        } else if let Ok(b) = data.downcast::<PyBytes>() {
            b.as_bytes()
        } else {
            return Err(py_err("each leaf must be bytes-like"));
        };
        let leaf = encode::encode_leaf(ns, buf);
        out.push(leaf);
    }

    // Compute root
    let root = rs_nmt_root(&out);
    Ok(PyBytes::new(py, &root))
}

/// Try to build a `Proof` from a Python object.
/// Accepts either a raw bytes blob (canonical binary format) or a dict with fields:
///   {"index": int, "namespace": bytes(8), "siblings": Sequence[bytes(32)]}
fn py_to_proof(py: Python<'_>, obj: &PyAny) -> PyResult<Proof> {
    // 1) If bytes: try decode using crate's decoder (canonical format).
    if let Ok(b) = obj.downcast::<PyBytes>() {
        let raw = b.as_bytes();
        return Proof::from_bytes(raw).map_err(|e| py_err(format!("failed to decode proof bytes: {e}")));
    }
    if let Ok(bv) = obj.extract::<&[u8]>() {
        return Proof::from_bytes(bv).map_err(|e| py_err(format!("failed to decode proof bytes: {e}")));
    }

    // 2) If dict: build manually.
    let d: &PyDict = obj.downcast()?;
    // index
    let index: u32 = d
        .get_item("index")
        .ok_or_else(|| py_err("proof dict missing key 'index'"))?
        .extract()?;

    // namespace
    let ns_val = d
        .get_item("namespace")
        .ok_or_else(|| py_err("proof dict missing key 'namespace'"))?;
    let ns_bytes: &[u8] = if let Ok(x) = ns_val.extract::<&[u8]>() {
        x
    } else if let Ok(pb) = ns_val.downcast::<PyBytes>() {
        pb.as_bytes()
    } else {
        return Err(py_err("'namespace' must be bytes-like"));
    };
    let ns = parse_namespace_id(ns_bytes)?;

    // siblings
    let sib_obj = d
        .get_item("siblings")
        .ok_or_else(|| py_err("proof dict missing key 'siblings'"))?;
    let sib_list: &PyList = sib_obj.downcast()?;
    let mut siblings: Vec<[u8; 32]> = Vec::with_capacity(sib_list.len());

    for it in sib_list.iter() {
        let as_bytes: &[u8] = if let Ok(x) = it.extract::<&[u8]>() {
            x
        } else if let Ok(pb) = it.downcast::<PyBytes>() {
            pb.as_bytes()
        } else {
            return Err(py_err("each sibling must be bytes-like"));
        };
        if as_bytes.len() != 32 {
            return Err(py_err("each sibling must be exactly 32 bytes"));
        }
        let mut h = [0u8; 32];
        h.copy_from_slice(as_bytes);
        siblings.push(h);
    }

    Ok(Proof { index, ns, siblings })
}

/// Verify an NMT single-leaf inclusion proof.
/// Accepts `proof` as bytes (canonical) or a dict with {index, namespace, siblings}.
///
/// Python:
///     nmt_verify(proof, leaf: bytes, root: bytes) -> bool
#[pyfunction(name = "nmt_verify")]
pub fn py_nmt_verify(_py: Python<'_>, proof: &PyAny, leaf: &[u8], root: &[u8]) -> PyResult<bool> {
    let py = unsafe { Python::assume_gil_acquired() }; // already in GIL
    let proof = py_to_proof(py, proof)?;
    let root = parse_root32(root)?;

    // Build encoded leaf from the proof's namespace and provided data.
    let enc_leaf = encode::encode_leaf(proof.ns, leaf);

    // Delegate to native verify
    Ok(verify::verify(&proof, &enc_leaf, &root))
}

/* ---------------------------- registration --------------------------- */

/// Register functions into a provided Python module.
/// Intended to be called by `py/mod.rs` when constructing `animica_native.nmt`.
pub fn register(_py: Python<'_>, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(py_nmt_root, m)?)?;
    m.add_function(wrap_pyfunction!(py_nmt_verify, m)?)?;

    m.add("__doc__", "Namespace Merkle Tree helpers (root and proof verification).")?;
    m.add("__all__", vec!["nmt_root", "nmt_verify"])?;
    Ok(())
}
