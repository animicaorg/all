//! Keccak-f1600 (Keccak-256) helpers with an optional C fast path.
//!
//! This module provides a streaming and multi-part API around
//! [`tiny_keccak`] to compute **Keccak-256** (Ethereum's `keccak256`),
//! plus an FFI hook for an accelerated C implementation when the
//! `c_keccak` feature is enabled for this crate.
//!
//! ### Why Keccak-256 (not SHA3-256)?
//! Keccak-256 (Ethereum-style) differs from NIST SHA3-256 in padding.
//! Many blockchain ecosystems (incl. EVM) use Keccak-256; we do the
//! same for compatibility and tooling ergonomics.
//!
//! ### Domain Separation (DS)
//! We use `DsTag` to prefix inputs with a stable, explicit context.
//! For BLAKE3 we leverage `derive_key(context)`. For Keccak, there is
//! no keyed mode, so we **absorb a delimiter-encoded DS prelude**:
//!
//! ```text
//! "animica.ds.keccak:" || context || 0x00
//! ```
//!
//! This is simple, non-ambiguous, and stable across versions.
//!
//! ### C fast path
//! If `--features c_keccak` is enabled, we call a single-shot FFI
//! function `animica_keccak256` for whole-buffer hashing. Streaming
//! and multi-part still go through `tiny_keccak` (they're already
//! fast and avoid excessive buffering). The C symbol is expected to
//! be provided by our build script (`build.rs`) via a bundled
//! implementation (e.g. a small file wrapping a tuned Keccak-f1600).
//!
//! ### API
//! - `keccak256(data)` — raw Keccak-256
//! - `keccak256_many(parts)` — treat parts as concatenated
//! - `keccak256_reader(reader)` — stream from `Read`
//! - `keccak256_ds(tag, data)` — DS-tagged one-shot
//! - `keccak256_many_ds(tag, parts)` — DS-tagged multi-part
//! - `keccak256_reader_ds(tag, reader)` — DS-tagged streaming
//!
//! ### Python bindings (feature `python`)
//! - `keccak256(tag: str, data: bytes) -> bytes`
//! - `keccak256_hex(tag: str, data: bytes) -> str`

use super::{Digest32, DsTag};

#[cfg(feature = "c_keccak")]
mod cfast {
    // Safety: implemented by our build script linking a small C file that exports this symbol.
    extern "C" {
        pub fn animica_keccak256(input: *const u8, len: usize, out32: *mut u8);
    }

    #[inline]
    pub fn one_shot(data: &[u8]) -> [u8; 32] {
        let mut out = [0u8; 32];
        unsafe { animica_keccak256(data.as_ptr(), data.len(), out.as_mut_ptr()) }
        out
    }
}

#[inline]
fn ds_prefix_bytes(tag: DsTag) -> (&'static [u8], &'static [u8], u8) {
    // Split the constant so the compiler can fold it nicely; also keeps the
    // literal discoverable in binaries while remaining compact.
    (b"animica.ds.", b"keccak:", 0u8)
}

#[inline]
fn absorb_ds_prefix(k: &mut tiny_keccak::Keccak, tag: DsTag) {
    use tiny_keccak::Hasher;
    let (a, b, z) = ds_prefix_bytes(tag);
    k.update(a);
    k.update(b);
    k.update(tag.context().as_bytes());
    k.update(&[z]); // delimiter to avoid suffix ambiguity
}

/* ---------------------------- Keccak core helpers --------------------------- */

/// One-shot Keccak-256 of a single buffer (no DS).
#[inline]
pub fn keccak256(data: &[u8]) -> Digest32 {
    #[cfg(feature = "c_keccak")]
    {
        return cfast::one_shot(data);
    }

    #[cfg(not(feature = "c_keccak"))]
    {
        use tiny_keccak::{Hasher, Keccak};
        let mut k = Keccak::v256();
        k.update(data);
        let mut out = [0u8; 32];
        k.finalize(&mut out);
        out
    }
}

/// Keccak-256 over multiple parts, treated as if concatenated (no DS).
pub fn keccak256_many<'a, I>(parts: I) -> Digest32
where
    I: IntoIterator<Item = &'a [u8]>,
{
    use tiny_keccak::{Hasher, Keccak};
    let mut k = Keccak::v256();
    for p in parts {
        k.update(p);
    }
    let mut out = [0u8; 32];
    k.finalize(&mut out);
    out
}

/// Stream Keccak-256 from a reader (no DS).
pub fn keccak256_reader<R: std::io::Read>(mut reader: R) -> std::io::Result<Digest32> {
    use std::io::Read;
    use tiny_keccak::{Hasher, Keccak};

    const BUF: usize = 1 << 20; // 1 MiB
    let mut k = Keccak::v256();
    let mut buf = vec![0u8; BUF];

    loop {
        let n = reader.read(&mut buf)?;
        if n == 0 {
            break;
        }
        k.update(&buf[..n]);
    }

    let mut out = [0u8; 32];
    k.finalize(&mut out);
    Ok(out)
}

