//! Zero-copy Python <-> Rust buffer helpers.
//!
//! Goals:
//! - Borrow Python buffers (bytes/bytearray/memoryview/NumPy, etc.) as `&[u8]`
//!   or `&mut [u8]` **without copying**, using the Python buffer protocol.
//! - Provide fast-paths for common types (PyBytes, PyByteArray).
//! - Create Python `memoryview` objects that alias existing Rust slices
//!   (read-only or mutable) when the lifetime is limited to the GIL borrow.
//!
//! Caveats:
//! - Returning `PyBytes` necessarily copies (CPython allocates an owned blob).
//!   Use `memoryview_from_slice(_mut)` to expose Rust memory **without** copies.
//! - Mutable borrows require the underlying Python object to be writable and
//!   C-contiguous; otherwise a `ValueError` is raised.
//!
//! These utilities are intentionally small and focused; wrap them in higher-level
//! APIs when you need type/shape metadata or structured views.

use pyo3::buffer::PyBuffer;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyAny, PyByteArray, PyBytes, PyMemoryView};

#[inline]
fn py_err<S: AsRef<str>>(s: S) -> PyErr {
    PyValueError::new_err(s.as_ref().to_owned())
}

/// Borrow a Python bytes-like object as an immutable `&[u8]` **without copying**.
///
/// Accepts: `bytes`, `bytearray`, `memoryview`, and any object implementing
/// the buffer protocol (e.g., NumPy array with `dtype=uint8` and C-contiguous).
///
/// Errors if the exported buffer isn't C-contiguous or its itemsize != 1.
pub fn ro_slice<'py>(obj: &'py PyAny) -> PyResult<&'py [u8]> {
    // Fast paths first.
    if let Ok(b) = obj.downcast::<PyBytes>() {
        return Ok(b.as_bytes());
    }
    if let Ok(ba) = obj.downcast::<PyByteArray>() {
        // Safe: CPython exports a contiguous u8 region for bytearray.
        return Ok(unsafe { ba.as_bytes() });
    }

    // Generic buffer protocol.
    let buf = PyBuffer::<u8>::get(obj)?;
    if !buf.is_c_contiguous() {
        return Err(py_err("buffer must be C-contiguous for zero-copy view"));
    }
    // Safety: PyO3 guarantees lifetime-tied & slice for C-contiguous u8 buffers.
    unsafe { buf.as_slice() }
}

/// Borrow a Python bytes-like object as a mutable `&mut [u8]` **without copying**.
///
/// Accepts: `bytearray`, writable `memoryview`, and any writable buffer
/// (e.g., a mutable NumPy array with `dtype=uint8`, C-contiguous).
///
/// Errors if the buffer is read-only, non-contiguous, or not `u8`-itemized.
pub fn rw_slice<'py>(obj: &'py PyAny) -> PyResult<&'py mut [u8]> {
    // Only bytearray exposes a direct mutable slice in the std types.
    if let Ok(ba) = obj.downcast::<PyByteArray>() {
        // Safety: CPython exports a contiguous u8 region for bytearray.
        return Ok(unsafe { ba.as_bytes_mut() });
    }

    // Generic buffer protocol.
    let mut buf = PyBuffer::<u8>::get(obj)?;
    if buf.readonly() {
        return Err(py_err("buffer is read-only; need a writable object (e.g., bytearray)"));
    }
    if !buf.is_c_contiguous() {
        return Err(py_err("buffer must be C-contiguous for zero-copy mutable view"));
    }
    // Safety: PyO3 guarantees lifetime-tied &mut slice for writable, contiguous u8 buffers.
    unsafe { buf.as_slice_mut() }
}

/// Create a Python `memoryview` that aliases a Rust **read-only** slice
/// **without copying**. The resulting memoryview is valid only for the
/// duration of the active GIL borrow `py`.
///
/// Note: The caller must ensure the Rust slice outlives the memoryview usage.
pub fn memoryview_from_slice<'py>(py: Python<'py>, slice: &'py [u8]) -> PyResult<&'py PyMemoryView> {
    // SAFETY contract is enforced by PyO3: returned memoryview borrows `slice` for 'py.
    Ok(PyMemoryView::from_slice(py, slice))
}

/// Create a Python `memoryview` that aliases a Rust **mutable** slice
/// **without copying**. The resulting memoryview is valid only for the
/// duration of the active GIL borrow `py`.
///
/// Note: The caller must ensure exclusive access to the slice while the
/// memoryview is in use on the Python side.
pub fn memoryview_from_mut_slice<'py>(
    py: Python<'py>,
    slice: &'py mut [u8],
) -> PyResult<&'py PyMemoryView> {
    // SAFETY contract is enforced by PyO3: returned memoryview borrows `slice` for 'py.
    Ok(PyMemoryView::from_mut_slice(py, slice))
}

/// Utility: Run a closure with an immutable zero-copy `&[u8]` borrowed from `obj`.
///
/// This helps keep borrows scoped and avoids accidental lifetime leaks in
/// more complex code paths.
pub fn with_ro_slice<'py, R, F>(obj: &'py PyAny, f: F) -> PyResult<R>
where
    F: FnOnce(&'py [u8]) -> R,
{
    let s = ro_slice(obj)?;
    Ok(f(s))
}

/// Utility: Run a closure with a mutable zero-copy `&mut [u8]` borrowed from `obj`.
pub fn with_rw_slice<'py, R, F>(obj: &'py PyAny, f: F) -> PyResult<R>
where
    F: FnOnce(&'py mut [u8]) -> R,
{
    let s = rw_slice(obj)?;
    Ok(f(s))
}

/// Fallback: Copy a Rust slice into a Python `bytes` object (owned by Python).
/// Use only when a *copy* is acceptable or necessary.
pub fn to_py_bytes<'py>(py: Python<'py>, data: &[u8]) -> &'py PyBytes {
    PyBytes::new(py, data)
}

/* ------------------------------- tests ------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::types::PyByteArray;

    #[test]
    fn roundtrip_ro_and_rw() {
        Python::with_gil(|py| {
            let ba = PyByteArray::new(py, 8);
            {
                // write via rw_slice
                let s = rw_slice(ba.as_any()).unwrap();
                for (i, b) in s.iter_mut().enumerate() {
                    *b = (i as u8) ^ 0xAA;
                }
            }
            // read via ro_slice (no copy)
            let r = ro_slice(ba.as_any()).unwrap();
            assert_eq!(r, &[0xAA, 0xAB, 0xA8, 0xA9, 0xAE, 0xAF, 0xAC, 0xAD]);
        });
    }

    #[test]
    fn memview_from_rust_slices() {
        Python::with_gil(|py| {
            let mut buf = [1u8, 2, 3, 4];
            let mv_ro = memoryview_from_slice(py, &buf).unwrap();
            let mv_rw = memoryview_from_mut_slice(py, &mut buf).unwrap();

            // Accessing the memoryview should see the same underlying data.
            let len_ro: usize = mv_ro.len().unwrap();
            let len_rw: usize = mv_rw.len().unwrap();
            assert_eq!(len_ro, 4);
            assert_eq!(len_rw, 4);
        });
    }
}
