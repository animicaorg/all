//! Python bindings for `animica_native` via PyO3.
//!
//! Exposes a top-level module `animica_native` with submodules:
//! - `utils` — CPU feature flags and small helpers
//! - `hash`  — BLAKE3 / SHA-256 / Keccak-256
//! - `nmt`   — Namespace Merkle Tree helpers (root/verify - minimal surface)
//! - `rs`    — Reed–Solomon helpers (encode, parity check)
//! - `bench` — tiny benchmarking helpers (best-effort, wall-clock)
//!
//! All functions aim to be allocation-friendly and return plain Python
//! primitives (`bytes`, `list[bytes]`, `dict`, `bool`, `int`) for ergonomic use.

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};

use crate::error::NativeError;

/* ------------------------------- utils ------------------------------- */

#[pyfunction]
fn cpu_flags<'py>(py: Python<'py>) -> PyResult<&'py PyDict> {
    let d = PyDict::new(py);
    // These helpers are exported by native/src/utils/cpu.rs
    let (avx2, sha, neon, sha3) = {
        #[allow(unused_imports)]
        use crate::utils::cpu;
        (
            cpu::has_avx2(),
            cpu::has_sha(),
            cpu::has_neon(),
            cpu::has_sha3(),
        )
    };
    d.set_item("avx2", avx2)?;
    d.set_item("sha", sha)?;
    d.set_item("neon", neon)?;
    d.set_item("sha3", sha3)?;
    Ok(d)
}

#[pymodule]
fn utils(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(cpu_flags, m)?)?;
    // Potential future: utils.zero_copy_view(...) etc.
    // Keep the submodule docstring helpful.
    m.add("__doc__", "Low-level utilities (CPU feature flags, helpers)")?;
    // Provide __all__ for nicer dir()
    let all = vec!["cpu_flags"];
    m.add("__all__", all)?;
    Ok(())
}

/* -------------------------------- hash -------------------------------- */

#[pyfunction]
fn blake3<'py>(py: Python<'py>, data: &[u8]) -> PyResult<&'py PyBytes> {
    use crate::hash::blake3 as b3;
    let out = b3::hash(data);
    Ok(PyBytes::new(py, &out))
}

#[pyfunction]
fn sha256<'py>(py: Python<'py>, data: &[u8]) -> PyResult<&'py PyBytes> {
    use crate::hash::sha256;
    let out = sha256::hash(data);
    Ok(PyBytes::new(py, &out))
}

#[pyfunction]
fn keccak256<'py>(py: Python<'py>, data: &[u8]) -> PyResult<&'py PyBytes> {
    use crate::hash::keccak;
    let out = keccak::keccak256(data);
    Ok(PyBytes::new(py, &out))
}

#[pymodule]
fn hash(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(blake3, m)?)?;
    m.add_function(wrap_pyfunction!(sha256, m)?)?;
    m.add_function(wrap_pyfunction!(keccak256, m)?)?;
    m.add("__doc__", "Hash functions: BLAKE3 / SHA-256 / Keccak-256")?;
    let all = vec!["blake3", "sha256", "keccak256"];
    m.add("__all__", all)?;
    Ok(())
}

/* -------------------------------- nmt -------------------------------- */

#[pyfunction]
fn nmt_root<'py>(py: Python<'py>, leaves: Vec<(Vec<u8>, Vec<u8>)>) -> PyResult<&'py PyBytes> {
    // leaves: List[Tuple[namespace_id: bytes, data: bytes]]
    use crate::nmt::{self, types::Leaf};
    use crate::nmt::types::NamespaceId;

    let mut rs_leaves = Vec::with_capacity(leaves.len());
    for (ns, data) in leaves {
        let ns_id = NamespaceId::try_from(ns.as_slice())
            .map_err(|_| PyValueError::new_err("invalid namespace id length"))?;
        rs_leaves.push(Leaf { ns: ns_id, data });
    }

    let root = nmt::nmt_root(&rs_leaves).map_err(to_py_err)?;
    Ok(PyBytes::new(py, &root))
}

