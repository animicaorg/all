# SPDX-License-Identifier: MIT
"""
AMD SEV-SNP attestation report parser & verifier.

Implements a pragmatic subset sufficient for Animica proof verification:
- Parses the fixed-layout ATTESTATION_REPORT fields (Rev 1.58 of the FW ABI).
- Extracts MEASUREMENT (SHA-384, 48 bytes), REPORT_DATA (64 bytes), HOST_DATA, policy, TCBs, etc.
- Decodes PLATFORM_INFO feature bits and the SIGNING_KEY selection (VCEK/VLEK).
- Verifies the report signature over bytes 0x00..0x29F using a provided VCEK/VLEK leaf cert.
- Optionally verifies a simple issuer->subject chain up to an ASK/ARK root (PEM).
- Returns a TEEEvidence object (see .common) with rich metadata for scoring.

References (field offsets and signature coverage):
  AMD “SEV Secure Nested Paging Firmware ABI Specification”, Rev 1.58 (May 2025),
  Table 23 “ATTESTATION_REPORT Structure” (signature covers bytes 0x00..0x29F),
  Table 24 “PLATFORM_INFO Field”.
"""

from __future__ import annotations
from dataclasses import asdict
from typing import Any, Dict, Optional, Tuple
import binascii
import hashlib
import struct

try:
    # cryptography is optional; if missing we still parse, but cannot verify signature
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils
    _HAS_CRYPTO = True
except Exception:  # pragma: no cover
    _HAS_CRYPTO = False

from ..errors import AttestationError
from .common import TEEKind, TEEEvidence, TCBStatus

# ---- Constants from AMD spec (Rev 1.58) ----

REPORT_SIZE_MIN = 0x2A0  # bytes up to (and excluding) SIGNATURE
SIGNATURE_OFFSET = 0x2A0 # signature starts here; runs to EOF
# Field offsets/sizes (see Table 23)
OFF_VERSION       = 0x00; SZ_VERSION = 4
OFF_GUEST_SVN     = 0x04; SZ_GUEST_SVN = 4
OFF_POLICY        = 0x08; SZ_POLICY = 8
OFF_FAMILY_ID     = 0x10; SZ_FAMILY_ID = 16
OFF_IMAGE_ID      = 0x20; SZ_IMAGE_ID = 16
OFF_VMPL          = 0x30; SZ_VMPL = 4
OFF_SIGNATURE_ALG = 0x34; SZ_SIGNATURE_ALG = 4
OFF_CURRENT_TCB   = 0x38; SZ_CURRENT_TCB = 8
OFF_PLATFORM_INFO = 0x40; SZ_PLATFORM_INFO = 8
OFF_SIGNING_KEY   = 0x48  # bits 2:0 indicate key selection; upper bits reserved
OFF_REPORT_DATA   = 0x50; SZ_REPORT_DATA = 64
OFF_MEASUREMENT   = 0x90; SZ_MEASUREMENT = 48  # SHA-384
OFF_HOST_DATA     = 0xC0; SZ_HOST_DATA = 32
OFF_ID_KEY_DIGEST = 0xE0; SZ_ID_KEY_DIGEST = 48
OFF_AUTHOR_DIGEST = 0x110; SZ_AUTHOR_DIGEST = 48
OFF_REPORT_ID     = 0x140; SZ_REPORT_ID = 32
OFF_REPORT_ID_MA  = 0x160; SZ_REPORT_ID_MA = 32
OFF_REPORTED_TCB  = 0x180; SZ_REPORTED_TCB = 8
OFF_CHIP_ID       = 0x1A0; SZ_CHIP_ID = 64
OFF_COMMITTED_TCB = 0x1E0; SZ_COMMITTED_TCB = 8
OFF_CURRENT_BUILD = 0x1E8
OFF_CURRENT_MINOR = 0x1E9
OFF_CURRENT_MAJOR = 0x1EA
OFF_COMMIT_BUILD  = 0x1EC
OFF_COMMIT_MINOR  = 0x1ED
OFF_COMMIT_MAJOR  = 0x1EE
OFF_LAUNCH_TCB    = 0x1F0; SZ_LAUNCH_TCB = 8
OFF_LAUNCH_MITV   = 0x1F8; SZ_LAUNCH_MITV = 8
OFF_CURR_MITV     = 0x200; SZ_CURR_MITV = 8

# SIGNING_KEY selection (Table 23 @ 0x48 bits 2:0)
SIGNING_KEY_VCEK = 0
SIGNING_KEY_VLEK = 1
SIGNING_KEY_NONE = 7

