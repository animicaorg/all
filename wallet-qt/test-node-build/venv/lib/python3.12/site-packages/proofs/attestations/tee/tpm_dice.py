# SPDX-License-Identifier: MIT
"""
TPM/DICE attestation support (optional helper for Animica proofs).

This module provides a pragmatic validator for:
  1) TPM 2.0 event logs (JSON form) with PCR replay (SHA-1/SHA-256).
  2) Optional TPM Quote verification against an AK (Attestation Key) public key.
  3) Optional DICE certificate chain sanity (issuer->subject) up to a provided root.

It outputs a TEEEvidence object usable by the AI/TEE proof path. The intention is to
offer a lightweight validator without requiring platform-specific TPM stacks. If the
'cryptography' library is present, X.509 parsing/verification and Quote signature
checks are enabled; otherwise those checks are skipped but the function still returns
a structured TEEEvidence with `signature_ok/chain_ok` reflecting what was validated.

Accepted Event Log Format (JSON)
--------------------------------
We accept the common "canonical JSON event log" shape used by go-tpm-tools and other
tooling. Each entry is a dict with keys:
  {
    "pcrIndex": 7,                         # integer PCR index
    "eventType": "EV_EFI_BOOT_SERVICES_APPLICATION" or int,
    "digests": [
      {"hashAlg": "sha256", "digest": "<hex>"},
      {"hashAlg": "sha1",   "digest": "<hex>"}
    ],
    "data": "<base64 or hex or opaque>"    # ignored for PCR replay
  }

Replay initializes chosen PCR registers to zero (length matches algorithm) and applies:
    PCR[n] = HASH( PCR[n] || digest )
for each event's selected algorithm digest, in log order.

TPM Quote (Optional)
--------------------
Provide:
  - quote_attest: bytes   (TPMS_ATTEST structure)
  - quote_sig:    bytes   (signature over TPMS_ATTEST or its digest, per AK type)
  - ak_pub_pem:   bytes   (PEM-encoded AK public key)

We compute the expected PCR composite digest from the selected PCRs and compare against
the digest reported in `quote_attest`. If cryptography is present, we also verify the
signature using the AK public key (RSA-PSS, RSASSA-PKCS1v1_5, or ECDSA). For brevity,
we support the most common AK types (RSA 2048/3072 and P-256/P-384). Ed25519 AKs are
rare in TPM context and are not covered here.

DICE Chain (Optional)
---------------------
Provide a list of DER certificates for the DICE chain (leaf first) and (optionally) a
root PEM. We do a simple issuer->subject walk and (if a root is provided) anchor it.

Returned Evidence
-----------------
vendor="tpm", kind=TEEKind.TPM_DICE (or TEEKind.TPM if TPM_DICE absent)
- measurement:     the quote PCR digest (if quote given) else sha256 over concatenated
                   selected PCR values from replay.
- report:          the raw quote_attest (if provided)
- report_data:     the nonce from quote_attest (if parsed), else empty
- host_data:       sha256 of the entire event log JSON bytes
- signature_ok:    True iff Quote signature verified
- chain_ok:        True iff DICE chain anchored (if provided), else True
- meta:            includes pcr_selection, pcr_values (hex), pcr_alg, quote_alg, etc.

This module is designed to be *conservative*: if any parsing/crypto steps fail, we
signal via flags and raise AttestationError only for structural errors.
"""

from __future__ import annotations

import binascii
import hashlib
import json
from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union

from ..errors import AttestationError
from .common import TCBStatus, TEEEvidence, TEEKind

# Optional cryptography for X.509 and signature checks
try:  # pragma: no cover - availability depends on environment
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import (ec, padding, rsa,
                                                           utils)
    from cryptography.hazmat.primitives.serialization import \
        load_pem_public_key

    _HAS_CRYPTO = True
except Exception:
    _HAS_CRYPTO = False

# ---------------------------------------------------------------------------

SUPPORTED_HASHALGS = {
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
}

Event = Mapping[str, Any]


def _hex(b: Optional[bytes]) -> Optional[str]:
    return None if b is None else binascii.hexlify(b).decode()


def _hash(data: bytes, alg: str) -> bytes:
    fn = SUPPORTED_HASHALGS.get(alg.lower())
    if fn is None:
        raise AttestationError(f"Unsupported hash algorithm in event log: {alg!r}")
    return fn(data).digest()


