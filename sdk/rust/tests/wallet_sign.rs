// Wallet signer sanity tests.
// --------------------------
// These tests exercise mnemonic→PQ signer derivation and address
// formatting. They do not require a running node.
//
// By default the whole module is gated behind the `pq` feature so it
// only builds when post-quantum signers are enabled. When the feature
// is missing, we compile a tiny placeholder test so `cargo test` still
// succeeds for non-PQ builds.

#[cfg(feature = "pq")]
mod pq_tests {
    use animica_sdk::wallet::mnemonic::Mnemonic;
    use animica_sdk::wallet::signer::{Dilithium3Signer, Signer};

    const TEST_MNEMONIC: &str = "shoot island position soft burden budget tooth cruel issue \
                                 economy destroy above holiday palm squirrel cute swamp rubber \
                                 era cost blouse trouble below frost";

    #[test]
    fn dilithium3_address_derivation_is_stable_and_distinct_by_index() -> Result<(), Box<dyn std::error::Error>> {
        let m = Mnemonic::from_phrase(TEST_MNEMONIC)?;

        // Derive index 0 twice → same address
        let s0a = Dilithium3Signer::from_mnemonic(&m, 0)?;
        let s0b = Dilithium3Signer::from_mnemonic(&m, 0)?;
        let addr0a = s0a.address();
        let addr0b = s0b.address();

        assert_eq!(addr0a, addr0b, "same mnemonic/index must yield same address");
        assert!(addr0a.starts_with("anim1"), "address should be bech32m with 'anim' HRP");
        assert!(addr0a.len() > 20, "address should be non-trivial length");

        // Derive index 1 → different address
        let s1 = Dilithium3Signer::from_mnemonic(&m, 1)?;
        let addr1 = s1.address();
        assert_ne!(addr0a, addr1, "different index should yield different address");

        Ok(())
    }

    #[test]
    fn dilithium3_can_sign_bytes() -> Result<(), Box<dyn std::error::Error>> {
        let m = Mnemonic::from_phrase(TEST_MNEMONIC)?;
        let mut signer = Dilithium3Signer::from_mnemonic(&m, 0)?;

        // Domain-separated sign-bytes (normally produced by tx::encode).
        let sign_bytes = b"animica:signbytes:test-vector-001";

        let sig = signer.sign(sign_bytes)?;
        assert!(!sig.is_empty(), "signature must be non-empty");

        // We don't assert determinism: Dilithium signatures include randomness.
        Ok(())
    }
}

#[cfg(not(feature = "pq"))]
mod non_pq_placeholder {
    #[test]
    fn pq_feature_not_enabled_placeholder() {
        // No-op: the SDK was built without the `pq` feature.
        // This placeholder keeps `cargo test` green on non-PQ builds.
        assert!(true);
    }
}
