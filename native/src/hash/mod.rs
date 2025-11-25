//! Hash abstraction and domain-separated helpers.
//!
//! This module provides:
//! - `HashFn`: a simple trait for 256-bit hash functions with streaming update.
//! - A default `Blake3Hash` implementation (pure Rust, fast).
//! - Domain-separation tags (`DsTag`) to avoid cross-protocol collisions.
//! - Small helper functions for one-shot / multi-part hashing.
//!
//! ### Why domain separation?
//! To keep different data families (txs, headers, proofs, etc.) from
//!"accidentally" sharing the same hash preimage space, we attach a
//! context-string when constructing hashers. With BLAKE3 we use
//! `Hasher::new_derive_key(context)`, which is the recommended way to
//! separate domains. For other algorithms, consumers can prefix an
//! agreed domain header before data.
//!
//! ### Digest size
//! We standardize on 32-byte digests (`[u8; 32]`) across the codebase
//! (Keccak-256, BLAKE3-256, SHA-256 all fit), keeping APIs consistent.

use core::fmt;

/// A 256-bit digest used across the codebase.
pub type Digest32 = [u8; 32];

/// Protocol-wide domain separation tags.
///
/// These are intentionally short and stable; the actual BLAKE3 context
/// includes a versioned prefix (`"animica:v1:"`) so we can evolve later
/// without breaking existing encodings.
///
/// If you add or rename a tag here, also update any specs/ABI docs that
/// embed these tags (e.g., proof envelopes, header hashing, etc.).
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DsTag {
    /// Generic, internal usage (avoid in consensus-critical flows).
    Generic,
    /// Transaction canonical encoding + signing / id.
    Tx,
    /// Block header hashing (stable).
    Header,
    /// Block body (tx list / layout).
    BlockBody,
    /// Proof envelope (DA/NMT/VDF/AI/Quantum).
    ProofEnvelope,
    /// VM bytecode / contract package hashing.
    VmCode,
    /// VM storage nodes / trie fragments (if any).
    VmState,
    /// P2P wire messages (framing).
    P2p,
    /// Data-availability blob commitment.
    DaBlob,
    /// Namespaced Merkle tree nodes.
    Nmt,
    /// Randomness beacon rounds / commitments.
    Randomness,
    /// AI Compute Framework task descriptors & results.
    Aicf,
    /// Quantum receipts / trap summaries.
    Quantum,
    /// Explorer / indexer materialized views (non-consensus).
    Explorer,
    /// ZK proof inputs/outputs bound to syscalls.
    Zk,
    /// Capability binding helpers (cross-subsystem).
    Capability,
}

impl DsTag {
    /// Return the canonical BLAKE3 context string for this tag.
    #[inline]
    pub fn context(self) -> &'static str {
        // Keep the "v1" prefix stable; bump only with extreme care
        // (and never for already-finalized consensus objects).
        match self {
            DsTag::Generic       => "animica:v1:generic",
            DsTag::Tx            => "animica:v1:tx",
            DsTag::Header        => "animica:v1:header",
            DsTag::BlockBody     => "animica:v1:block_body",
            DsTag::ProofEnvelope => "animica:v1:proof_envelope",
            DsTag::VmCode        => "animica:v1:vm_code",
            DsTag::VmState       => "animica:v1:vm_state",
            DsTag::P2p           => "animica:v1:p2p",
            DsTag::DaBlob        => "animica:v1:da_blob",
            DsTag::Nmt           => "animica:v1:nmt",
            DsTag::Randomness    => "animica:v1:randomness",
            DsTag::Aicf          => "animica:v1:aicf",
            DsTag::Quantum       => "animica:v1:quantum",
            DsTag::Explorer      => "animica:v1:explorer",
            DsTag::Zk            => "animica:v1:zk",
            DsTag::Capability    => "animica:v1:capability",
        }
    }
}

