"""
Animica | proofs.attestations.tee.sgx

Intel SGX / (basic) TDX quote parsing and best-effort verification helpers.

This module provides:
  - parse_quote_sgx_v3: extract SGX REPORTBODY fields (MRENCLAVE, MRSIGNER, ISVPRODID, ISVSVN, DEBUG)
  - verify_pck_chain: lightweight validity checks for an Intel PCK certificate chain (PEM bundle)
  - summarize_tcb_status: derive a coarse TCBStatus from QE identity JSON (if provided)
  - verify_quote_sgx: one-shot wrapper that returns a TEEEvidence object suitable for
    policy evaluation (proofs.attestations.tee.common.evaluate_attestation)

Notes & trade-offs
------------------
* Full Intel "DCAP" verification entails cryptographically validating the
  quote signature against the PCK leaf, chaining to Intel roots, consulting
  CRLs/TCB-info, and matching QE identity. That is beyond this reference
  verifier's scope. Here we perform:
    - Robust parsing of the SGX quote v3 header and REPORTBODY (constant offsets)
    - Optional parsing of a PEM bundle containing PCK certificates:
      * If python 'cryptography' is available: check leaf validity window
        and basic chain structure (no internet; no OCSP/CRLs)
      * Otherwise: treat chain as unverified (chain_ok=False)
    - Map QE identity JSON status strings to a coarse TCBStatus (optional)

* The resulting TEEEvidence has:
    claims = {
      "vendor": "intel",
      "product": "sgx" | "tdx",
      "report_version": 3,
      "mrenclave": <32B>,
      "mrsigner": <32B>,
      "isvprodid": <int>,
      "isvsvn": <int>,
      "debug": <0|1>,
    }
  Unknown fields for TDX are omitted (we do not parse TDREPORT here).

Security model
--------------
This module never marks chain_ok=True unless:
  - a PEM bundle parses and (if cryptography is present) the leaf is currently valid.
It never throws on missing 'cryptography'; instead it downgrades to chain_ok=False.

Downstream, apply policy checks with AttestationPolicy (require_chain_ok, require_tcb_up_to_date, etc).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from proofs.attestations.tee.common import TCBStatus, TEEEvidence, TEEKind
from proofs.errors import AttestationError

# Optional: use cryptography for PCK parsing if available
try:
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend
except Exception:  # pragma: no cover - environment without cryptography
    x509 = None  # type: ignore[assignment]


# ──────────────────────────────────────────────────────────────────────────────
# SGX Quote v3 constants (ECDSA quotes)
# Layout reference (public Intel DCAP docs):
#   - Header: 48 bytes
#   - ReportBody: 384 bytes (SGX REPORTBODY)
#   - SignatureData: variable length (not parsed here)
# REPORTBODY offsets (bytes):
#   attributes @ 48 (16 bytes) → flags[0:8], xfrm[8:16]
#   mrenclave  @ 64 (32 bytes)
#   mrsigner   @ 128 (32 bytes)
#   isvprodid  @ 256 (2 bytes, LE)
#   isvsvn     @ 258 (2 bytes, LE)
# The DEBUG flag is bit 1 (0x00000002) of attributes.flags (little-endian u64).
# ──────────────────────────────────────────────────────────────────────────────

Q_HEADER_SIZE = 48
Q_REPORT_BODY_SIZE = 384

# SGX attributes.flags debug bit
SGX_FLAGS_DEBUG = 0x00000002

# Quote header fields (subset)
# struct {
#   uint16_t version;
#   uint16_t att_key_type;
#   uint32_t tee_type;         // 0x00000000 = SGX; 0x00000081 = TDX (for ECDSA quotes)
#   uint16_t qe_svn;
#   uint16_t pce_svn;
#   uint8_t  qe_vendor_id[16];
#   uint8_t  user_data[20];
# } sgx_quote_header_t;


@dataclass
class QuoteHeader:
    version: int
    att_key_type: int
    tee_type: int
    qe_svn: int
    pce_svn: int
    qe_vendor_id: bytes
    user_data: bytes


@dataclass
class ReportBody:
    attributes_flags: int
    attributes_xfrm: int
    mrenclave: bytes
    mrsigner: bytes
    isvprodid: int
    isvsvn: int
    debug: bool


# ──────────────────────────────────────────────────────────────────────────────
# Parsers
# ──────────────────────────────────────────────────────────────────────────────


def parse_quote_header(quote: bytes) -> QuoteHeader:
    if len(quote) < Q_HEADER_SIZE:
        raise AttestationError(
            f"SGX quote too short for header: {len(quote)} < {Q_HEADER_SIZE}"
        )
    (
        version,
        att_key_type,
        tee_type,
        qe_svn,
        pce_svn,
    ) = struct.unpack_from("<HHIHH", quote, 0)
    qe_vendor_id = quote[12:28]
    user_data = quote[28:48]
    return QuoteHeader(
        version=version,
        att_key_type=att_key_type,
        tee_type=tee_type,
        qe_svn=qe_svn,
        pce_svn=pce_svn,
        qe_vendor_id=qe_vendor_id,
        user_data=user_data,
    )


def parse_report_body_sgx(report_body: bytes) -> ReportBody:
    if len(report_body) < Q_REPORT_BODY_SIZE:
        raise AttestationError(
            f"SGX report body too short: {len(report_body)} < {Q_REPORT_BODY_SIZE}"
        )
    # attributes at offset 48
    flags_le = struct.unpack_from("<Q", report_body, 48)[0]
    xfrm_le = struct.unpack_from("<Q", report_body, 56)[0]
    # mrenclave @ 64, 32 bytes
    mr_enclave = report_body[64:96]
    # mrsigner @ 128, 32 bytes
    mr_signer = report_body[128:160]
    # isvprodid @ 256, isvsvn @ 258
    isvprodid = struct.unpack_from("<H", report_body, 256)[0]
    isvsvn = struct.unpack_from("<H", report_body, 258)[0]
    debug = (flags_le & SGX_FLAGS_DEBUG) != 0
    return ReportBody(
        attributes_flags=flags_le,
        attributes_xfrm=xfrm_le,
        mrenclave=mr_enclave,
        mrsigner=mr_signer,
        isvprodid=isvprodid,
        isvsvn=isvsvn,
        debug=debug,
    )


def parse_quote_sgx_v3(quote: bytes) -> Tuple[QuoteHeader, Optional[ReportBody]]:
    """
    Parse the SGX quote header, and if tee_type indicates SGX (0x0),
    parse the REPORTBODY from bytes 48..432.

    If tee_type suggests TDX (0x81) we do NOT attempt to parse TDREPORT here
    (structurally different); we return (header, None).
    """
    header = parse_quote_header(quote)
    if header.tee_type == 0x00000000:
        # Plain SGX: REPORTBODY follows immediately.
        report_body = parse_report_body_sgx(
            quote[Q_HEADER_SIZE : Q_HEADER_SIZE + Q_REPORT_BODY_SIZE]
        )
        return header, report_body
    else:
        # Likely TDX: we keep the header but skip body parsing.
        return header, None


# ──────────────────────────────────────────────────────────────────────────────
# PCK certificate bundle (PEM) — lightweight checks
# ──────────────────────────────────────────────────────────────────────────────


def verify_pck_chain(
    pem_bundle: Optional[bytes],
) -> Tuple[bool, Optional[datetime], Optional[datetime]]:
    """
    Best-effort check of PCK certificate bundle.

    Strategy:
      - If no bundle provided ⇒ (False, None, None)
      - If 'cryptography' is unavailable ⇒ (False, None, None)
      - Else:
          * parse all PEM certs
          * consider the first cert the "leaf"
          * if now ∈ [not_before, not_after] for leaf ⇒ chain_ok=True
            (we do not perform full path validation or CRL checks here)
          * return the leaf validity window

    This is intentionally conservative; callers can enforce stronger policies.
    """
    if not pem_bundle:
        return False, None, None
    if x509 is None:
        return False, None, None  # cannot meaningfully validate

    certs = []
    rest = pem_bundle
    backend = default_backend()
    # Split multiple PEM blocks
    while True:
        try:
            cert = x509.load_pem_x509_certificate(rest, backend)  # type: ignore[attr-defined]
            certs.append(cert)
            # Trim the parsed cert from 'rest' (naive but works with well-formed PEMs)
            end_marker = b"-----END CERTIFICATE-----"
            idx = rest.find(end_marker)
            if idx == -1:
                break
            rest = rest[idx + len(end_marker) :]
        except Exception:
            break

    if not certs:
        return False, None, None

    leaf = certs[0]
    now = datetime.now(timezone.utc)
    nb = leaf.not_valid_before.replace(tzinfo=timezone.utc)  # type: ignore[attr-defined]
    na = leaf.not_valid_after.replace(tzinfo=timezone.utc)  # type: ignore[attr-defined]
    chain_ok = nb <= now <= na
    return chain_ok, nb, na


# ──────────────────────────────────────────────────────────────────────────────
# QE Identity (JSON) → coarse TCB status mapping (optional)
# ──────────────────────────────────────────────────────────────────────────────


def summarize_tcb_status(qe_identity_json: Optional[bytes]) -> TCBStatus:
    """
    Parse Intel QE identity JSON (or compatible) and derive a coarse TCBStatus.

    We look for any tcbLevels[].status; map:
      "UpToDate" → UP_TO_DATE
      "OutOfDate" | "ConfigurationNeeded" → OUT_OF_DATE
      "Revoked" → REVOKED
      otherwise → UNKNOWN

    If multiple levels present, we conservatively take the *worst*.
    """
    if not qe_identity_json:
        return TCBStatus.UNKNOWN
    try:
        import json

        data = json.loads(qe_identity_json.decode("utf-8"))
        levels = data.get("tcbLevels") or []
        worst = TCBStatus.UP_TO_DATE
        for lvl in levels:
            status = (lvl.get("status") or "").lower()
            if "revoked" in status:
                worst = TCBStatus.REVOKED
                break
            if "outofdate" in status or "configurationneeded" in status:
                worst = TCBStatus.OUT_OF_DATE
        return worst
    except Exception:
        return TCBStatus.UNKNOWN


# ──────────────────────────────────────────────────────────────────────────────
# One-shot wrapper: quote → TEEEvidence
# ──────────────────────────────────────────────────────────────────────────────


def verify_quote_sgx(
    quote: bytes,
    pck_chain_pem: Optional[bytes] = None,
    qe_identity_json: Optional[bytes] = None,
) -> TEEEvidence:
    """
    Parse the SGX (or TDX) quote and return normalized TEEEvidence.

    This does *not* perform full DCAP verification; see notes above.
    """
    header, rb = parse_quote_sgx_v3(quote)

    product = "sgx" if header.tee_type == 0x00000000 else "tdx"
    claims: Dict[str, object] = {
        "vendor": "intel",
        "product": product,
        "report_version": header.version,
    }

    if rb is not None:
        claims.update(
            {
                "mrenclave": rb.mrenclave,
                "mrsigner": rb.mrsigner,
                "isvprodid": rb.isvprodid,
                "isvsvn": rb.isvsvn,
                "debug": 1 if rb.debug else 0,
            }
        )
    else:
        # TDX path (no TDREPORT parse here); set debug=0 by default.
        claims["debug"] = 0

    chain_ok, nb, na = verify_pck_chain(pck_chain_pem)
    tcb_status = summarize_tcb_status(qe_identity_json)

    return TEEEvidence(
        kind=TEEKind.SGX,  # treat TDX under Intel umbrella for now
        report=quote,
        claims=claims,
        chain_ok=chain_ok,
        tcb_status=tcb_status,
        not_before=nb,
        not_after=na,
    )


__all__ = [
    "QuoteHeader",
    "ReportBody",
    "parse_quote_header",
    "parse_report_body_sgx",
    "parse_quote_sgx_v3",
    "verify_pck_chain",
    "summarize_tcb_status",
    "verify_quote_sgx",
]