# PLATFORM_INFO bits (Table 24)
PLAT_SMT_EN  = 1 << 0
PLAT_TSME_EN = 1 << 1
PLAT_ECC_EN  = 1 << 2
PLAT_RAPL_DIS= 1 << 3
PLAT_CIPH_DR = 1 << 4
PLAT_ALIAS_OK= 1 << 5
PLAT_TIO_EN  = 1 << 7

# ---- Helpers ----

def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]

def _u64(b: bytes, off: int) -> int:
    return struct.unpack_from("<Q", b, off)[0]

def _slice(b: bytes, off: int, n: int) -> bytes:
    return memoryview(b)[off:off+n].tobytes()

def _hex(b: Optional[bytes]) -> Optional[str]:
    return None if b is None else binascii.hexlify(b).decode()

def decode_platform_info(pi: int) -> Dict[str, bool]:
    return {
        "smt_en": bool(pi & PLAT_SMT_EN),
        "tsme_en": bool(pi & PLAT_TSME_EN),
        "ecc_en": bool(pi & PLAT_ECC_EN),
        "rapl_disabled": bool(pi & PLAT_RAPL_DIS),
        "ciphertext_hiding_dram_en": bool(pi & PLAT_CIPH_DR),
        "alias_check_complete": bool(pi & PLAT_ALIAS_OK),
        "tio_en": bool(pi & PLAT_TIO_EN),
    }

def _signing_key_sel(b: bytes) -> int:
    # at 0x48, low 3 bits encode selection
    val = _u32(b, OFF_SIGNING_KEY)
    return val & 0b111

# ---- Core parsing ----

def parse_snp_report(report: bytes) -> Dict[str, Any]:
    """
    Parse an AMD SEV-SNP ATTESTATION_REPORT buffer.

    Returns a dict of raw fields and convenient hex strings for large blobs.
    Raises AttestationError on length/format errors.
    """
    if not isinstance(report, (bytes, bytearray, memoryview)):
        raise AttestationError("SEV-SNP report must be bytes-like")

    report = bytes(report)
    if len(report) < REPORT_SIZE_MIN:
        raise AttestationError(f"SEV-SNP report too short: {len(report)} < {REPORT_SIZE_MIN}")

    version        = _u32(report, OFF_VERSION)
    guest_svn      = _u32(report, OFF_GUEST_SVN)
    policy         = _u64(report, OFF_POLICY)
    family_id      = _slice(report, OFF_FAMILY_ID, SZ_FAMILY_ID)
    image_id       = _slice(report, OFF_IMAGE_ID, SZ_IMAGE_ID)
    vmpl           = _u32(report, OFF_VMPL)
    sig_alg        = _u32(report, OFF_SIGNATURE_ALG)
    current_tcb    = _u64(report, OFF_CURRENT_TCB)
    platform_info  = _u64(report, OFF_PLATFORM_INFO)
    signing_key    = _signing_key_sel(report)
    report_data    = _slice(report, OFF_REPORT_DATA, SZ_REPORT_DATA)
    measurement    = _slice(report, OFF_MEASUREMENT, SZ_MEASUREMENT)
    host_data      = _slice(report, OFF_HOST_DATA, SZ_HOST_DATA)
    id_key_digest  = _slice(report, OFF_ID_KEY_DIGEST, SZ_ID_KEY_DIGEST)
    author_digest  = _slice(report, OFF_AUTHOR_DIGEST, SZ_AUTHOR_DIGEST)
    report_id      = _slice(report, OFF_REPORT_ID, SZ_REPORT_ID)
    report_id_ma   = _slice(report, OFF_REPORT_ID_MA, SZ_REPORT_ID_MA)
    reported_tcb   = _u64(report, OFF_REPORTED_TCB)
    chip_id        = _slice(report, OFF_CHIP_ID, SZ_CHIP_ID)
    committed_tcb  = _u64(report, OFF_COMMITTED_TCB)
    current_build  = report[OFF_CURRENT_BUILD]
    current_minor  = report[OFF_CURRENT_MINOR]
    current_major  = report[OFF_CURRENT_MAJOR]
    commit_build   = report[OFF_COMMIT_BUILD]
    commit_minor   = report[OFF_COMMIT_MINOR]
    commit_major   = report[OFF_COMMIT_MAJOR]
    launch_tcb     = _u64(report, OFF_LAUNCH_TCB)
    launch_mitvec  = _u64(report, OFF_LAUNCH_MITV)
    current_mitvec = _u64(report, OFF_CURR_MITV)
    signature      = report[SIGNATURE_OFFSET:]  # DER or raw r||s (96B)

    return {
        "version": version,
        "guest_svn": guest_svn,
        "policy": policy,
        "family_id": _hex(family_id),
        "image_id": _hex(image_id),
        "vmpl": vmpl,
        "signature_algo": sig_alg,
        "current_tcb": current_tcb,
        "platform_info": platform_info,
        "platform_flags": decode_platform_info(platform_info),
        "signing_key_sel": signing_key,
        "report_data": _hex(report_data),
        "measurement": _hex(measurement),
        "host_data": _hex(host_data),
        "id_key_digest": _hex(id_key_digest),
        "author_key_digest": _hex(author_digest),
        "report_id": _hex(report_id),
        "report_id_ma": _hex(report_id_ma),
        "reported_tcb": reported_tcb,
        "chip_id": _hex(chip_id),
        "committed_tcb": committed_tcb,
        "fw_version": {
            "current": {"major": current_major, "minor": current_minor, "build": current_build},
            "committed": {"major": commit_major, "minor": commit_minor, "build": commit_build},
        },
        "launch": {"tcb": launch_tcb, "mitigation_vector": launch_mitvec},
        "current_mitigation_vector": current_mitvec,
        "signature_bytes": _hex(signature),
        "signed_region": _hex(report[:SIGNATURE_OFFSET]),
        "_raw_report": report,  # retained for signature verification
    }

