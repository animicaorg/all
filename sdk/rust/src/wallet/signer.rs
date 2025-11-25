use crate::error::{Error, Result};
use sha3::{Digest, Sha3_512};

#[cfg(feature = "pq")]
use oqs::sig::{Algorithm, PublicKey, SecretKey, Sig};

/// Canonical Animica PQ alg IDs (must match pq/alg_ids.yaml and SDKs)
pub const ALG_ID_DILITHIUM3: u16 = 0x0103;
pub const ALG_ID_SPHINCS_SHAKE_128S: u16 = 0x0201;

/// Internal helper: domain-separated prehash used by all signers.
/// Hash = SHA3-512( domain || 0x00 || message )
fn prehash(domain: &[u8], message: &[u8]) -> [u8; 64] {
    let mut h = Sha3_512::new();
    h.update(domain);
    h.update(&[0u8]);
    h.update(message);
    let out = h.finalize();
    let mut arr = [0u8; 64];
    arr.copy_from_slice(&out);
    arr
}

/// Minimal trait a wallet signer should satisfy.
pub trait WalletSigner {
    /// 16-bit canonical algorithm id.
    fn alg_id(&self) -> u16;
    /// Raw public key bytes for this algorithm.
    fn public_key(&self) -> &[u8];
    /// Sign `message` under a domain separator. Returns raw signature bytes.
    fn sign(&self, domain: &[u8], message: &[u8]) -> Result<Vec<u8>>;
}

#[cfg(feature = "pq")]
fn map_oqs_err<E: core::fmt::Display>(e: E) -> Error {
    Error::Other(format!("oqs error: {e}"))
}

#[cfg(feature = "pq")]
fn ensure_enabled(alg: Algorithm) -> Result<()> {
    if !alg.is_enabled() {
        return Err(Error::Other(format!(
            "liboqs algorithm not enabled at build time: {alg}"
        )));
    }
    Ok(())
}

/// Dilithium3 signer (via liboqs).
#[cfg(feature = "pq")]
pub struct Dilithium3Signer {
    pk: Vec<u8>,
    sk: Vec<u8>,
}

#[cfg(feature = "pq")]
impl core::fmt::Debug for Dilithium3Signer {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.debug_struct("Dilithium3Signer")
            .field("pk_len", &self.pk.len())
            .field("sk_len", &self.sk.len())
            .finish()
    }
}

#[cfg(feature = "pq")]
impl Dilithium3Signer {
    /// Construct from an existing keypair (byte-accurate; no decoding performed).
    pub fn from_keypair(public_key: impl AsRef<[u8]>, secret_key: impl AsRef<[u8]>) -> Self {
        Self {
            pk: public_key.as_ref().to_vec(),
            sk: secret_key.as_ref().to_vec(),
        }
    }

    /// Generate a fresh keypair using liboqs.
    pub fn generate() -> Result<Self> {
        let alg = Algorithm::Dilithium3;
        ensure_enabled(alg)?;
        let sig = Sig::new(alg).map_err(map_oqs_err)?;
        let (pk, sk) = sig.keypair().map_err(map_oqs_err)?;
        Ok(Self {
            pk: pk.into_vec(),
            sk: sk.into_vec(),
        })
    }

    #[inline]
    fn algorithm() -> Algorithm {
        // Both `Dilithium3` and `MlDsa65` map to the same underlying scheme
        // in modern liboqs; we stick with the legacy-friendly variant here.
        Algorithm::Dilithium3
    }
}

#[cfg(feature = "pq")]
impl WalletSigner for Dilithium3Signer {
    fn alg_id(&self) -> u16 {
        ALG_ID_DILITHIUM3
    }

    fn public_key(&self) -> &[u8] {
        &self.pk
    }

    fn sign(&self, domain: &[u8], message: &[u8]) -> Result<Vec<u8>> {
        let alg = Self::algorithm();
        ensure_enabled(alg)?;
        let sig = Sig::new(alg).map_err(map_oqs_err)?;
        // Import secret key bytes into liboqs
        let sk: SecretKey = sig
            .secret_key_from_bytes(&self.sk)
            .map_err(map_oqs_err)?;
        let digest = prehash(domain, message);
        let signature = sig.sign(&digest, &sk).map_err(map_oqs_err)?;
        Ok(signature.into_vec())
    }
}

