from __future__ import annotations

"""
Self-signed node certificate for QUIC/TLS (aioquic)
===================================================

This module creates/loads a *TLS* X.509 certificate used only to establish the
encrypted transport (QUIC/TLS 1.3). It does **not** replace the node's PQ
identity keys used at the P2P layer. We deliberately keep those concerns
separate:

- TLS keypair: Ed25519 (fast, widely supported by OpenSSL/cryptography)
- Node identity (P2P signing): Dilithium3 or SPHINCS+ (see pq/ and p2p.crypto.keys)

We *bind* the TLS cert to the P2P identity by embedding the Animica peer-id
(as defined in p2p.crypto.peer_id) into the certificate's SubjectAlternativeName
as a `URI` entry of the form:

    URI: animica:peerid:<64-hex>

This allows peers to sanity-check that the presented TLS cert is intended for
the same logical node they later authenticate at the P2P layer.

Typical usage (server side)
---------------------------
>>> from p2p.crypto.keys import generate
>>> from p2p.crypto.cert import load_or_create_cert, tls_server_context
>>> ident = generate("dilithium3")
>>> crt, key = load_or_create_cert(ident, dirpath="~/.animica/p2p/certs")
>>> ctx = tls_server_context(crt, key)  # pass to aioquic QuicConfiguration

Typical usage (client side)
---------------------------
Clients usually do not need a certificate, but they may present one for
symmetry. Verification of the peer-id happens at the P2P HELLO after QUIC
comes up. You can still parse and compare SAN peer-id if desired.

Notes
-----
- We generate Ed25519 certs (cryptography >= 35 recommended).
- ALPN is set to ["animica/1"] on the SSL context.
- Files are PEM-encoded and persisted atomically.

"""

import binascii
import datetime as dt
import os
import ssl
from pathlib import Path
from typing import Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

# Local deps (no heavy imports at module top to avoid cycles)
from p2p.crypto.peer_id import (format_peer_id_short, is_valid_peer_id_hex,
                                peer_id_hex_from_identity)

ALPN = "animica/1"
SAN_URI_PREFIX = "animica:peerid:"

# ---------- generation / persistence -----------------------------------------


def _now_utc() -> dt.datetime:
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc)


def _expand(p: str | os.PathLike[str]) -> Path:
    return Path(os.path.expanduser(os.fspath(p))).resolve()


def generate_self_signed_pem(
    *,
    common_name: str,
    peer_id_hex: str,
    days_valid: int = 3650,
) -> Tuple[bytes, bytes]:
    """
    Create a self-signed X.509 cert/key (Ed25519) and return (cert_pem, key_pem).
    Embeds SAN URI `animica:peerid:<hex>`.
    """
    if not is_valid_peer_id_hex(peer_id_hex):
        raise ValueError("peer_id_hex must be 64 hex chars")

    # Generate TLS keypair (Ed25519)
    key = ed25519.Ed25519PrivateKey.generate()
    pub = key.public_key()

    # Subject / Issuer (self-signed)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Animica"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )

    not_before = _now_utc() - dt.timedelta(minutes=5)
    not_after = not_before + dt.timedelta(days=days_valid)

    # SAN with our binding to the P2P peer-id
    san = x509.SubjectAlternativeName(
        [x509.UniformResourceIdentifier(SAN_URI_PREFIX + peer_id_hex)]
    )

    # Extended Key Usage: serverAuth + clientAuth (QUIC usually uses serverAuth)
    eku = x509.ExtendedKeyUsage(
        [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
    )

    serial = x509.random_serial_number()

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(pub)
        .serial_number(serial)
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(san, critical=False)
        .add_extension(eku, critical=False)
    )

    cert = builder.sign(private_key=key, algorithm=hashes.SHA256())
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def save_pem_pair(
    cert_pem: bytes, key_pem: bytes, *, cert_path: Path, key_path: Path
) -> None:
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    # Atomic writes
    tmpc = cert_path.with_suffix(cert_path.suffix + ".tmp")
    tmpk = key_path.with_suffix(key_path.suffix + ".tmp")
    tmpc.write_bytes(cert_pem)
    tmpk.write_bytes(key_pem)
    os.replace(tmpc, cert_path)
    os.replace(tmpk, key_path)
    os.chmod(key_path, 0o600)