/// Minimal inclusion/range verification wrapper.
/// Arguments:
/// - `root`: bytes — the NMT root (32 bytes)
/// - `namespace`: bytes — namespace id of the leaf/range
/// - `proof_siblings`: List[bytes] — proof nodes (left-to-right)
/// - `start`: usize — leaf start index (inclusive)
/// - `end`: usize — leaf end index (exclusive)
/// - `leaves`: Optional[List[Tuple[bytes, bytes]]] — if provided, verifies a small contiguous range.
/// Returns:
/// - bool: `True` if verification succeeds.
#[pyfunction]
#[pyo3(signature = (root, namespace, proof_siblings, start, end, leaves=None))]
fn nmt_verify_range(
    root: &[u8],
    namespace: Vec<u8>,
    proof_siblings: Vec<Vec<u8>>,
    start: usize,
    end: usize,
    leaves: Option<Vec<(Vec<u8>, Vec<u8>)>>,
) -> PyResult<bool> {
    use crate::nmt::{self, types::{Leaf, Proof, NamespaceId}};

    let ns_id = NamespaceId::try_from(namespace.as_slice())
        .map_err(|_| PyValueError::new_err("invalid namespace id length"))?;
    if root.len() != 32 {
        return Err(PyValueError::new_err("root must be 32 bytes"));
    }
    if start >= end {
        return Err(PyValueError::new_err("start must be < end"));
    }
    // Construct a minimal Proof from siblings (helper assumes siblings-only).
    let proof = Proof::from_siblings_only(proof_siblings).map_err(to_py_err)?;

    let maybe_leaves = leaves.map(|ls| {
        ls.into_iter()
            .map(|(ns, data)| {
                let ns_id = NamespaceId::try_from(ns.as_slice()).expect("namespace id");
                Leaf { ns: ns_id, data }
            })
            .collect::<Vec<_>>()
    });

    nmt::verify_range(root, ns_id, &proof, start..end, maybe_leaves.as_deref())
        .map_err(to_py_err)
}

#[pymodule]
fn nmt(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(nmt_root, m)?)?;
    m.add_function(wrap_pyfunction!(nmt_verify_range, m)?)?;
    m.add("__doc__", "Namespace Merkle Tree: root + (minimal) verify helpers")?;
    let all = vec!["nmt_root", "nmt_verify_range"];
    m.add("__all__", all)?;
    Ok(())
}

/* -------------------------------- rs --------------------------------- */

#[pyfunction]
fn rs_encode<'py>(
    py: Python<'py>,
    data_shards: Vec<&[u8]>,
    parity_shards: usize,
) -> PyResult<Vec<&'py PyBytes>> {
    use reed_solomon_erasure::galois_8::ReedSolomon;

    if data_shards.is_empty() {
        return Err(PyValueError::new_err("data_shards must be non-empty"));
    }
    let k = data_shards.len();
    let shard_len = data_shards[0].len();
    if !data_shards.iter().all(|s| s.len() == shard_len) {
        return Err(PyValueError::new_err("all data shards must have equal length"));
    }
    if parity_shards == 0 {
        return Err(PyValueError::new_err("parity_shards must be > 0"));
    }

    // Prepare shards vector: clone data, allocate parity placeholders
    let mut shards: Vec<Vec<u8>> = data_shards.iter().map(|s| s.to_vec()).collect();
    for _ in 0..parity_shards {
        shards.push(vec![0u8; shard_len]);
    }

    let rs = ReedSolomon::new(k, parity_shards)
        .map_err(|_| PyRuntimeError::new_err("failed to init reed-solomon"))?;
    rs.encode(&mut shards)
        .map_err(|_| PyRuntimeError::new_err("reed-solomon encode failed"))?;

    // Return only parity shards as bytes objects
    let mut out = Vec::with_capacity(parity_shards);
    for s in shards.iter().skip(k) {
        out.push(PyBytes::new(py, s));
    }
    Ok(out)
}

#[pyfunction]
fn rs_parity_check(
    payload_len: usize,
    data_shards: usize,
    parity_shards: usize,
    shards: Vec<&[u8]>,
) -> PyResult<bool> {
    use crate::rs::layout::Layout;
    use crate::rs::verify::parity_check;

    if shards.len() != data_shards + parity_shards {
        return Err(PyValueError::new_err("shards.len() must be data_shards + parity_shards"));
    }
    if shards.is_empty() {
        return Err(PyValueError::new_err("shards must be non-empty"));
    }
    let shard_len = shards[0].len();
    if !shards.iter().all(|s| s.len() == shard_len) {
        return Err(PyValueError::new_err("all shards must have equal length"));
    }

    // Build a layout that matches the shard geometry for given payload_len.
    let layout = Layout::with_default_align(payload_len, data_shards, parity_shards)
        .map_err(to_py_err)?;
    let owned: Vec<Vec<u8>> = shards.into_iter().map(|s| s.to_vec()).collect();
    parity_check(&layout, &owned).map_err(to_py_err)
}