/// Minimal interface for a streaming 256-bit hash.
///
/// Implementors should produce a 32-byte digest and support constructing
/// a new instance with a domain tag (`DsTag`). The trait provides blanket
/// defaults for one-shot helpers.
pub trait HashFn: Sized + fmt::Debug {
    /// Create a new hasher prepped for a given domain.
    fn new_ds(tag: DsTag) -> Self;

    /// Feed additional bytes.
    fn update(&mut self, data: &[u8]);

    /// Finalize and return the 32-byte digest. Consumes `self`.
    fn finalize(self) -> Digest32;

    /// One-shot convenience over `new_ds` + `update` + `finalize`.
    #[inline]
    fn hash_ds(tag: DsTag, data: &[u8]) -> Digest32 {
        let mut h = Self::new_ds(tag);
        h.update(data);
        h.finalize()
    }

    /// Hash several chunks as if concatenated, under `tag`.
    #[inline]
    fn hash_many<'a, I>(tag: DsTag, parts: I) -> Digest32
    where
        I: IntoIterator<Item = &'a [u8]>,
    {
        let mut h = Self::new_ds(tag);
        for p in parts {
            h.update(p);
        }
        h.finalize()
    }
}

/* ---------------------------- Blake3 (default) ---------------------------- */

#[derive(Debug)]
pub struct Blake3Hash(::blake3::Hasher);

impl HashFn for Blake3Hash {
    #[inline]
    fn new_ds(tag: DsTag) -> Self {
        // Use derive-key mode for built-in domain separation.
        Self(::blake3::Hasher::new_derive_key(tag.context()))
    }

    #[inline]
    fn update(&mut self, data: &[u8]) {
        self.0.update(data);
    }

    #[inline]
    fn finalize(self) -> Digest32 {
        *self.0.finalize().as_bytes()
    }
}

/// One-shot Blake3-256 with a domain tag.
#[inline]
pub fn blake3_256_ds(tag: DsTag, data: &[u8]) -> Digest32 {
    Blake3Hash::hash_ds(tag, data)
}

/// One-shot Blake3-256 over multiple chunks.
#[inline]
pub fn blake3_256_many<'a, I>(tag: DsTag, parts: I) -> Digest32
where
    I: IntoIterator<Item = &'a [u8]>,
{
    Blake3Hash::hash_many(tag, parts)
}

/* -------------------------- Generic helpers (agnostic) -------------------------- */

/// Hash bytes in a given domain, using the default hash implementation.
#[inline]
pub fn hash_ds(tag: DsTag, data: &[u8]) -> Digest32 {
    blake3_256_ds(tag, data)
}

/// Hash concatenated chunks in a given domain, using the default hash impl.
#[inline]
pub fn hash_many<'a, I>(tag: DsTag, parts: I) -> Digest32
where
    I: IntoIterator<Item = &'a [u8]>,
{
    blake3_256_many(tag, parts)
}

/// Hash a sequence of 32-byte items (often child digests) in a domain.
///
/// This is a common pattern for Merkle-node hashing or deterministic
/// pairwise compositions.
#[inline]
pub fn hash_digests(tag: DsTag, items: &[Digest32]) -> Digest32 {
    let mut h = Blake3Hash::new_ds(tag);
    for it in items {
        h.update(it);
    }
    h.finalize()
}

/* ------------------------------ Python bindings ------------------------------ */

#[cfg(feature = "python")]
mod py {
    use super::*;
    use pyo3::prelude::*;
    use pyo3::types::PyBytes;