/// SPHINCS+ SHAKE-128s (simple) signer (via liboqs).
#[cfg(feature = "pq")]
pub struct SphincsShake128sSigner {
    pk: Vec<u8>,
    sk: Vec<u8>,
}

#[cfg(feature = "pq")]
impl core::fmt::Debug for SphincsShake128sSigner {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        f.debug_struct("SphincsShake128sSigner")
            .field("pk_len", &self.pk.len())
            .field("sk_len", &self.sk.len())
            .finish()
    }
}

#[cfg(feature = "pq")]
impl SphincsShake128sSigner {
    /// Construct from an existing keypair (byte-accurate; no decoding performed).
    pub fn from_keypair(public_key: impl AsRef<[u8]>, secret_key: impl AsRef<[u8]>) -> Self {
        Self {
            pk: public_key.as_ref().to_vec(),
            sk: secret_key.as_ref().to_vec(),
        }
    }

    /// Generate a fresh keypair using liboqs.
    pub fn generate() -> Result<Self> {
        let alg = Algorithm::SphincsShake128sSimple;
        ensure_enabled(alg)?;
        let sig = Sig::new(alg).map_err(map_oqs_err)?;
        let (pk, sk) = sig.keypair().map_err(map_oqs_err)?;
        Ok(Self {
            pk: pk.into_vec(),
            sk: sk.into_vec(),
        })
    }

    #[inline]
    fn algorithm() -> Algorithm {
        Algorithm::SphincsShake128sSimple
    }
}

#[cfg(feature = "pq")]
impl WalletSigner for SphincsShake128sSigner {
    fn alg_id(&self) -> u16 {
        ALG_ID_SPHINCS_SHAKE_128S
    }

    fn public_key(&self) -> &[u8] {
        &self.pk
    }

    fn sign(&self, domain: &[u8], message: &[u8]) -> Result<Vec<u8>> {
        let alg = Self::algorithm();
        ensure_enabled(alg)?;
        let sig = Sig::new(alg).map_err(map_oqs_err)?;
        let sk: SecretKey = sig
            .secret_key_from_bytes(&self.sk)
            .map_err(map_oqs_err)?;
        let digest = prehash(domain, message);
        let signature = sig.sign(&digest, &sk).map_err(map_oqs_err)?;
        Ok(signature.into_vec())
    }
}

#[cfg(all(test, feature = "pq"))]
mod tests {
    use super::*;
    use oqs::sig::Sig;

    const TEST_DOMAIN: &[u8] = b"animica/tx/sign/v1";

    #[test]
    fn dilithium3_roundtrip() -> Result<()> {
        let s = Dilithium3Signer::generate()?;
        let digest = prehash(TEST_DOMAIN, b"hello");
        // Verify with liboqs directly
        let sig = Sig::new(Dilithium3Signer::algorithm()).map_err(super::map_oqs_err)?;
        let pk: PublicKey = sig.public_key_from_bytes(s.public_key()).map_err(super::map_oqs_err)?;
        let sig_bytes = s.sign(TEST_DOMAIN, b"hello")?;
        sig.verify(&sig_bytes, &digest, &pk).map_err(super::map_oqs_err)?;
        Ok(())
    }

    #[test]
    fn sphincs_roundtrip() -> Result<()> {
        let s = SphincsShake128sSigner::generate()?;
        let digest = prehash(TEST_DOMAIN, b"world");
        let sig = Sig::new(SphincsShake128sSigner::algorithm()).map_err(super::map_oqs_err)?;
        let pk: PublicKey = sig.public_key_from_bytes(s.public_key()).map_err(super::map_oqs_err)?;
        let sig_bytes = s.sign(TEST_DOMAIN, b"world")?;
        sig.verify(&sig_bytes, &digest, &pk).map_err(super::map_oqs_err)?;
        Ok(())
    }
}

