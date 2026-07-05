"""ANM-C07: wallet at-rest encryption of secret-key material.

These tests prove BOTH sides of the hardening:

  * FAIL-CLOSED / new behavior: secrets can be encrypted at rest with a
    passphrase-derived key + AEAD; the on-disk form carries no plaintext, a
    wrong passphrase / tampered ciphertext is rejected, and a reader without
    the passphrase cannot recover the secret.
  * BACKWARD COMPAT: existing plaintext wallets still load and save exactly as
    before (default path unchanged), and a canonical save never re-materializes
    plaintext for an encrypted entry.

No real secrets are used — all key material is synthetic.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from animica.wallet import at_rest
from animica.wallet.at_rest import (
    AEAD_AES_256_GCM,
    AEAD_SHAKE_HMAC,
    KDF_PBKDF2_SHA3,
    KDF_SCRYPT,
    WalletEncryptionError,
    decrypt_secret_hex,
    decrypt_store_secrets,
    encrypt_secret_hex,
    encrypt_store_secrets,
    is_encrypted_secret,
    resolve_passphrase,
    store_is_encrypted,
)
from animica.wallet.serialization import (
    canonical_json_dumps,
    export_canonical_store,
    load_store_canonical,
    parse_wallets_text,
)

# Synthetic, obviously-fake key material.
_FAKE_SK_HEX = "aa" * 40  # 40 bytes
_FAKE_PK_HEX = "bb" * 32
_PASS = "correct horse battery staple"


def _valid_addr() -> str:
    # Derive a real bech32m address from the fake pubkey so the store parser
    # (which validates addresses when pq.py is available) accepts the fixture.
    try:
        from pq.py.address import address_from_pubkey  # type: ignore

        return address_from_pubkey(bytes.fromhex(_FAKE_PK_HEX), 0x1003)
    except Exception:
        return "anim1" + _FAKE_PK_HEX


_ADDR = _valid_addr()


def _plaintext_store() -> dict:
    return {
        "format": "animica.wallets",
        "version": 2,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "default": "w0",
        "wallets": [
            {
                "label": "w0",
                "address": _ADDR,
                "alg_id": 0x1003,
                "alg_name": "ml_dsa_65",
                "public_key_hex": _FAKE_PK_HEX,
                "secret_key_hex": _FAKE_SK_HEX,
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
    }


# ---------------------------------------------------------------------------
# Envelope-level crypto
# ---------------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip_default():
    env = encrypt_secret_hex(_FAKE_SK_HEX, _PASS, address=_ADDR)
    assert is_encrypted_secret(env)
    # Envelope carries NO plaintext key material anywhere.
    blob = json.dumps(env)
    assert _FAKE_SK_HEX not in blob
    assert "secret_key_hex" not in blob
    assert decrypt_secret_hex(env, _PASS, address=_ADDR) == _FAKE_SK_HEX


def test_wrong_passphrase_rejected():
    env = encrypt_secret_hex(_FAKE_SK_HEX, _PASS, address=_ADDR)
    with pytest.raises(WalletEncryptionError):
        decrypt_secret_hex(env, "not the passphrase", address=_ADDR)


def test_tampered_ciphertext_rejected():
    env = encrypt_secret_hex(_FAKE_SK_HEX, _PASS, address=_ADDR)
    ct = bytearray(bytes.fromhex(env["ct"]))
    ct[0] ^= 0x01
    env["ct"] = bytes(ct).hex()
    with pytest.raises(WalletEncryptionError):
        decrypt_secret_hex(env, _PASS, address=_ADDR)


def test_address_binding_detects_swap():
    # An envelope minted for one address should not silently decrypt for another
    # when the aad differs and the ciphertext was bound to the original.
    env = encrypt_secret_hex(_FAKE_SK_HEX, _PASS, address=_ADDR)
    # Correct address works.
    assert decrypt_secret_hex(env, _PASS, address=_ADDR) == _FAKE_SK_HEX
    # A different, non-empty address must fail auth (aad mismatch, no empty-aad
    # fallback because the envelope was bound to a real address).
    with pytest.raises(WalletEncryptionError):
        decrypt_secret_hex(env, _PASS, address="anim1qotheraddressyyyyyyyyyyyyyyyyyyyyyyyyyyyy")


@pytest.mark.parametrize("kdf", [KDF_SCRYPT, KDF_PBKDF2_SHA3])
def test_each_available_kdf_roundtrips(kdf):
    env = encrypt_secret_hex(_FAKE_SK_HEX, _PASS, kdf=kdf, address=_ADDR)
    assert env["kdf"] == kdf
    assert decrypt_secret_hex(env, _PASS, address=_ADDR) == _FAKE_SK_HEX


def test_vendored_fallback_aead_roundtrips():
    # Force the pure-stdlib AEAD (used when `cryptography` is unavailable).
    env = encrypt_secret_hex(_FAKE_SK_HEX, _PASS, aead=AEAD_SHAKE_HMAC, address=_ADDR)
    assert env["aead"] == AEAD_SHAKE_HMAC
    assert decrypt_secret_hex(env, _PASS, address=_ADDR) == _FAKE_SK_HEX
    # And its auth still catches tampering.
    env["ct"] = ("00" + env["ct"][2:])
    with pytest.raises(WalletEncryptionError):
        decrypt_secret_hex(env, _PASS, address=_ADDR)


def test_aesgcm_available_and_selected_by_default():
    # In this venv cryptography is present, so the default AEAD is AES-256-GCM.
    assert at_rest.preferred_aead() == AEAD_AES_256_GCM
    env = encrypt_secret_hex(_FAKE_SK_HEX, _PASS, address=_ADDR)
    assert env["aead"] == AEAD_AES_256_GCM


# ---------------------------------------------------------------------------
# Store-level migration + transparent read
# ---------------------------------------------------------------------------

def test_encrypt_store_removes_plaintext():
    store = _plaintext_store()
    assert not store_is_encrypted(store)
    enc = encrypt_store_secrets(store, _PASS)
    assert store_is_encrypted(enc)
    w = enc["wallets"][0]
    assert "secret_key_hex" not in w
    assert is_encrypted_secret(w["secret_key_enc"])
    # Full serialized store contains no plaintext secret.
    assert _FAKE_SK_HEX not in canonical_json_dumps(export_canonical_store(enc))
    # And it round-trips back to the original secret.
    dec = decrypt_store_secrets(enc, _PASS)
    assert dec["wallets"][0]["secret_key_hex"] == _FAKE_SK_HEX


def test_decrypt_store_wrong_pass_non_strict_leaves_sealed():
    enc = encrypt_store_secrets(_plaintext_store(), _PASS)
    dec = decrypt_store_secrets(enc, "wrong", strict=False)
    # Secret not recovered, entry stays sealed, no crash.
    assert "secret_key_hex" not in dec["wallets"][0]
    assert is_encrypted_secret(dec["wallets"][0]["secret_key_enc"])


def test_decrypt_store_wrong_pass_strict_raises():
    enc = encrypt_store_secrets(_plaintext_store(), _PASS)
    with pytest.raises(WalletEncryptionError):
        decrypt_store_secrets(enc, "wrong", strict=True)


# ---------------------------------------------------------------------------
# Backward compatibility through the serialization layer
# ---------------------------------------------------------------------------

def test_plaintext_store_loads_unchanged(tmp_path: Path, monkeypatch):
    # No passphrase env — default path must be identical to legacy behavior.
    monkeypatch.delenv(at_rest.PASSPHRASE_ENV, raising=False)
    monkeypatch.delenv(at_rest.PASSPHRASE_FILE_ENV, raising=False)
    p = tmp_path / "wallets.json"
    p.write_text(canonical_json_dumps(_plaintext_store()), encoding="utf-8")
    res = load_store_canonical(p)
    assert not res.failures
    w = res.store["wallets"][0]
    assert w["secret_key_hex"] == _FAKE_SK_HEX
    assert "secret_key_enc" not in w


def test_encrypted_envelope_roundtrips_through_parser():
    # secret_key_enc must survive parse -> canonical without loss or plaintext.
    enc_store = encrypt_store_secrets(_plaintext_store(), _PASS)
    text = canonical_json_dumps(export_canonical_store(enc_store))
    reparsed = parse_wallets_text(text)
    w = reparsed.store["wallets"][0]
    assert is_encrypted_secret(w["secret_key_enc"])
    assert "secret_key_hex" not in w


def test_encrypted_store_read_without_passphrase_is_sealed(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(at_rest.PASSPHRASE_ENV, raising=False)
    monkeypatch.delenv(at_rest.PASSPHRASE_FILE_ENV, raising=False)
    p = tmp_path / "wallets.json"
    enc_store = export_canonical_store(encrypt_store_secrets(_plaintext_store(), _PASS))
    p.write_text(canonical_json_dumps(enc_store), encoding="utf-8")
    res = load_store_canonical(p)
    w = res.store["wallets"][0]
    # Address / pubkey still available (read-only ops work) but no secret.
    assert w["address"] == _ADDR
    assert w["public_key_hex"] == _FAKE_PK_HEX
    assert "secret_key_hex" not in w
    assert any("encrypted at rest" in warn for warn in res.warnings)


def test_encrypted_store_read_with_passphrase_env_unlocks(tmp_path: Path, monkeypatch):
    monkeypatch.setenv(at_rest.PASSPHRASE_ENV, _PASS)
    p = tmp_path / "wallets.json"
    enc_store = export_canonical_store(encrypt_store_secrets(_plaintext_store(), _PASS))
    p.write_text(canonical_json_dumps(enc_store), encoding="utf-8")
    res = load_store_canonical(p)
    w = res.store["wallets"][0]
    assert w["secret_key_hex"] == _FAKE_SK_HEX
    assert is_encrypted_secret(w["secret_key_enc"])  # envelope retained


def test_resaving_decrypted_store_never_leaks_plaintext(tmp_path: Path, monkeypatch):
    # Simulate the CLI flow: load (transparent decrypt) -> mutate -> save.
    monkeypatch.setenv(at_rest.PASSPHRASE_ENV, _PASS)
    p = tmp_path / "wallets.json"
    enc_store = export_canonical_store(encrypt_store_secrets(_plaintext_store(), _PASS))
    p.write_text(canonical_json_dumps(enc_store), encoding="utf-8")

    res = load_store_canonical(p)  # secret_key_hex populated in memory
    assert res.store["wallets"][0]["secret_key_hex"] == _FAKE_SK_HEX
    # Persist again through the canonical export path (what _save_store uses).
    on_disk = canonical_json_dumps(export_canonical_store(res.store))
    p.write_text(on_disk, encoding="utf-8")
    assert _FAKE_SK_HEX not in on_disk
    assert "secret_key_hex" not in json.loads(on_disk)["wallets"][0]


def test_resolve_passphrase_precedence(tmp_path: Path, monkeypatch):
    monkeypatch.delenv(at_rest.PASSPHRASE_ENV, raising=False)
    monkeypatch.delenv(at_rest.PASSPHRASE_FILE_ENV, raising=False)
    assert resolve_passphrase() is None
    assert resolve_passphrase("explicit") == "explicit"
    monkeypatch.setenv(at_rest.PASSPHRASE_ENV, "from-env")
    assert resolve_passphrase() == "from-env"
    assert resolve_passphrase("explicit") == "explicit"  # explicit wins
    pf = tmp_path / "pass.txt"
    pf.write_text("from-file\n", encoding="utf-8")
    monkeypatch.delenv(at_rest.PASSPHRASE_ENV, raising=False)
    monkeypatch.setenv(at_rest.PASSPHRASE_FILE_ENV, str(pf))
    assert resolve_passphrase() == "from-file"  # trailing newline stripped


# ---------------------------------------------------------------------------
# Payment signing against an encrypted store
# ---------------------------------------------------------------------------

def _real_wallet_store() -> dict:
    """Generate a real (fallback) PQ keypair so signing actually works."""
    import os

    # Fake / pure-fallback keygen is a non-mainnet dev operation; the ANM-M07
    # mainnet guard (correctly) refuses it on chain_id=1, so pin a testnet id.
    os.environ["ANIMICA_CHAIN_ID"] = "2"
    os.environ["ANIMICA_NETWORK"] = "testnet"
    os.environ.setdefault("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
    os.environ.setdefault("ANIMICA_UNSAFE_PQ_FAKE", "1")
    from animica.cli.wallet import _generate_entry, _resolve_signature_alg

    entry = _generate_entry(
        "signer",
        allow_fallback=True,
        alg_info=_resolve_signature_alg(None),
        allow_default_fallback=True,
    )
    return {
        "format": "animica.wallets",
        "version": 2,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "default": entry.label,
        "wallets": [entry.to_dict()],
    }


def test_sign_payment_tx_with_encrypted_store(tmp_path: Path, monkeypatch):
    pytest.importorskip("cbor2")
    from animica.wallet.payment import PaymentSigningError, sign_payment_tx

    store = _real_wallet_store()
    addr = store["wallets"][0]["address"]
    plain_secret = store["wallets"][0]["secret_key_hex"]

    enc_store = export_canonical_store(encrypt_store_secrets(store, _PASS))
    p = tmp_path / "wallets.json"
    p.write_text(canonical_json_dumps(enc_store), encoding="utf-8")
    # Sanity: nothing on disk leaks the plaintext secret.
    assert plain_secret not in p.read_text(encoding="utf-8")

    # Without a passphrase, signing must fail closed (not sign with no key).
    monkeypatch.delenv("ANIMICA_WALLET_PASSPHRASE", raising=False)
    with pytest.raises(PaymentSigningError):
        sign_payment_tx(
            wallet_path=str(p), recipient=addr, amount=1.0, nonce=0,
            chain_id=1, from_address=addr,
        )

    # With the passphrase, signing succeeds and produces a hex tx.
    raw = sign_payment_tx(
        wallet_path=str(p), recipient=addr, amount=1.0, nonce=0,
        chain_id=1, from_address=addr, passphrase=_PASS,
    )
    assert isinstance(raw, str) and raw.startswith("0x") and len(raw) > 4
