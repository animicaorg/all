# Third-Party Notices

This project includes or depends on third-party software components. The following is a list of these components along with their respective license information.

## Vendored Pure-Python Cryptographic Implementations

### kyber_py (ML-KEM-768 / Kyber768)

**Location:** `python/animica/_vendor/kyber_py/`

**License:** MIT License

**Copyright:** Copyright (c) 2024 Animica Contributors

**Description:** A minimal pure-Python reference implementation of ML-KEM-768 (Kyber768) key encapsulation mechanism based on FIPS 203 (Module-Lattice-Based Key-Encapsulation Mechanism Standard).

This is a reference implementation created for the Animica project. For production use, consider using a fully validated implementation.

**License Text:** See `python/animica/_vendor/kyber_py/LICENSE`

---

### dilithium_py (ML-DSA-65 / Dilithium3)

**Location:** `python/animica/_vendor/dilithium_py/`

**License:** MIT License

**Copyright:** Copyright (c) 2024 Animica Contributors

**Description:** A minimal pure-Python reference implementation of ML-DSA-65 (Dilithium3) digital signature algorithm based on FIPS 204 (Module-Lattice-Based Digital Signature Standard).

This is a reference implementation created for the Animica project. For production use, consider using a fully validated implementation.

**License Text:** See `python/animica/_vendor/dilithium_py/LICENSE`

---

## Standards and Specifications

The post-quantum cryptographic algorithms implemented in this project are based on the following NIST standards:

- **FIPS 203:** Module-Lattice-Based Key-Encapsulation Mechanism Standard (ML-KEM)
- **FIPS 204:** Module-Lattice-Based Digital Signature Standard (ML-DSA)

These standards are public domain specifications from the National Institute of Standards and Technology (NIST).

---

## Notes

The pure-Python implementations provided in this project are intended for:
- Environments where native compiled dependencies are not available or desired
- Educational and reference purposes
- Development and testing scenarios

For production deployments requiring maximum performance and security assurance, consider using:
- Hardware-accelerated implementations
- Implementations that have undergone formal security audits
- NIST-validated cryptographic modules

---

*Last updated: 2024-12-13*
