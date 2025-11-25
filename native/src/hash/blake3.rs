//! High-performance BLAKE3 helpers with optional parallel updates.
//!
//! This module complements `hash::mod` by exposing tuned paths that
//! leverage the `blake3` crate's internal tree hashing and (optionally)
//! its Rayon-powered parallel update routine when the `rayon` feature
//! is enabled for **this** crate (and propagated to `blake3`).
//!
//! ## Notes
//! - We keep all domain-separation (DS) logic centralized: callers must
//!   provide a `DsTag`, and we construct the hasher with
//!   `Hasher::new_derive_key(tag.context())`.
//! - Parallel update will be used **only** when the `rayon` feature is
//!   compiled in. Otherwise, we fall back to the standard `update` path.
//! - BLAKE3 is already SIMD-optimized; enabling our crate feature
//!   `simd` will allow the upstream crate to use platform intrinsics.
//!
//! ## When to use this
//! - Large, contiguous buffers (tens of KB or larger).
//! - Multiple large chunks you don't want to concatenate in memory.
//!
//! For small inputs, the standard one-shot helpers in `hash::mod` are
//! usually faster due to lower overhead.

use super::{Digest32, DsTag};
use blake3::Hasher;

/// Minimum size (in bytes) to consider using the parallel update path.
///
/// This constant is advisory: when the `rayon` feature is *not* enabled,
/// we always use the scalar `update` path regardless of size.
const PAR_THRESHOLD: usize = 256 * 1024; // 256 KiB

/// One-shot BLAKE3-256 over a single contiguous buffer, with DS tag.
///
/// Uses `update_rayon` when available and the buffer length exceeds
/// `PAR_THRESHOLD`; otherwise falls back to `update`.
#[inline]
pub fn blake3_256_parallel_ds(tag: DsTag, data: &[u8]) -> Digest32 {
    let mut h = Hasher::new_derive_key(tag.context());

    // If the feature is available and the buffer is large, use the
    // parallel tree hashing entrypoint. Otherwise, use the scalar path.
    #[cfg(feature = "rayon")]
    {
        if data.len() >= PAR_THRESHOLD {
            // Safety: `update_rayon` exists in blake3 with feature "rayon".
            h.update_rayon(data);
        } else {
            h.update(data);
        }
    }

    #[cfg(not(feature = "rayon"))]
    {
        let _ = PAR_THRESHOLD; // silence unused warning
        h.update(data);
    }

    *h.finalize().as_bytes()
}

/// Hash several chunks (treated as if concatenated) under a DS tag.
///
/// Each chunk is fed in-order. When `rayon` is enabled, **each** chunk
/// that exceeds `PAR_THRESHOLD` is fed via `update_rayon`; smaller ones
/// use the normal `update`. This avoids extra allocations/concats.
///
/// ### Correctness
/// BLAKE3's tree mode ensures that sequential `update` calls produce the
/// same digest as hashing the full concatenation once. Mixing `update`
/// and `update_rayon` is safe and deterministic—both are canonical.
///
/// ### Performance tip
/// Try to pass fewer, larger chunks when possible; very many tiny chunks
/// will be dominated by call overhead regardless of parallelism.
pub fn blake3_256_parallel_many<'a, I>(tag: DsTag, parts: I) -> Digest32
where
    I: IntoIterator<Item = &'a [u8]>,
{
    let mut h = Hasher::new_derive_key(tag.context());

    #[cfg(feature = "rayon")]
    {
        for part in parts {
            if part.len() >= PAR_THRESHOLD {
                h.update_rayon(part);
            } else {
                h.update(part);
            }
        }
        return *h.finalize().as_bytes();
    }

    #[cfg(not(feature = "rayon"))]
    {
        for part in parts {
            h.update(part);
        }
        return *h.finalize().as_bytes();
    }
}

