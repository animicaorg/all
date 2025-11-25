//! Wallet module: mnemonic/keystore helpers and PQ signers.
//!
//! This module exposes a small, stable surface for producing **Animica**
//! addresses and signatures. The address format is:
//!
//! ```text
//! payload = alg_id (u16, BE) || sha3_256(pubkey_bytes)
//! address = bech32m(hrp="anim", payload)
//! ```
//!
//! The `Wallet` wraps a pluggable [`WalletSigner`] (Dilithium3, SPHINCS+, …).
//! Implementations live under `wallet::signer` and are feature-gated by `pq`.
//!
//! ## Examples
//! ```no_run
//! use animica_sdk::wallet::{Wallet, WalletSigner};
//! # struct DummySigner; # impl WalletSigner for DummySigner {
//! #   fn alg_id(&self) -> u16 { 0x0103 } // example
//! #   fn public_key(&self) -> Vec<u8> { vec![0u8; 1312] }
//! #   fn sign(&self, _domain: &[u8], msg: &[u8]) -> Result<Vec<u8>, animica_sdk::error::Error> {
//! #       Ok([b'D', b'U', b'M', b'Y'].into_iter().chain(msg.iter().copied()).collect())
//! #   }
//! # }
//! let w = Wallet::new(DummySigner)?;
//! assert!(w.address().starts_with("anim1"));
//! let sig = w.sign(b"sign-domain/tx", b"hello")?;
//! println!("addr={} sig_len={}", w.address(), sig.len());
//! # Ok::<(), animica_sdk::error::Error>(())
//! ```
use crate::error::{Error, Result};
use std::sync::Arc;

pub mod mnemonic;
pub mod keystore;

/// Post-quantum signer implementations (Dilithium3/SPHINCS+ via liboqs or other backends).
/// Enabled with the `pq` feature.
#[cfg(feature = "pq")]
pub mod signer;

//
// ----------------------------- Traits & Types --------------------------------
//

/// Minimal signer interface used by the SDK wallet. Concrete implementations
/// (e.g., Dilithium3/SPHINCS+) must implement this trait.
///
/// `alg_id` must match the canonical IDs in `pq/alg_ids.yaml` and the address
/// scheme (two-byte big-endian in the address payload).
pub trait WalletSigner: Send + Sync {
    /// Canonical algorithm id (u16).
    fn alg_id(&self) -> u16;

    /// Raw public key bytes as required for address derivation.
    fn public_key(&self) -> Vec<u8>;

    /// Produce a domain-separated signature over `message`.
    ///
    /// The `domain` should be a short stable string/bytes identifying the
    /// signing context (e.g., `b"sign-domain/tx"` or `b"sign-domain/ws-auth"`).
    fn sign(&self, domain: &[u8], message: &[u8]) -> Result<Vec<u8>>;
}

/// Convenience wrapper around a signer providing address derivation and helpers.
#[derive(Clone)]
pub struct Wallet {
    alg_id: u16,
    pubkey: Vec<u8>,
    address: String,
    signer: Arc<dyn WalletSigner>,
}

impl std::fmt::Debug for Wallet {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Wallet")
            .field("alg_id", &self.alg_id)
            .field("address", &self.address)
            .finish()
    }
}

impl Wallet {
    /// Construct from any signer implementation.
    pub fn new<S: WalletSigner + 'static>(signer: S) -> Result<Self> {
        let alg = signer.alg_id();
        let pk = signer.public_key();
        let addr = crate::address::derive_address(alg, &pk)?;
        Ok(Self {
            alg_id: alg,
            pubkey: pk,
            address: addr,
            signer: Arc::new(signer),
        })
    }

    /// Address in bech32m (`anim1…`) form.
    pub fn address(&self) -> &str {
        &self.address
    }

    /// Algorithm id used by this wallet.
    pub fn alg_id(&self) -> u16 {
        self.alg_id
    }

    /// Public key bytes.
    pub fn public_key(&self) -> &[u8] {
        &self.pubkey
    }

    /// Sign arbitrary bytes with an explicit domain separator.
    pub fn sign(&self, domain: &[u8], message: &[u8]) -> Result<Vec<u8>> {
        self.signer.sign(domain, message)
    }

    /// Convenience for signing **transaction sign-bytes** (already CBOR-encoded,
    /// canonical, and domain-encoded by the caller).
    ///
    /// Use this when you have produced the canonical SignBytes per spec using
    /// the `tx::encode` helpers.
    pub fn sign_tx_signbytes(&self, sign_bytes: &[u8]) -> Result<Vec<u8>> {
        // Transaction signing domain agreed across SDKs.
        const TX_DOMAIN: &[u8] = b"sign-domain/tx";
        self.signer.sign(TX_DOMAIN, sign_bytes)
    }
}

//
// --------------------------- High-level Helpers -------------------------------
//

/// Validate a bech32m address and return `(alg_id, pubkey_hash32)`.
///
/// This simply forwards to `crate::address::validate_address`.
pub fn validate_address(addr: &str) -> Result<(u16, [u8; 32])> {
    crate::address::validate_address(addr)
}

//
// ------------------------------ Re-exports -----------------------------------
//

pub use keystore::{Keystore, KeystoreEntry};
pub use mnemonic::{Mnemonic, MnemonicLang};

#[cfg(feature = "pq")]
pub use signer::{Dilithium3Signer, SphincsShake128sSigner};

