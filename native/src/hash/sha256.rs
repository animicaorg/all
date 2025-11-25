//! SHA-256 helpers with pluggable backend (`sha2` by default, `ring` optional).
//!
//! This module provides a streaming and multi-part API to compute **SHA-256**,
//! plus Animica's domain-separation (DS) prelude helpers to make context
//! explicit and stable across versions.
//!
//! ### Backends
//! - Default: [`sha2::Sha256`] (pure Rust, portable, fast).
//! - Optional: [`ring::digest::SHA256`] if the crate is built with the `ring`
//!   feature enabled. Some platforms benefit from hand-optimized assembly.
//!
//! Build-time selection is transparent to callers. Both APIs produce identical
//! outputs.
//!
//! ### Domain Separation (DS)
//! For SHA-256 we absorb a delimiter-encoded DS prelude before the user data:
//!
//! ```text
//! "animica.ds.sha256:" || context || 0x00
//! ```
//!
//! ### API
//! - `sha256(data)` — raw SHA-256
//! - `sha256_many(parts)` — treat parts as concatenated
//! - `sha256_reader(reader)` — stream from `Read`
//! - `sha256_ds(tag, data)` — DS-tagged one-shot
//! - `sha256_many_ds(tag, parts)` — DS-tagged multi-part
//! - `sha256_reader_ds(tag, reader)` — DS-tagged streaming
//!
//! ### Python bindings (feature `python`)
//! - `sha256(tag: str, data: bytes) -> bytes`
//! - `sha256_hex(tag: str, data: bytes) -> str`

use super::{Digest32, DsTag};

/* ------------------------------- DS utilities ------------------------------- */

#[inline]
fn ds_prefix_bytes(_tag: DsTag) -> (&'static [u8], &'static [u8], u8) {
    (b"animica.ds.", b"sha256:", 0u8)
}

#[cfg(not(feature = "ring"))]
mod back {
    use sha2::{Digest as _, Sha256};

    #[inline]
    pub fn one_shot(data: &[u8]) -> [u8; 32] {
        let mut h = Sha256::new();
        h.update(data);
        let out = h.finalize();
        let mut o = [0u8; 32];
        o.copy_from_slice(&out);
        o
    }

    pub struct Ctx(Sha256);

    impl Ctx {
        #[inline]
        pub fn new() -> Self {
            Self(Sha256::new())
        }
        #[inline]
        pub fn update(&mut self, data: &[u8]) {
            self.0.update(data)
        }
        #[inline]
        pub fn finalize(self) -> [u8; 32] {
            let out = self.0.finalize();
            let mut o = [0u8; 32];
            o.copy_from_slice(&out);
            o
        }
    }
}

#[cfg(feature = "ring")]
mod back {
    use ring::digest::{digest, Context, SHA256};

    #[inline]
    pub fn one_shot(data: &[u8]) -> [u8; 32] {
        let d = digest(&SHA256, data);
        let mut o = [0u8; 32];
        o.copy_from_slice(d.as_ref());
        o
    }

    pub struct Ctx(Context);

    impl Ctx {
        #[inline]
        pub fn new() -> Self {
            Self(Context::new(&SHA256))
        }
        #[inline]
        pub fn update(&mut self, data: &[u8]) {
            self.0.update(data)
        }
        #[inline]
        pub fn finalize(self) -> [u8; 32] {
            let d = self.0.finish();
            let mut o = [0u8; 32];
            o.copy_from_slice(d.as_ref());
            o
        }
    }
}

/* ------------------------------ Core functions ------------------------------ */

/// One-shot SHA-256 of a single buffer (no DS).
#[inline]
pub fn sha256(data: &[u8]) -> Digest32 {
    back::one_shot(data)
}

/// SHA-256 over multiple parts, treated as if concatenated (no DS).
pub fn sha256_many<'a, I>(parts: I) -> Digest32
where
    I: IntoIterator<Item = &'a [u8]>,
{
    let mut ctx = back::Ctx::new();
    for p in parts {
        ctx.update(p);
    }
    ctx.finalize()
}

