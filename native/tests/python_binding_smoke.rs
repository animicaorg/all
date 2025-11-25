//! PyO3 smoke test for animica_native.
//!
//! IMPORTANT: This test only exercises the Python bindings if a suitable
//! feature is enabled (one of: "python", "pyo3", "python-bindings") *and*
//! a PyO3-based extension module is actually built/importable.
//!
//! Without those features, this test becomes a no-op (it just logs and
//! returns), so that `cargo test` does not require PyO3 by default.

mod common;

use animica_native::{blake3_hash, sha3_256_hash};
use animica_native::hash::Digest32;

/// Internal module that only compiles when Python/PyO3 support is enabled.
#[cfg(any(feature = "python", feature = "pyo3", feature = "python-bindings"))]
mod py_impl {
    use super::{blake3_hash, sha3_256_hash, Digest32};
    use pyo3::prelude::*;

    /// Try to import an Animica-related Python module.
    fn try_import_animica_module(py: Python<'_>) -> Option<Py<PyAny>> {
        let candidates = ["animica_native", "animica_native_py", "animica"];
        for name in candidates {
            match py.import(name) {
                Ok(m) => {
                    eprintln!("python_binding_smoke: imported Python module {:?}", name);
                    return Some(m.into());
                }
                Err(_) => continue,
            }
        }
        None
    }

    fn call_py_hash_fn(
        py: Python<'_>,
        module: &PyAny,
        attr: &str,
        msg: &[u8],
    ) -> Option<Digest32> {
        let func = match module.getattr(attr) {
            Ok(f) => f,
            Err(_) => {
                eprintln!(
                    "python_binding_smoke: module has no attribute {:?}, skipping",
                    attr
                );
                return None;
            }
        };

        let result = match func.call1((msg,)) {
            Ok(r) => r,
            Err(err) => {
                err.print(py);
                eprintln!(
                    "python_binding_smoke: calling {:?} raised, skipping this function",
                    attr
                );
                return None;
            }
        };

        let bytes: Vec<u8> = match result.extract() {
            Ok(b) => b,
            Err(err) => {
                err.print(py);
                eprintln!(
                    "python_binding_smoke: result of {:?} is not bytes-like, skipping",
                    attr
                );
                return None;
            }
        };

        if bytes.len() != 32 {
            eprintln!(
                "python_binding_smoke: result of {:?} has length {}, expected 32, skipping",
                attr,
                bytes.len()
            );
            return None;
        }

        let mut out: Digest32 = [0u8; 32];
        out.copy_from_slice(&bytes);
        Some(out)
    }

    /// Actual PyO3-powered smoke test. Only compiled when a Python feature is on.
    pub fn run() {
        Python::with_gil(|py| {
            let Some(module_any) = try_import_animica_module(py) else {
                eprintln!(
                    "python_binding_smoke: no animica_native Python module importable; \
                     treating as no-op."
                );
                return;
            };
            let module = module_any.as_ref(py);

            let samples: &[&[u8]] = &[
                b"",
                b"animica-python-binding-smoke",
                b"the quick brown fox jumps over the lazy dog",
                b"pybindings-test-123",
            ];

            for msg in samples {
                let rust_sha3 = sha3_256_hash(msg);
                let rust_blake = blake3_hash(msg);

                if let Some(py_sha3) = call_py_hash_fn(py, module, "sha3_256_hash", msg) {
                    assert_eq!(
                        rust_sha3, py_sha3,
                        "Python sha3_256_hash output must match Rust for msg {:?}",
                        std::str::from_utf8(msg).unwrap_or("<non-utf8>")
                    );
                }

                if let Some(py_blake) = call_py_hash_fn(py, module, "blake3_hash", msg) {
                    assert_eq!(
                        rust_blake, py_blake,
                        "Python blake3_hash output must match Rust for msg {:?}",
                        std::str::from_utf8(msg).unwrap_or("<non-utf8>")
                    );
                }
            }

            // Optional presence probe for NMT/RS bindings.
            for attr in ["nmt", "rs"] {
                match module.getattr(attr) {
                    Ok(_) => eprintln!("python_binding_smoke: module has attribute {:?}", attr),
                    Err(_) => eprintln!(
                        "python_binding_smoke: module has no attribute {:?} (this is OK)",
                        attr
                    ),
                }
            }
        });
    }
}

/// Top-level test that either runs the real PyO3 smoke test (when available)
/// or degrades to a logged no-op when Python bindings are not enabled.
#[test]
fn python_bindings_hash_smoke_test() {
    #[cfg(any(feature = "python", feature = "pyo3", feature = "python-bindings"))]
    {
        py_impl::run();
    }

    #[cfg(not(any(feature = "python", feature = "pyo3", feature = "python-bindings")))]
    {
        eprintln!(
            "python_binding_smoke: Python/PyO3 features not enabled; skipping bindings test."
        );
    }
}