def load_or_create_cert(
    identity: "NodeIdentity",
    *,
    dirpath: str | os.PathLike[str] = "~/.animica/p2p/certs",
    basename: Optional[str] = None,
    days_valid: int = 3650,
) -> Tuple[Path, Path]:
    """
    Load existing cert/key from disk, or create a new pair if missing.
    The filenames are derived from the peer-id for uniqueness.
    Returns (cert_path, key_path).
    """
    # Local import to avoid hard dependency at module import
    from p2p.crypto.keys import NodeIdentity  # type: ignore

    if not isinstance(identity, NodeIdentity):
        raise TypeError("identity must be p2p.crypto.keys.NodeIdentity")

    pid_hex = peer_id_hex_from_identity(identity)
    short = format_peer_id_short(pid_hex, sep="", head=8, tail=0)
    base = basename or f"node-{short}"
    d = _expand(dirpath)
    cert_path = d / f"{base}.crt"
    key_path = d / f"{base}.key"

    if cert_path.exists() and key_path.exists():
        # Optional: validate SAN contains our peer-id
        try:
            crt = x509.load_pem_x509_certificate(cert_path.read_bytes())
            san_pid = extract_peer_id_from_cert(crt)
            if san_pid and san_pid.lower() == pid_hex.lower():
                return cert_path, key_path
        except Exception:
            pass
        # If mismatch or parse error, regenerate to be safe
    cn = f"Animica Node {format_peer_id_short(pid_hex)}"
    cert_pem, key_pem = generate_self_signed_pem(
        common_name=cn, peer_id_hex=pid_hex, days_valid=days_valid
    )
    save_pem_pair(cert_pem, key_pem, cert_path=cert_path, key_path=key_path)
    return cert_path, key_path


# ---------- parsing / validation ---------------------------------------------


def load_cert(path: str | os.PathLike[str]) -> x509.Certificate:
    return x509.load_pem_x509_certificate(_expand(path).read_bytes())


def fingerprint_sha256(cert: x509.Certificate) -> str:
    return binascii.hexlify(cert.fingerprint(hashes.SHA256())).decode("ascii")


def extract_peer_id_from_cert(cert: x509.Certificate) -> Optional[str]:
    """
    Pull animica:peerid:<hex> from SAN URIs if present.
    """
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return None
    for uri in san.get_values_for_type(x509.UniformResourceIdentifier):
        if uri.startswith(SAN_URI_PREFIX):
            pid = uri[len(SAN_URI_PREFIX) :]
            if is_valid_peer_id_hex(pid):
                return pid.lower()
    return None


def cert_matches_identity(cert: x509.Certificate, identity: "NodeIdentity") -> bool:
    """
    Check SAN-embedded peer-id equals the peer-id derived from the NodeIdentity.
    """
    pid = extract_peer_id_from_cert(cert)
    if pid is None:
        return False
    expected = peer_id_hex_from_identity(identity).lower()
    return pid == expected


# ---------- SSL contexts (TLS/QUIC) -----------------------------------------


def tls_server_context(
    cert_path: str | os.PathLike[str], key_path: str | os.PathLike[str]
) -> ssl.SSLContext:
    """
    Build a TLS server context for QUIC/TLS 1.3 with ALPN=[animica/1].
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # TLS 1.3 only (QUIC requires TLS 1.3)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    ctx.set_alpn_protocols([ALPN])
    ctx.load_cert_chain(
        certfile=os.fspath(_expand(cert_path)), keyfile=os.fspath(_expand(key_path))
    )
    # Reasonable defaults
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def tls_client_context() -> ssl.SSLContext:
    """
    Client context for QUIC/TLS 1.3 with ALPN=[animica/1].
    Certificate verification is disabled here because P2P authentication is done
    at the protocol layer; callers may enable verification as desired.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.maximum_version = ssl.TLSVersion.TLSv1_3
    ctx.set_alpn_protocols([ALPN])
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ---------- CLI helper -------------------------------------------------------


def _cli() -> None:  # pragma: no cover
    import argparse

    from p2p.crypto.keys import \
        load_or_generate_identity  # convenience (user-defined helper)

    ap = argparse.ArgumentParser(
        description="Generate or show Animica node TLS certificate bound to peer-id."
    )
    ap.add_argument(
        "--dir",
        default="~/.animica/p2p/certs",
        help="Directory to store cert/key (default: %(default)s)",
    )
    ap.add_argument(
        "--basename", default=None, help="File base name (default: node-<peerid8>)."
    )
    ap.add_argument(
        "--days", type=int, default=3650, help="Validity in days (default: %(default)s)"
    )
    args = ap.parse_args()

    ident = load_or_generate_identity()  # implement in p2p.crypto.keys as a convenience
    cert_path, key_path = load_or_create_cert(
        ident, dirpath=args.dir, basename=args.basename, days_valid=args.days
    )
    crt = load_cert(cert_path)
    print(f"wrote: {cert_path}")
    print(f"wrote: {key_path}")
    print(f"subject: {crt.subject.rfc4514_string()}")
    print(f"issuer : {crt.issuer.rfc4514_string()}")
    print(f"valid  : {crt.not_valid_before_utc} .. {crt.not_valid_after_utc}")
    pid = extract_peer_id_from_cert(crt) or "<none>"
    print(f"peer-id: {pid} ({format_peer_id_short(pid) if pid!='<none>' else ''})")
    print(f"fp256  : {fingerprint_sha256(crt)}")
    print(f"ALPN   : {ALPN}")


if __name__ == "__main__":  # pragma: no cover
    _cli()