/// Stream SHA-256 from a reader (no DS).
pub fn sha256_reader<R: std::io::Read>(mut reader: R) -> std::io::Result<Digest32> {
    use std::io::Read;
    const BUF: usize = 1 << 20; // 1 MiB
    let mut ctx = back::Ctx::new();
    let mut buf = vec![0u8; BUF];

    loop {
        let n = reader.read(&mut buf)?;
        if n == 0 {
            break;
        }
        ctx.update(&buf[..n]);
    }
    Ok(ctx.finalize())
}

/* ------------------------------ DS-tagged API ------------------------------ */

#[inline]
fn absorb_ds_prefix(ctx: &mut back::Ctx, tag: DsTag) {
    let (a, b, z) = ds_prefix_bytes(tag);
    ctx.update(a);
    ctx.update(b);
    ctx.update(tag.context().as_bytes());
    ctx.update(&[z]);
}

/// SHA-256 with DS prefix.
pub fn sha256_ds(tag: DsTag, data: &[u8]) -> Digest32 {
    let mut ctx = back::Ctx::new();
    absorb_ds_prefix(&mut ctx, tag);
    ctx.update(data);
    ctx.finalize()
}

/// DS-tagged SHA-256 over multiple parts.
pub fn sha256_many_ds<'a, I>(tag: DsTag, parts: I) -> Digest32
where
    I: IntoIterator<Item = &'a [u8]>,
{
    let mut ctx = back::Ctx::new();
    absorb_ds_prefix(&mut ctx, tag);
    for p in parts {
        ctx.update(p);
    }
    ctx.finalize()
}

/// DS-tagged streaming SHA-256 from a reader.
pub fn sha256_reader_ds<R: std::io::Read>(
    tag: DsTag,
    mut reader: R,
) -> std::io::Result<Digest32> {
    use std::io::Read;
    const BUF: usize = 1 << 20; // 1 MiB
    let mut ctx = back::Ctx::new();
    absorb_ds_prefix(&mut ctx, tag);

    let mut buf = vec![0u8; BUF];
    loop {
        let n = reader.read(&mut buf)?;
        if n == 0 {
            break;
        }
        ctx.update(&buf[..n]);
    }
    Ok(ctx.finalize())
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

    /// `sha256(tag: str, data: bytes) -> bytes`
    #[pyfunction]
    pub fn sha256(py: Python<'_>, tag: &str, data: &[u8]) -> PyResult<PyObject> {
        let t = parse_tag(tag)?;
        let out = super::sha256_ds(t, data);
        Ok(PyBytes::new(py, &out).into())
    }

    /// `sha256_hex(tag: str, data: bytes) -> str`
    #[pyfunction]
    pub fn sha256_hex(_py: Python<'_>, tag: &str, data: &[u8]) -> PyResult<String> {
        let t = parse_tag(tag)?;
        let out = super::sha256_ds(t, data);
        Ok(to_lower_hex(&out))
    }

    pub fn register(m: &Bound<PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(sha256, m)?)?;
        m.add_function(wrap_pyfunction!(sha256_hex, m)?)?;
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
    fn sha256_empty_matches_vector() {
        // SHA-256("") well-known vector:
        // e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        let got = super::sha256(&[]);
        let expect = hex_literal::hex!(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(got, expect);
    }

    #[test]
    fn sha256_abc_matches_vector() {
        // SHA-256("abc"):
        // ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
        let got = super::sha256(b"abc");
        let expect = hex_literal::hex!(
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(got, expect);
    }

    #[test]
    fn many_equals_concat() {
        let a = super::sha256_many([b"ab".as_ref(), b"c"].into_iter());
        let b = super::sha256(b"abc");
        assert_eq!(a, b);
    }

    #[test]
    fn ds_stream_equals_ds_direct() {
        let data = vec![7u8; 1_500_000]; // > 1 MiB
        let via_reader = super::sha256_reader_ds(Tag::DaBlob, &data[..]).unwrap();
        let via_direct = super::sha256_ds(Tag::DaBlob, &data);
        assert_eq!(via_reader, via_direct);
    }

    #[test]
    fn ds_changes_digest() {
        let d0 = super::sha256(b"hello");
        let d1 = super::sha256_ds(Tag::Generic, b"hello");
        assert_ne!(d0, d1, "DS prefix must affect the digest");
    }
}