/* ------------------------------ DS-tagged API ------------------------------ */

/// Keccak-256 with DS prefix.
pub fn keccak256_ds(tag: DsTag, data: &[u8]) -> Digest32 {
    // If the C fast path is on, concatenate DS prelude + data into one buffer
    // to take advantage of the single-shot symbol.
    #[cfg(feature = "c_keccak")]
    {
        let (a, b, z) = ds_prefix_bytes(tag);
        let ctx = tag.context().as_bytes();
        let mut buf = Vec::with_capacity(a.len() + b.len() + ctx.len() + 1 + data.len());
        buf.extend_from_slice(a);
        buf.extend_from_slice(b);
        buf.extend_from_slice(ctx);
        buf.push(z);
        buf.extend_from_slice(data);
        return cfast::one_shot(&buf);
    }

    #[cfg(not(feature = "c_keccak"))]
    {
        use tiny_keccak::{Hasher, Keccak};
        let mut k = Keccak::v256();
        absorb_ds_prefix(&mut k, tag);
        k.update(data);
        let mut out = [0u8; 32];
        k.finalize(&mut out);
        out
    }
}

/// DS-tagged Keccak-256 over multiple parts.
pub fn keccak256_many_ds<'a, I>(tag: DsTag, parts: I) -> Digest32
where
    I: IntoIterator<Item = &'a [u8]>,
{
    use tiny_keccak::{Hasher, Keccak};
    let mut k = Keccak::v256();
    absorb_ds_prefix(&mut k, tag);
    for p in parts {
        k.update(p);
    }
    let mut out = [0u8; 32];
    k.finalize(&mut out);
    out
}

/// DS-tagged streaming Keccak-256 from a reader.
pub fn keccak256_reader_ds<R: std::io::Read>(
    tag: DsTag,
    mut reader: R,
) -> std::io::Result<Digest32> {
    use std::io::Read;
    use tiny_keccak::{Hasher, Keccak};

    const BUF: usize = 1 << 20; // 1 MiB
    let mut k = Keccak::v256();
    absorb_ds_prefix(&mut k, tag);

    let mut buf = vec![0u8; BUF];
    loop {
        let n = reader.read(&mut buf)?;
        if n == 0 {
            break;
        }
        k.update(&buf[..n]);
    }

    let mut out = [0u8; 32];
    k.finalize(&mut out);
    Ok(out)
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

    /// `keccak256(tag: str, data: bytes) -> bytes`
    #[pyfunction]
    pub fn keccak256(py: Python<'_>, tag: &str, data: &[u8]) -> PyResult<PyObject> {
        let t = parse_tag(tag)?;
        let out = super::keccak256_ds(t, data);
        Ok(PyBytes::new(py, &out).into())
    }

    /// `keccak256_hex(tag: str, data: bytes) -> str`
    #[pyfunction]
    pub fn keccak256_hex(_py: Python<'_>, tag: &str, data: &[u8]) -> PyResult<String> {
        let t = parse_tag(tag)?;
        let out = super::keccak256_ds(t, data);
        Ok(to_lower_hex(&out))
    }

    pub fn register(m: &Bound<PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(keccak256, m)?)?;
        m.add_function(wrap_pyfunction!(keccak256_hex, m)?)?;
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
    use crate::hash::DsTag as Tag;

    #[test]
    fn keccak_empty_matches_vector() {
        // Known Keccak-256("") from Ethereum tooling:
        // c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470
        let got = super::keccak256(&[]);
        let expect = hex_literal::hex!(
            "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
        );
        assert_eq!(got, expect);
    }

    #[test]
    fn many_equals_concat() {
        let a = super::keccak256_many([b"ab".as_ref(), b"c"].into_iter());
        let b = super::keccak256(b"abc");
        assert_eq!(a, b);
    }

    #[test]
    fn ds_stream_equals_ds_direct() {
        let data = vec![42u8; 1_100_000]; // > 1 MiB
        let via_reader = super::keccak256_reader_ds(Tag::DaBlob, &data[..]).unwrap();
        let via_direct = super::keccak256_ds(Tag::DaBlob, &data);
        assert_eq!(via_reader, via_direct);
    }

    #[test]
    fn ds_changes_digest() {
        let d0 = super::keccak256(b"hello");
        let d1 = super::keccak256_ds(Tag::Generic, b"hello");
        assert_ne!(d0, d1, "DS prefix must affect the digest");
    }
}