    fn parse_tag(tag: &str) -> PyResult<DsTag> {
        let t = match tag {
            "generic"       => DsTag::Generic,
            "tx"            => DsTag::Tx,
            "header"        => DsTag::Header,
            "block_body"    => DsTag::BlockBody,
            "proof_envelope"=> DsTag::ProofEnvelope,
            "vm_code"       => DsTag::VmCode,
            "vm_state"      => DsTag::VmState,
            "p2p"           => DsTag::P2p,
            "da_blob"       => DsTag::DaBlob,
            "nmt"           => DsTag::Nmt,
            "randomness"    => DsTag::Randomness,
            "aicf"          => DsTag::Aicf,
            "quantum"       => DsTag::Quantum,
            "explorer"      => DsTag::Explorer,
            "zk"            => DsTag::Zk,
            "capability"    => DsTag::Capability,
            _ => return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("unknown DsTag: {tag}")))
        };
        Ok(t)
    }

    #[pyfunction]
    pub fn blake3_256(py: Python<'_>, tag: &str, data: &[u8]) -> PyResult<PyObject> {
        let t = parse_tag(tag)?;
        let out = super::blake3_256_ds(t, data);
        Ok(PyBytes::new(py, &out).into())
    }

    #[pyfunction]
    pub fn blake3_256_hex(_py: Python<'_>, tag: &str, data: &[u8]) -> PyResult<String> {
        let t = parse_tag(tag)?;
        let out = super::blake3_256_ds(t, data);
        Ok(to_lower_hex(&out))
    }

    pub fn register(m: &Bound<PyModule>) -> PyResult<()> {
        m.add_function(wrap_pyfunction!(blake3_256, m)?)?;
        m.add_function(wrap_pyfunction!(blake3_256_hex, m)?)?;
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

/* ----------------------------------- Tests ----------------------------------- */

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn digest_length_is_32() {
        let d = blake3_256_ds(DsTag::Generic, b"");
        assert_eq!(d.len(), 32);
    }

    #[test]
    fn domain_separates_outputs() {
        let a = blake3_256_ds(DsTag::Tx, b"hello");
        let b = blake3_256_ds(DsTag::Header, b"hello");
        assert_ne!(a, b, "different domains must not collide on same input");
    }

    #[test]
    fn streaming_equals_one_shot() {
        let parts = [b"abc" as &[u8], b"def", b"ghi"];
        let one = blake3_256_many(DsTag::VmCode, parts.iter().copied());
        let cat = blake3_256_ds(DsTag::VmCode, b"abcdefghi");
        assert_eq!(one, cat);
    }

    #[test]
    fn hash_digests_is_stable_concat() {
        let x = blake3_256_ds(DsTag::Generic, b"x");
        let y = blake3_256_ds(DsTag::Generic, b"y");
        let z = blake3_256_ds(DsTag::Generic, b"z");

        let via_helper = hash_digests(DsTag::Nmt, &[x, y, z]);

        let mut h = Blake3Hash::new_ds(DsTag::Nmt);
        h.update(&x);
        h.update(&y);
        h.update(&z);
        let via_manual = h.finalize();

        assert_eq!(via_helper, via_manual);
    }
}

/* ------------------------- Legacy-style BLAKE3 API ------------------------- */
///
/// This submodule provides a minimal, "raw BLAKE3" API used by older code and
/// some tests (`crate::nmt` in particular). It intentionally exposes:
///
///   - `blake3::blake3(&[u8]) -> Digest32`
///   - `blake3::blake3_many(iter)`
///
/// Both helpers are thin wrappers around the crate's default BLAKE3
/// implementation and are fully implemented.
pub mod blake3 {
    use super::Digest32;

    /// Hash a single byte slice with BLAKE3-256.
    #[inline]
    pub fn blake3(data: &[u8]) -> Digest32 {
        let hash = ::blake3::hash(data);
        *hash.as_bytes()
    }

    /// Hash a sequence of byte slices as their concatenation.
    #[inline]
    pub fn blake3_many<'a, I>(parts: I) -> Digest32
    where
        I: IntoIterator<Item = &'a [u8]>,
    {
        let mut hasher = ::blake3::Hasher::new();
        for part in parts {
            hasher.update(part);
        }
        *hasher.finalize().as_bytes()
    }
}