#[pyfunction]
fn rs_parity_mismatch_positions(
    payload_len: usize,
    data_shards: usize,
    parity_shards: usize,
    shards: Vec<&[u8]>,
) -> PyResult<Vec<usize>> {
    use crate::rs::layout::Layout;
    use crate::rs::verify::parity_mismatch_positions;

    if shards.len() != data_shards + parity_shards {
        return Err(PyValueError::new_err("shards.len() must be data_shards + parity_shards"));
    }
    let shard_len = shards[0].len();
    if !shards.iter().all(|s| s.len() == shard_len) {
        return Err(PyValueError::new_err("all shards must have equal length"));
    }

    let layout = Layout::with_default_align(payload_len, data_shards, parity_shards)
        .map_err(to_py_err)?;
    let owned: Vec<Vec<u8>> = shards.into_iter().map(|s| s.to_vec()).collect();
    parity_mismatch_positions(&layout, &owned).map_err(to_py_err)
}

#[pymodule]
fn rs(py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(rs_encode, m)?)?;
    m.add_function(wrap_pyfunction!(rs_parity_check, m)?)?;
    m.add_function(wrap_pyfunction!(rs_parity_mismatch_positions, m)?)?;
    m.add(
        "__doc__",
        "Reed–Solomon helpers: encode data->parity, parity check & diagnostics",
    )?;
    let all = vec![
        "rs_encode",
        "rs_parity_check",
        "rs_parity_mismatch_positions",
    ];
    m.add("__all__", all)?;
    Ok(())
}

/* ------------------------------- bench -------------------------------- */

#[pyfunction]
fn bench_blake3<'py>(py: Python<'py>, data_len: usize, iters: usize) -> PyResult<&'py PyDict> {
    if data_len == 0 || iters == 0 {
        return Err(PyValueError::new_err("data_len and iters must be > 0"));
    }
    use crate::hash::blake3 as b3;
    use std::time::Instant;

    let mut buf = vec![0u8; data_len];
    // Fill with a simple pattern to avoid easy optimization illusions.
    for (i, b) in buf.iter_mut().enumerate() {
        *b = (i as u8).wrapping_mul(31).wrapping_add(7);
    }

    let t0 = Instant::now();
    let mut last = [0u8; 32];
    for _ in 0..iters {
        last.copy_from_slice(&b3::hash(&buf));
    }
    let dt = t0.elapsed();
    let bytes = (data_len as u128) * (iters as u128);
    let secs = dt.as_secs_f64();
    let mib_per_s = (bytes as f64) / (1024.0 * 1024.0) / secs;

    let d = PyDict::new(py);
    d.set_item("bytes", bytes)?;
    d.set_item("seconds", secs)?;
    d.set_item("MiB_per_s", mib_per_s)?;
    d.set_item("last_digest_hex", hex::encode(last))?;
    Ok(d)
}

#[pymodule]
fn bench(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(bench_blake3, m)?)?;
    m.add("__doc__", "Tiny best-effort wall-clock micro-benchmarks")?;
    m.add("__all__", vec!["bench_blake3"])?;
    Ok(())
}

/* ------------------------------ top-level ----------------------------- */

fn to_py_err(e: impl Into<NativeError>) -> PyErr {
    PyRuntimeError::new_err(e.into().to_string())
}

#[pymodule]
pub fn animica_native(py: Python, m: &PyModule) -> PyResult<()> {
    // Submodules
    let utils_mod = PyModule::new(py, "utils")?;
    utils(py, utils_mod)?;
    m.add_submodule(utils_mod)?;

    let hash_mod = PyModule::new(py, "hash")?;
    hash(py, hash_mod)?;
    m.add_submodule(hash_mod)?;

    let nmt_mod = PyModule::new(py, "nmt")?;
    nmt(py, nmt_mod)?;
    m.add_submodule(nmt_mod)?;

    let rs_mod = PyModule::new(py, "rs")?;
    rs(py, rs_mod)?;
    m.add_submodule(rs_mod)?;

    let bench_mod = PyModule::new(py, "bench")?;
    bench(py, bench_mod)?;
    m.add_submodule(bench_mod)?;

    // Top-level docstring & metadata
    m.add(
        "__doc__",
        "Animica native fast-paths (hash/NMT/RS) exposed to Python.",
    )?;
    m.add("__all__", vec!["utils", "hash", "nmt", "rs", "bench"])?;
    Ok(())
}