# ---- Signature & chain verification ----

def _ecdsa_verify_p384_sha384(pubkey, msg: bytes, sig: bytes) -> bool:
    """
    Try to verify with DER first; if that fails, try raw r||s (96B big-endian).
    """
    try:
        pubkey.verify(sig, msg, ec.ECDSA(hashes.SHA384()))
        return True
    except Exception:
        pass
    if len(sig) == 96:
        r = int.from_bytes(sig[:48], "big")
        s = int.from_bytes(sig[48:], "big")
        der = utils.encode_dss_signature(r, s)
        try:
            pubkey.verify(der, msg, ec.ECDSA(hashes.SHA384()))
            return True
        except Exception:
            return False
    return False

def verify_report_signature(report: bytes, leaf_cert_pem: bytes) -> bool:
    if not _HAS_CRYPTO:
        raise AttestationError("cryptography is required to verify SEV-SNP signatures")
    claims = parse_snp_report(report)
    try:
        cert = x509.load_pem_x509_certificate(leaf_cert_pem)
        pub = cert.public_key()
        if not isinstance(pub, ec.EllipticCurvePublicKey) or pub.curve.name != "secp384r1":
            raise AttestationError("SEV-SNP VCEK/VLEK must be an ECDSA P-384 public key")
    except Exception as e:
        raise AttestationError(f"Failed to load leaf certificate: {e}") from e
    msg = claims["_raw_report"][:SIGNATURE_OFFSET]
    sig = bytes.fromhex(claims["signature_bytes"]) if isinstance(claims["signature_bytes"], str) else claims["signature_bytes"]
    return _ecdsa_verify_p384_sha384(pub, msg, sig)

def verify_chain_simple(leaf_pem: bytes, chain_pem: Optional[bytes], root_pem: Optional[bytes]) -> bool:
    """
    Very small issuer->subject signature walk using cryptography only.
    Returns True if we can walk and verify signatures up to the provided root.
    If chain/root not supplied, returns True (policy chooses whether to require root-anchoring).
    """
    if not _HAS_CRYPTO:
        return False
    try:
        leaf = x509.load_pem_x509_certificate(leaf_pem)
        inters = []
        if chain_pem:
            # may contain 1 or more concatenated certs
            for blob in chain_pem.split(b"-----END CERTIFICATE-----"):
                blob = blob.strip()
                if not blob:
                    continue
                if not blob.endswith(b"-----END CERTIFICATE-----"):
                    blob += b"\n-----END CERTIFICATE-----\n"
                try:
                    inters.append(x509.load_pem_x509_certificate(blob))
                except Exception:
                    pass
        root = x509.load_pem_x509_certificate(root_pem) if root_pem else None

        # Build simple map subject->cert
        by_subject = {c.subject.rfc4514_string(): c for c in inters}
        if root:
            by_subject[root.subject.rfc4514_string()] = root

        # Walk up
        curr = leaf
        while True:
            issuer = curr.issuer.rfc4514_string()
            subj   = curr.subject.rfc4514_string()
            if issuer == subj:
                # self-signed; accept only if matches provided root (when present)
                if root and curr.fingerprint(hashes.SHA256()) != root.fingerprint(hashes.SHA256()):
                    return False
                # Verify self-signature
                try:
                    curr.public_key().verify(curr.signature, curr.tbs_certificate_bytes,
                                             ec.ECDSA(curr.signature_hash_algorithm))
                except Exception:
                    return False
                return True
            parent = by_subject.get(issuer)
            if parent is None:
                # If a root was provided and we haven't reached it, fail; otherwise pass leniently
                return root is None
            # Verify child with parent
            try:
                parent.public_key().verify(curr.signature, curr.tbs_certificate_bytes,
                                           ec.ECDSA(curr.signature_hash_algorithm))
            except Exception:
                return False
            curr = parent
    except Exception:
        return False