/// Stream a reader in fixed-size blocks and hash under a DS tag.
///
/// This does *not* currently parallelize I/O (we avoid complexity and
/// extra buffering here). It benefits from BLAKE3's SIMD path and keeps
/// memory constant.
///
/// Returns `io::Result<Digest32>` for ergonomic use in tooling.
pub fn blake3_256_parallel_reader<R: std::io::Read>(
    tag: DsTag,
    mut reader: R,
) -> std::io::Result<Digest32> {
    use std::io::Read;

    // 1 MiB block; large enough to benefit SIMD and amortize syscalls.
    const BUF: usize = 1 << 20;

    let mut h = Hasher::new_derive_key(tag.context());
    let mut buf = vec![0u8; BUF];

    loop {
        let n = reader.read(&mut buf)?;
        if n == 0 {
            break;
        }

        let chunk = &buf[..n];

        #[cfg(feature = "rayon")]
        {
            if n >= PAR_THRESHOLD {
                h.update_rayon(chunk);
            } else {
                h.update(chunk);
            }
        }

        #[cfg(not(feature = "rayon"))]
        {
            let _ = PAR_THRESHOLD;
            h.update(chunk);
        }
    }

    Ok(*h.finalize().as_bytes())
}

/* ---------------------------------- Python ---------------------------------- */

#[cfg(feature = "python")]
mod py {
    use super::*;
    use pyo3::prelude::*;
    use pyo3::types::PyBytes;

    fn parse_tag(tag: &str) -> PyResult<DsTag> {
        match tag {
            "generic" => Ok(DsTag::Generic),
            "tx" => Ok(DsTag::Tx),
            "header" => Ok(DsTag::Header),
            "block_body" => Ok(DsTag::BlockBody),
            "proof_envelope" => Ok(DsTag::ProofEnvelope),
            "vm_code" => Ok(DsTag::VmCode),
            "vm_state" => Ok(DsTag::VmState),
            "p2p" => Ok(DsTag::P2p),
            "da_blob" => Ok(DsTag::DaBlob),
            "nmt" => Ok(DsTag::Nmt),
            "randomness" => Ok(DsTag::Randomness),
            "aicf" => Ok(DsTag::Aicf),
            "quantum" => Ok(DsTag::Quantum),
            "explorer" => Ok(DsTag::Explorer),
            "zk" => Ok(DsTag::Zk),
            "capability" => Ok(DsTag::Capability),
            _ => Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "unknown DsTag: {tag}"
            ))),
        }
    }

    #[pyfunction]
    pub fn blake3_256_parallel(py: Python<'_>, tag: &str, data: &[u8]) -> PyResult<PyObject> {
        let t = parse_tag(tag)?;
        let out = super::blake3_256_parallel_ds(t, data);
        Ok(PyBytes::new(py, &out).into())
    }

    #[pyfunction]
    pub fn blake3_256_parallel_hex(_py: Python<'_>, tag: &str, data: &[u8]) -> PyResult<String> {
        let t = parse_tag(tag)?;
        let out = super::blake3_256_parallel_ds(t, data);
        Ok(to_lower_hex(&out))
    }

    pub fn register(m: &Bound<PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(blake3_256_parallel, m)?)?;
        m.add_function(wrap_pyfunction!(blake3_256_parallel_hex, m)?)?;
        Ok(())
    }

    #[inline]
    fn to_lower_hex(bytes: &[u8]) -> String {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let mut s = String::with_capacity(bytes.len() * 2);
        for &b in bytes {
            s.push(HEX[(b >> 4) as usize] as char);
            s.push(HEX[(b & 0x0f) as usize] as char);
        }
        s
    }
}

/* ---------------------------------- Tests ----------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hash::{hash_many, hash_ds, DsTag as Tag};

    #[test]
    fn parallel_equals_scalar_one_shot() {
        let msg = vec![0u8; 2_000_000]; // 2 MiB, crosses threshold
        let a = blake3_256_parallel_ds(Tag::VmCode, &msg);
        let b = hash_ds(Tag::VmCode, &msg);
        assert_eq!(a, b, "parallel path must equal scalar one-shot");
    }

    #[test]
    fn parallel_many_equals_concat() {
        let a = blake3_256_parallel_many(Tag::Header, [b"abc".as_ref(), b"def"].into_iter());
        let b = hash_ds(Tag::Header, b"abcdef");
        assert_eq!(a, b);
    }

    #[test]
    fn reader_matches_direct() {
        let data = vec![42u8; 1_500_000]; // 1.5 MiB
        let via_reader = blake3_256_parallel_reader(Tag::DaBlob, &data[..]).unwrap();
        let direct = hash_ds(Tag::DaBlob, &data);
        assert_eq!(via_reader, direct);
    }
}