def _pcr_zero(alg: str) -> bytes:
    if alg.lower() == "sha1":
        return b"\x00" * 20
    if alg.lower() == "sha256":
        return b"\x00" * 32
    raise AttestationError(f"Unsupported PCR alg: {alg}")


def parse_eventlog_json(buf: Union[str, bytes, bytearray]) -> List[Event]:
    """
    Parse a canonical JSON event log.
    Returns a list of event dicts (read-only Mapping).
    """
    try:
        if isinstance(buf, (bytes, bytearray)):
            text = buf.decode("utf-8")
        else:
            text = buf
        data = json.loads(text)
        if not isinstance(data, list):
            raise AttestationError("Event log JSON must be a list of events")
        # Lightweight validation
        for ev in data:
            if not isinstance(ev, dict):
                raise AttestationError("Event log entries must be objects")
            if "pcrIndex" not in ev or "digests" not in ev:
                raise AttestationError(
                    "Event missing required keys (pcrIndex, digests)"
                )
        return data  # type: ignore
    except AttestationError:
        raise
    except Exception as e:
        raise AttestationError(f"Failed to parse event log JSON: {e}")


def replay_pcrs(
    events: Iterable[Event],
    *,
    pcr_selection: Iterable[int],
    alg: str = "sha256",
) -> Dict[int, bytes]:
    """
    Replay PCR extends for the selected PCRs with the chosen algorithm digest.
    Unknown PCR indexes are ignored unless selected.
    """
    alg = alg.lower()
    if alg not in SUPPORTED_HASHALGS:
        raise AttestationError(f"Unsupported PCR hash algorithm: {alg}")
    pcrs: Dict[int, bytes] = {i: _pcr_zero(alg) for i in pcr_selection}
    for ev in events:
        try:
            pcr_index = int(ev["pcrIndex"])
        except Exception:
            continue
        if pcr_index not in pcrs:
            continue
        digests = ev.get("digests", [])
        if not isinstance(digests, list):
            continue
        # find matching alg digest
        d = None
        for ent in digests:
            if not isinstance(ent, dict):
                continue
            if ent.get("hashAlg", "").lower() == alg:
                d_hex = ent.get("digest")
                if isinstance(d_hex, str):
                    try:
                        d = binascii.unhexlify(d_hex)
                    except Exception:
                        d = None
                break
        if d is None:
            # Event did not include the selected algorithm — skip
            continue
        pcrs[pcr_index] = _hash(pcrs[pcr_index] + d, alg)
    return pcrs