# ---- High-level API ----

def verify_sev_snp_attestation(
    report: bytes,
    *,
    vcek_or_vlek_pem: Optional[bytes] = None,
    chain_pem: Optional[bytes] = None,
    root_pem: Optional[bytes] = None,
) -> TEEEvidence:
    """
    Parse and (optionally) verify a SEV-SNP attestation report.

    Args:
      report: Raw ATTESTATION_REPORT bytes (binary, as returned by SNP REPORT).
      vcek_or_vlek_pem: Leaf certificate (PEM) used to sign the report (VCEK or VLEK).
      chain_pem: Optional concatenated PEM for intermediates (ASK, etc.).
      root_pem: Optional ARK root PEM (matches proofs/attestations/vendor_roots/amd_sev_snp_root.pem).

    Returns:
      TEEEvidence with fields:
        - vendor="amd", kind=TEEKind.SEV_SNP
        - measurement (bytes, 48B SHA-384)
        - report_data (bytes, 64B) and host_data (bytes, 32B)
        - policy (int), guest_svn (int), vmpl (int)
        - tcb dict (current/reported/committed)
        - platform_flags dict (SMT/ECC/etc.)
        - signing_key ("vcek"/"vlek"/"unknown")
        - signature_ok, chain_ok booleans
        - raw_report (bytes)
        - meta: dict of parsed fields (hex strings for big blobs)
    """
    c = parse_snp_report(report)

    # Minimal sanity on sizes (spec-mandated)
    if len(report) < SIGNATURE_OFFSET + 80:  # DER or raw 96B; allow small DER
        # Not fatal (some environments pack signature separately), but mark evidence accordingly
        signature_ok = False
    else:
        # Verify signature if leaf cert provided
        if vcek_or_vlek_pem:
            signature_ok = verify_report_signature(report, vcek_or_vlek_pem)
        else:
            signature_ok = False

    # Chain verification (optional)
    chain_ok = True
    if vcek_or_vlek_pem and (chain_pem or root_pem):
        chain_ok = verify_chain_simple(vcek_or_vlek_pem, chain_pem, root_pem)

    # Signing key selection hint → label
    key_sel = c["signing_key_sel"]
    signing_key = "vcek" if key_sel == SIGNING_KEY_VCEK else "vlek" if key_sel == SIGNING_KEY_VLEK else "unknown"

    # Build TCB status (very light; consumers can apply policy thresholds)
    tcb = {
        "current": c["current_tcb"],
        "reported": c["reported_tcb"],
        "committed": c["committed_tcb"],
        "fw_version": c["fw_version"],
    }
    # A simple status heuristic: reported must not exceed current; committed <= current
    if c["reported_tcb"] > c["current_tcb"]:
        tcb_status = TCBStatus.OUT_OF_SPEC
    else:
        tcb_status = TCBStatus.OK

    evidence = TEEEvidence(
        vendor="amd",
        kind=TEEKind.SEV_SNP,
        measurement=binascii.unhexlify(c["measurement"]),
        report=report,
        report_data=binascii.unhexlify(c["report_data"]),
        host_data=binascii.unhexlify(c["host_data"]),
        policy=c["policy"],
        guest_svn=c["guest_svn"],
        vmpl=c["vmpl"],
        platform_flags=c["platform_flags"],
        signing_key=signing_key,
        chain_ok=bool(chain_ok),
        signature_ok=bool(signature_ok),
        tcb=tcb,
        tcb_status=tcb_status,
        meta={
            "family_id": c["family_id"],
            "image_id": c["image_id"],
            "id_key_digest": c["id_key_digest"],
            "author_key_digest": c["author_key_digest"],
            "report_id": c["report_id"],
            "report_id_ma": c["report_id_ma"],
            "chip_id": c["chip_id"],
            "signature_algo": c["signature_algo"],
            "current_mitigation_vector": c["current_mitigation_vector"],
            "launch": c["launch"],
            "platform_info_raw": c["platform_info"],
        },
    )
    return evidence

# Convenience: compute the transcript hash (not part of AMD spec; used by upper layers)
def transcript_hash(report: bytes) -> str:
    """
    Animica helper: SHA3-256 over the signed region.
    """
    region = report[:SIGNATURE_OFFSET]
    return hashlib.sha3_256(region).hexdigest()

__all__ = [
    "parse_snp_report",
    "verify_sev_snp_attestation",
    "verify_report_signature",
    "verify_chain_simple",
    "transcript_hash",
]
