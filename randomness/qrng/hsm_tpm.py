"""
randomness.qrng.hsm_tpm
=======================

Hardware-rooted signing for Quantum Useful Work contributions.

The trust root for QUW is **not** the QRNG card (which only produces bytes) but a
tamper-resistant signer that attests "these entropy bytes + this health report
were produced on my platform at this time, bound to the verifier's round nonce."
We support, in order of preference:

  1. YubiHSM 2 via PKCS#11 (ECDSA-P256 / Ed25519 key resident in the HSM).
  2. TPM 2.0 (sign + PCR quote binding platform state).
  3. Software self-signer (Ed25519) — clearly marked NON-ATTESTED, so the whole
     pipeline is runnable/testable without hardware but earns no attested reward.

All signers sign the EXISTING domain-separated transcript hash from
``randomness.qrng.attest.transcript_hash`` (domain ``animica/qrng/attest/v1``),
so verification reuses that machinery. Optional deps (PyKCS11, tpm2-pytss) are
imported lazily; their absence degrades gracefully to the software signer.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

# Ed25519 / ECDSA via 'cryptography' (a hard dep of the project). Imported lazily
# so this module still loads in minimal environments (HMAC fallback then).
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey, Ed25519PublicKey)
    from cryptography.hazmat.primitives.asymmetric import ec, utils as _ecutils
    from cryptography.hazmat.primitives import hashes as _hashes
    from cryptography.exceptions import InvalidSignature
    _HAVE_CRYPTO = True
except Exception:  # pragma: no cover
    _HAVE_CRYPTO = False
    InvalidSignature = Exception  # type: ignore


ALG_ED25519 = "ed25519"
ALG_ECDSA_P256 = "ecdsa-p256"
ALG_HMAC_SHA3 = "hmac-sha3-256"

_DEFAULT_KEY_DIR = os.path.expanduser("~/.animica/quw")
_SOFTWARE_KEY_FILE = "quw_ed25519.key"


@dataclasses.dataclass(frozen=True)
class SignerInfo:
    backend: str          # "yubihsm2" | "tpm2" | "software"
    alg: str              # ALG_*
    attested: bool        # True only for genuine hardware roots
    key_fingerprint: str  # sha256(public_key)[:16] hex
    provider: str
    model: str
    serial: str
    extra: Dict[str, str] = dataclasses.field(default_factory=dict)


class EntropySigner:
    """Common interface for a QUW signer."""

    def public_key(self) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def sign(self, transcript: bytes) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def info(self) -> SignerInfo:  # pragma: no cover - interface
        raise NotImplementedError

    def quote(self, nonce: bytes) -> Optional[bytes]:
        """Optional platform quote (TPM). None when not supported."""
        return None


def _fp(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Software self-signer (Ed25519) — testable, NON-ATTESTED
# --------------------------------------------------------------------------- #


class SoftwareSelfSigner(EntropySigner):
    """
    Ed25519 self-signer. Key is generated once and stored under ~/.animica/quw.
    Verifiable by anyone (public key travels in DeviceIdentity), so the full QUW
    pipeline is exercisable in tests, but ``attested=False`` — the verifier marks
    it non-attested and the scorer discounts it to near-zero reward.
    """

    def __init__(self, key_dir: str = _DEFAULT_KEY_DIR) -> None:
        self._key_dir = key_dir
        if _HAVE_CRYPTO:
            self._alg = ALG_ED25519
            self._sk = self._load_or_create_ed25519()
            self._pk = self._sk.public_key().public_bytes_raw()
        else:  # pragma: no cover - cryptography is normally present
            self._alg = ALG_HMAC_SHA3
            self._hmac_key = self._load_or_create_hmac()
            # "public key" for HMAC is the key id (sha256 of key) — not verifiable
            # by third parties; usable only for in-process/self tests.
            self._pk = hashlib.sha256(self._hmac_key).digest()

    def _ensure_dir(self) -> Path:
        p = Path(self._key_dir)
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(p, 0o700)
        except Exception:
            pass
        return p

    def _load_or_create_ed25519(self):
        path = self._ensure_dir() / _SOFTWARE_KEY_FILE
        if path.exists():
            raw = path.read_bytes()
            if len(raw) == 32:
                return Ed25519PrivateKey.from_private_bytes(raw)
        sk = Ed25519PrivateKey.generate()
        raw = sk.private_bytes_raw()
        path.write_bytes(raw)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return sk

    def _load_or_create_hmac(self) -> bytes:
        path = self._ensure_dir() / "quw_hmac.key"
        if path.exists():
            return path.read_bytes()
        k = os.urandom(32)
        path.write_bytes(k)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
        return k

    def public_key(self) -> bytes:
        return self._pk

    def sign(self, transcript: bytes) -> bytes:
        if self._alg == ALG_ED25519:
            return self._sk.sign(transcript)
        return hmac.new(self._hmac_key, transcript, hashlib.sha3_256).digest()

    def info(self) -> SignerInfo:
        return SignerInfo(
            backend="software", alg=self._alg, attested=False,
            key_fingerprint=_fp(self._pk), provider="animica",
            model="software-fallback", serial="local",
            extra={"warning": "non-attested software signer"},
        )


# --------------------------------------------------------------------------- #
# YubiHSM 2 (PKCS#11) — optional
# --------------------------------------------------------------------------- #


class YubiHSM2Signer(EntropySigner):
    """
    Sign with a key resident in a YubiHSM 2 via a PKCS#11 module
    (e.g. yubihsm_pkcs11.so). Requires the PyKCS11 package and a configured
    connector/auth-key. Raises a clear error if the toolchain is unavailable so
    callers can fall back to the software signer.
    """

    def __init__(
        self,
        *,
        pkcs11_module: Optional[str] = None,
        pin: Optional[str] = None,
        key_label: str = "animica-quw",
        slot: int = 0,
        alg: str = ALG_ECDSA_P256,
    ) -> None:
        try:
            import PyKCS11  # type: ignore
        except Exception as e:  # pragma: no cover - no HSM here
            raise RuntimeError(
                "YubiHSM2 signer requires PyKCS11 and a PKCS#11 module "
                "(e.g. yubihsm_pkcs11.so). Install python-pykcs11 and set "
                "ANIMICA_QUW_PKCS11_MODULE. Falling back to software signer."
            ) from e
        self._PyKCS11 = PyKCS11
        self._module = pkcs11_module or os.environ.get(
            "ANIMICA_QUW_PKCS11_MODULE", "/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so"
        )
        self._pin = pin or os.environ.get("ANIMICA_QUW_PKCS11_PIN", "")
        self._key_label = key_label
        self._slot = slot
        self._alg = alg
        self._pk = self._load_public_key()  # may raise

    def _session(self):  # pragma: no cover - requires HSM
        lib = self._PyKCS11.PyKCS11Lib()
        lib.load(self._module)
        slots = lib.getSlotList(tokenPresent=True)
        if not slots:
            raise RuntimeError("no PKCS#11 token present")
        session = lib.openSession(slots[self._slot])
        if self._pin:
            session.login(self._pin)
        return lib, session

    def _load_public_key(self) -> bytes:  # pragma: no cover - requires HSM
        lib, session = self._session()
        try:
            objs = session.findObjects([
                (self._PyKCS11.CKA_CLASS, self._PyKCS11.CKO_PUBLIC_KEY),
                (self._PyKCS11.CKA_LABEL, self._key_label),
            ])
            if not objs:
                raise RuntimeError(f"no PKCS#11 public key labelled {self._key_label!r}")
            val = session.getAttributeValue(objs[0], [self._PyKCS11.CKA_EC_POINT])[0]
            return bytes(val)
        finally:
            try:
                session.logout()
            except Exception:
                pass

    def public_key(self) -> bytes:
        return self._pk

    def sign(self, transcript: bytes) -> bytes:  # pragma: no cover - requires HSM
        lib, session = self._session()
        try:
            priv = session.findObjects([
                (self._PyKCS11.CKA_CLASS, self._PyKCS11.CKO_PRIVATE_KEY),
                (self._PyKCS11.CKA_LABEL, self._key_label),
            ])
            if not priv:
                raise RuntimeError(f"no PKCS#11 private key labelled {self._key_label!r}")
            mech = self._PyKCS11.Mechanism(self._PyKCS11.CKM_ECDSA, None)
            digest = hashlib.sha256(transcript).digest()
            sig = session.sign(priv[0], digest, mech)
            return bytes(sig)
        finally:
            try:
                session.logout()
            except Exception:
                pass

    def info(self) -> SignerInfo:
        return SignerInfo(
            backend="yubihsm2", alg=self._alg, attested=True,
            key_fingerprint=_fp(self._pk), provider="Yubico",
            model="YubiHSM 2", serial=os.environ.get("ANIMICA_QUW_HSM_SERIAL", "hsm"),
        )


# --------------------------------------------------------------------------- #
# TPM 2.0 — optional (subprocess tpm2-tools)
# --------------------------------------------------------------------------- #


class TPM2Signer(EntropySigner):
    """
    Sign + quote with a TPM 2.0 using the tpm2-tools CLI (tpm2_sign / tpm2_quote)
    over a persistent handle. Requires tpm2-tools + access to /dev/tpmrm0.
    Raises a clear error if unavailable so callers fall back to software.
    """

    def __init__(self, *, handle: str = "0x81010001", pcrs: str = "sha256:0,1,7") -> None:
        if not _tpm2_available():  # pragma: no cover - no TPM here
            raise RuntimeError(
                "TPM2 signer requires tpm2-tools and a TPM (/dev/tpmrm0). "
                "Falling back to software signer."
            )
        self._handle = handle
        self._pcrs = pcrs
        self._pk = self._read_public()  # may raise

    def _read_public(self) -> bytes:  # pragma: no cover - requires TPM
        out = subprocess.run(
            ["tpm2_readpublic", "-c", self._handle, "-f", "der", "-o", "/dev/stdout"],
            capture_output=True, check=True,
        )
        return out.stdout

    def public_key(self) -> bytes:
        return self._pk

    def sign(self, transcript: bytes) -> bytes:  # pragma: no cover - requires TPM
        with tempfile.NamedTemporaryFile() as msg, tempfile.NamedTemporaryFile() as sig:
            msg.write(transcript); msg.flush()
            subprocess.run(
                ["tpm2_sign", "-c", self._handle, "-g", "sha256",
                 "-o", sig.name, msg.name], check=True,
            )
            return Path(sig.name).read_bytes()

    def quote(self, nonce: bytes) -> Optional[bytes]:  # pragma: no cover - requires TPM
        with tempfile.NamedTemporaryFile() as q:
            subprocess.run(
                ["tpm2_quote", "-c", self._handle, "-l", self._pcrs,
                 "-q", nonce.hex(), "-m", q.name], check=True,
            )
            return Path(q.name).read_bytes()

    def info(self) -> SignerInfo:
        return SignerInfo(
            backend="tpm2", alg=ALG_ECDSA_P256, attested=True,
            key_fingerprint=_fp(self._pk), provider="TPM",
            model="TPM 2.0", serial="tpm", extra={"pcrs": self._pcrs},
        )


def _tpm2_available() -> bool:
    if not os.path.exists("/dev/tpmrm0") and not os.path.exists("/dev/tpm0"):
        return False
    try:
        subprocess.run(["tpm2_readpublic", "--help"], capture_output=True, check=False)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Factory + verification
# --------------------------------------------------------------------------- #


def make_signer(prefer: Optional[str] = None, **kwargs) -> EntropySigner:
    """
    Build the best available signer. ``prefer`` in {"yubihsm2","tpm2","software"}
    forces a backend (errors if unavailable). Default tries HSM -> TPM -> software.
    """
    order = [prefer] if prefer else ["yubihsm2", "tpm2", "software"]
    last_err: Optional[Exception] = None
    for backend in order:
        try:
            if backend == "yubihsm2":
                return YubiHSM2Signer(**kwargs)
            if backend == "tpm2":
                return TPM2Signer(**kwargs)
            if backend == "software":
                return SoftwareSelfSigner(**kwargs)
        except Exception as e:
            last_err = e
            continue
    if prefer:
        raise RuntimeError(f"signer backend {prefer!r} unavailable: {last_err}")
    # Should be unreachable: software signer is always constructible.
    return SoftwareSelfSigner()


def verify_signature(alg: str, public_key: bytes, transcript: bytes, signature: bytes,
                     *, hmac_key: Optional[bytes] = None) -> bool:
    """
    Verify a QUW signature. ed25519/ecdsa-p256 use 'cryptography'; hmac-sha3-256
    is symmetric (only verifiable with the shared key — used for the no-crypto
    software path in self-tests).
    """
    try:
        if alg == ALG_ED25519:
            if not _HAVE_CRYPTO:
                return False
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, transcript)
            return True
        if alg == ALG_ECDSA_P256:
            if not _HAVE_CRYPTO:
                return False
            pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
            digest = hashlib.sha256(transcript).digest()
            pub.verify(signature, digest, ec.ECDSA(_ecutils.Prehashed(_hashes.SHA256())))
            return True
        if alg == ALG_HMAC_SHA3:
            if hmac_key is None:
                return False
            expected = hmac.new(hmac_key, transcript, hashlib.sha3_256).digest()
            return hmac.compare_digest(expected, signature)
    except InvalidSignature:
        return False
    except Exception:
        return False
    return False