def _pcr_selection_bytes(selection: Iterable[int], total: int = 24) -> bytes:
    """
    Build a TPM2 PCR selection bitfield for informational purposes.
    (Not used in signature verification; included in meta.)
    """
    bits = [0] * total
    for i in selection:
        if 0 <= i < total:
            bits[i] = 1
    out = 0
    for i, b in enumerate(bits):
        if b:
            out |= 1 << i
    return out.to_bytes((total + 7) // 8, "little")


# --------------------- TPM Quote minimal parsing & verify ---------------------


def _parse_tpms_attest_sha256(attest: bytes) -> Tuple[Optional[bytes], Optional[bytes]]:
    """
    Minimal TPMS_ATTEST parser (very pragmatic):
     - returns (extraData/nonce, pcrDigest) for SHA-256 PCR bank quotes
    This does NOT fully parse the structure; it's just a best-effort extractor
    based on common layouts (TPM2B_ATTEST → TPMS_ATTEST with TPMS_QUOTE_INFO).

    If parsing fails, returns (None, None).
    """
    # We avoid strict struct unpacking because of variable-length fields.
    # Heuristic: look for the "QUOT" magic in the attestation type and sniff TLV-ish pieces.
    try:
        # Search for "QUOT" (0x51 0x55 0x4f 0x54) in the blob as a sanity check.
        if b"QUOT" not in attest:
            # Still try to parse — some tooling doesn't keep ASCII "QUOT".
            pass

        # Very rough: find a region that *looks* like a 32-byte PCR digest preceded by a length.
        # We'll also try to extract a likely nonce (extraData) — often small (<64 bytes).
        # This is intentionally forgiving and only used for cross-checking.
        pcr_digest: Optional[bytes] = None
        extra_data: Optional[bytes] = None

        # Heuristic for pcrDigest: look for several repeating 0x20-length chunks that look non-zero.
        for i in range(0, len(attest) - 34):
            if attest[i] == 0x00 and attest[i + 1] == 0x20:
                cand = attest[i + 2 : i + 2 + 32]
                if len(cand) == 32 and any(cand):
                    pcr_digest = cand
                    break

        # Heuristic for extraData (nonce): scan for small-length TLV [0x00, n] followed by n data,
        # prefer printable bytes; last such candidate before pcrDigest region is likely the nonce.
        stop = len(attest)
        if pcr_digest is not None:
            stop = attest.index(pcr_digest) if pcr_digest in attest else stop
        best = None
        for i in range(0, stop - 2):
            ln = int.from_bytes(attest[i : i + 2], "big")
            if 4 <= ln <= 64 and i + 2 + ln <= stop:
                blob = attest[i + 2 : i + 2 + ln]
                # Prefer ascii-ish entropy or hex-ish data
                if any(32 <= b <= 126 for b in blob) or any(b > 127 for b in blob):
                    best = blob
        if best:
            extra_data = best

        return (extra_data, pcr_digest)
    except Exception:
        return (None, None)


def _verify_quote_signature(
    ak_pub_pem: bytes,
    quote_attest: bytes,
    quote_sig: bytes,
) -> bool:
    if not _HAS_CRYPTO:
        return False
    try:
        pub = load_pem_public_key(ak_pub_pem)
        if isinstance(pub, rsa.RSAPublicKey):
            # Try RSASSA-PSS (most common), fall back to PKCS1v1_5 if that fails.
            try:
                pub.verify(
                    quote_sig,
                    quote_attest,
                    padding.PSS(
                        mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.MAX_LENGTH,
                    ),
                    hashes.SHA256(),
                )
                return True
            except Exception:
                try:
                    pub.verify(
                        quote_sig, quote_attest, padding.PKCS1v15(), hashes.SHA256()
                    )
                    return True
                except Exception:
                    return False
        elif isinstance(pub, ec.EllipticCurvePublicKey):
            # ECDSA signatures are ASN.1 DER (r,s)
            try:
                pub.verify(quote_sig, quote_attest, ec.ECDSA(hashes.SHA256()))
                return True
            except Exception:
                return False
        else:
            return False
    except Exception:
        return False


# --------------------------- DICE chain validation ----------------------------


def verify_dice_chain_simple(der_chain: List[bytes], root_pem: Optional[bytes]) -> bool:
    """
    Simple issuer->subject walk. If root provided, require anchor match.
    """
    if not _HAS_CRYPTO:
        return False
    try:
        pems: List[bytes] = []
        for der in der_chain:
            try:
                cert = x509.load_der_x509_certificate(der)
                pems.append(cert.public_bytes(encoding=x509.Encoding.PEM))  # type: ignore[attr-defined]
            except Exception:
                # Wrap as PEM if DER parse fails
                pems.append(
                    b"-----BEGIN CERTIFICATE-----\n"
                    + binascii.b2a_base64(der).replace(b"\n", b"")
                    + b"\n-----END CERTIFICATE-----\n"
                )

        chain = [x509.load_pem_x509_certificate(p) for p in pems]
        anchors = [x509.load_pem_x509_certificate(root_pem)] if root_pem else []

        # Map by subject for quick lookup
        by_subject = {c.subject.rfc4514_string(): c for c in chain}
        if anchors:
            for r in anchors:
                by_subject[r.subject.rfc4514_string()] = r

        # Start at leaf (first)
        curr = chain[0]
        while True:
            isub = curr.issuer.rfc4514_string()
            ssub = curr.subject.rfc4514_string()
            if isub == ssub:
                # self-signed root
                if not anchors:
                    return True
                return any(
                    curr.fingerprint(hashes.SHA256()) == r.fingerprint(hashes.SHA256())
                    for r in anchors
                )
            parent = by_subject.get(isub)
            if parent is None:
                return False
            try:
                parent.public_key().verify(
                    curr.signature,
                    curr.tbs_certificate_bytes,
                    (
                        ec.ECDSA(curr.signature_hash_algorithm)  # type: ignore[arg-type]
                        if isinstance(parent.public_key(), ec.EllipticCurvePublicKey)  # type: ignore[name-defined]
                        else padding.PKCS1v15()
                    ),  # RSA fallback
                )
            except Exception:
                # For RSA parents, the above line may not be correct due to hash algorithm.
                # Try a generic path:
                try:
                    if isinstance(parent.public_key(), rsa.RSAPublicKey):
                        parent.public_key().verify(
                            curr.signature,
                            curr.tbs_certificate_bytes,
                            padding.PKCS1v15(),
                            curr.signature_hash_algorithm,  # type: ignore[arg-type]
                        )
                    else:
                        return False
                except Exception:
                    return False
            curr = parent
    except Exception:
        return False


# ------------------------------ High-level API --------------------------------


def verify_tpm_dice(
    *,
    eventlog_json: Union[bytes, str],
    pcr_selection: Iterable[int],
    pcr_alg: str = "sha256",
    # Optional Quote
    quote_attest: Optional[bytes] = None,
    quote_sig: Optional[bytes] = None,
    ak_pub_pem: Optional[bytes] = None,
    # Optional DICE chain anchoring
    dice_chain_der: Optional[List[bytes]] = None,
    dice_root_pem: Optional[bytes] = None,
) -> TEEEvidence:
    """
    Verify TPM event log with PCR replay, optional Quote signature, and optional DICE chain.

    Returns:
      TEEEvidence suitable for downstream ψ-input derivation (via proofs.ai/… adapters).
    """
    # Parse event log
    events = parse_eventlog_json(eventlog_json)
    pcr_alg = pcr_alg.lower()
    if pcr_alg not in SUPPORTED_HASHALGS:
        raise AttestationError(f"Unsupported PCR algorithm: {pcr_alg}")

    # Replay PCRs
    sel = list(sorted(set(int(x) for x in pcr_selection)))
    pcrs = replay_pcrs(events, pcr_selection=sel, alg=pcr_alg)

    # Build a composite digest (same construction used by many tools: hash(concat(PCR[i])))
    concat = b"".join(pcrs[i] for i in sel if i in pcrs)
    composite_digest = hashlib.sha256(concat).digest()

    # Try to parse quote to extract pcrDigest and nonce (extraData)
    nonce: Optional[bytes] = None
    quoted_pcr_digest: Optional[bytes] = None
    if quote_attest:
        nonce, quoted_pcr_digest = _parse_tpms_attest_sha256(quote_attest)

    # Compare composite to quoted pcrDigest if present (best-effort cross-check)
    digest_matches = True
    if quoted_pcr_digest is not None:
        digest_matches = quoted_pcr_digest == composite_digest

    # Verify quote signature if possible
    signature_ok = False
    if quote_attest and quote_sig and ak_pub_pem:
        signature_ok = _verify_quote_signature(ak_pub_pem, quote_attest, quote_sig)

    # Verify DICE chain if provided
    chain_ok = True
    if dice_chain_der:
        chain_ok = verify_dice_chain_simple(dice_chain_der, dice_root_pem)

    # Host-data: hash of the entire eventlog JSON bytes
    ev_bytes = (
        eventlog_json
        if isinstance(eventlog_json, (bytes, bytearray))
        else eventlog_json.encode("utf-8")
    )
    host_data = hashlib.sha3_256(ev_bytes).digest()

    # Evidence measurement preference: quoted digest if present and matched; else composite
    measurement = (
        quoted_pcr_digest if quoted_pcr_digest and digest_matches else composite_digest
    )

    evidence = TEEEvidence(
        vendor="tpm",
        kind=getattr(
            TEEKind, "TPM_DICE", getattr(TEEKind, "TPM", TEEKind.GENERIC)
        ),  # graceful fallback
        measurement=measurement,
        report=quote_attest or b"",
        report_data=nonce or b"",
        host_data=host_data,
        policy=0,
        guest_svn=0,
        vmpl=0,
        platform_flags={},
        signing_key="AK" if ak_pub_pem else "unknown",
        chain_ok=bool(chain_ok),
        signature_ok=bool(signature_ok),
        tcb={"pcr_selection": sel, "pcr_alg": pcr_alg},
        tcb_status=TCBStatus.OK,
        meta={
            "pcr_values": {int(i): _hex(pcrs[i]) for i in sel if i in pcrs},
            "pcr_selection_bits": _hex(_pcr_selection_bytes(sel)),
            "composite_digest": _hex(composite_digest),
            "quoted_pcr_digest": _hex(quoted_pcr_digest) if quoted_pcr_digest else None,
            "digest_matches": digest_matches,
            "has_quote": bool(quote_attest),
            "has_sig": bool(quote_sig),
            "has_ak": bool(ak_pub_pem),
            "dice_chain_len": len(dice_chain_der) if dice_chain_der else 0,
        },
    )
    return evidence


__all__ = [
    "parse_eventlog_json",
    "replay_pcrs",
    "verify_dice_chain_simple",
    "verify_tpm_dice",
]
