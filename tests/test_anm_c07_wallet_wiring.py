# SPDX-License-Identifier: MIT
"""ANM-C07 (at-rest wallet secret encryption wiring) + ANM-M07 (default scheme /
insecure keygen guard) regression tests.

These exercise the *real* vulnerable paths that ship in ``animica wallet create``
and ``animica tx send``:

  (a) creating/saving a wallet with a passphrase writes NO plaintext
      ``secret_key_hex`` to disk (only an encrypted ``secret_key_enc`` envelope);
  (b) an encrypted store round-trips (decrypt -> sign) with the passphrase and
      FAILS to sign without it;
  (c) an existing PLAINTEXT wallet still loads and signs with no passphrase
      (backward compatibility is mandatory);
  (d) ``animica wallet encrypt`` migrates a plaintext store in place;
  (e) a wallet entry with a MISSING ``alg_id`` defaults to ml_dsa_65 (0x1003),
      never the forgeable dilithium3 stub (0x1001);
  (f) the transparent-decrypt read path used by the signer (tx.py) unlocks with
      a passphrase and fails closed without one;
  (g) insecure/stub PQ keygen is refused on mainnet (chain_id=1) and only
      permitted off-mainnet.

No real ``~/.animica/wallets.json`` is ever touched — every test uses a
synthetic wallet under a tmp dir and real ml_dsa_65 keys generated in-process.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from animica.cli import tx as TX
from animica.cli import wallet as W
from animica.wallet import at_rest, payment

runner = CliRunner()

PASSPHRASE = "correct horse battery staple"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize inherited PQ/passphrase env so tests are deterministic.

    Real ml_dsa_65 keygen is used everywhere (no fake/stub keys), and no
    ambient passphrase leaks into the plaintext/backward-compat cases.
    """
    for var in (
        at_rest.PASSPHRASE_ENV,
        at_rest.PASSPHRASE_FILE_ENV,
        "ANIMICA_UNSAFE_PQ_FAKE",
        "ANIMICA_ALLOW_PQ_PURE_FALLBACK",
    ):
        monkeypatch.delenv(var, raising=False)
    # Reset the process-global "plaintext at rest" warning latch.
    monkeypatch.setattr(W, "_PLAINTEXT_WARN_EMITTED", False, raising=False)


def _new_real_entry(label: str = "acct") -> W.WalletEntry:
    """Generate a wallet entry backed by a REAL ml_dsa_65 keypair (no fallback)."""
    alg_info = W._resolve_signature_alg(None)
    entry = W._generate_entry(
        label,
        allow_fallback=False,
        alg_info=alg_info,
        allow_default_fallback=True,
    )
    assert entry.alg_id == 0x1003, f"expected ml_dsa_65 default, got {entry.alg_id:#06x}"
    return entry


def _write_plaintext_store(path: Path, entry: W.WalletEntry) -> None:
    store = {"format": "animica.wallets", "version": 2, "wallets": [entry.to_dict()]}
    # No passphrase -> classic plaintext behavior (preserved for compat).
    W._save_store(path, store)


def _sign(wallet_path: Path, entry: W.WalletEntry, **kw) -> str:
    return payment.sign_payment_tx(
        wallet_path=str(wallet_path),
        recipient=entry.address,
        amount=0.25,
        nonce=0,
        chain_id=1337,
        from_address=entry.address,
        **kw,
    )


# --------------------------------------------------------------------------- #
# (a) create + save with a passphrase writes NO plaintext secret to disk
# --------------------------------------------------------------------------- #
def test_create_with_passphrase_writes_no_plaintext_secret(tmp_path: Path) -> None:
    store = tmp_path / "wallets.json"
    result = runner.invoke(
        W.app,
        ["--wallet-file", str(store), "create", "--label", "enc", "--password", PASSPHRASE],
    )
    assert result.exit_code == 0, result.output

    raw = store.read_text(encoding="utf-8")
    disk = json.loads(raw)
    wallets = disk["wallets"]
    assert len(wallets) == 1
    w = wallets[0]

    # The encrypted envelope is present and self-describing...
    assert at_rest.is_encrypted_secret(w.get("secret_key_enc"))
    # ...and NO plaintext secret material survives on disk, in any casing/field.
    assert "secret_key_hex" not in w
    assert "secretKeyHex" not in w
    assert "secret_key_hex" not in raw
    # No plaintext ``.bak.*`` snapshot should have been written for a fresh
    # encrypted store either.
    assert list(tmp_path.glob("wallets.json.bak.*")) == []


def test_plaintext_save_emits_single_warning(tmp_path: Path) -> None:
    store = tmp_path / "wallets.json"
    result = runner.invoke(
        W.app, ["--wallet-file", str(store), "create", "--label", "plain"]
    )
    assert result.exit_code == 0, result.output
    assert "UNENCRYPTED" in result.output
    # Plaintext path is preserved exactly: secret_key_hex present, no envelope.
    disk = json.loads(store.read_text(encoding="utf-8"))
    assert disk["wallets"][0].get("secret_key_hex")
    assert "secret_key_enc" not in disk["wallets"][0]


# --------------------------------------------------------------------------- #
# (b) encrypted store round-trips (decrypt -> sign) WITH the passphrase and
#     FAILS without it
# --------------------------------------------------------------------------- #
def test_encrypted_store_signs_with_passphrase_and_fails_without(tmp_path: Path) -> None:
    entry = _new_real_entry("enc")
    store = tmp_path / "wallets.json"
    W._save_store(
        store,
        {"format": "animica.wallets", "version": 2, "wallets": [entry.to_dict()]},
        passphrase=PASSPHRASE,
    )
    # Sanity: on-disk is encrypted.
    disk = json.loads(store.read_text(encoding="utf-8"))
    assert at_rest.is_encrypted_secret(disk["wallets"][0]["secret_key_enc"])
    assert "secret_key_hex" not in disk["wallets"][0]

    # WITH passphrase -> produces a signed tx.
    signed = _sign(store, entry, passphrase=PASSPHRASE)
    assert isinstance(signed, str) and signed.startswith("0x") and len(signed) > 64

    # WITHOUT passphrase -> fail closed (never signs, never leaks).
    with pytest.raises(payment.PaymentSigningError):
        _sign(store, entry)

    # WRONG passphrase -> auth failure, still no signature.
    with pytest.raises(payment.PaymentSigningError):
        _sign(store, entry, passphrase="not-the-passphrase")


# --------------------------------------------------------------------------- #
# (c) existing PLAINTEXT wallet still loads + signs with NO passphrase
# --------------------------------------------------------------------------- #
def test_plaintext_wallet_backward_compat_signs(tmp_path: Path) -> None:
    entry = _new_real_entry("legacy")
    store = tmp_path / "wallets.json"
    _write_plaintext_store(store, entry)

    disk = json.loads(store.read_text(encoding="utf-8"))
    assert disk["wallets"][0]["secret_key_hex"]  # plaintext preserved
    assert "secret_key_enc" not in disk["wallets"][0]

    # Signs with no passphrase at all — the legacy flow is untouched.
    signed = _sign(store, entry)
    assert signed.startswith("0x") and len(signed) > 64


# --------------------------------------------------------------------------- #
# (d) ``animica wallet encrypt`` migrates a plaintext store
# --------------------------------------------------------------------------- #
def test_wallet_encrypt_command_migrates_plaintext(tmp_path: Path) -> None:
    entry = _new_real_entry("migrate")
    store = tmp_path / "wallets.json"
    _write_plaintext_store(store, entry)
    assert "secret_key_hex" in json.loads(store.read_text())["wallets"][0]

    result = runner.invoke(
        W.app, ["--wallet-file", str(store), "encrypt", "--password", PASSPHRASE]
    )
    assert result.exit_code == 0, result.output
    assert "Encrypted 1 wallet" in result.output

    disk = json.loads(store.read_text(encoding="utf-8"))
    w = disk["wallets"][0]
    assert at_rest.is_encrypted_secret(w["secret_key_enc"])
    assert "secret_key_hex" not in w
    assert "secret_key_hex" not in store.read_text(encoding="utf-8")

    # The migrated store still signs with the passphrase.
    signed = _sign(store, entry, passphrase=PASSPHRASE)
    assert signed.startswith("0x")

    # And a subsequent decrypt round-trips it back to plaintext.
    result2 = runner.invoke(
        W.app, ["--wallet-file", str(store), "decrypt", "--password", PASSPHRASE, "--yes"]
    )
    assert result2.exit_code == 0, result2.output
    disk2 = json.loads(store.read_text(encoding="utf-8"))
    assert disk2["wallets"][0]["secret_key_hex"]
    assert "secret_key_enc" not in disk2["wallets"][0]


# --------------------------------------------------------------------------- #
# (e) missing alg_id defaults to ml_dsa_65 (0x1003), not the 0x1001 stub
# --------------------------------------------------------------------------- #
def test_missing_alg_id_defaults_to_ml_dsa_65(tmp_path: Path, monkeypatch) -> None:
    entry = _new_real_entry("noalg")
    store = tmp_path / "wallets.json"
    _write_plaintext_store(store, entry)

    # Strip alg_id from the on-disk entry to force the default-resolution path.
    disk = json.loads(store.read_text(encoding="utf-8"))
    disk["wallets"][0].pop("alg_id", None)
    disk["wallets"][0].pop("algId", None)
    store.write_text(json.dumps(disk), encoding="utf-8")

    captured: dict[str, int] = {}

    class _Stop(Exception):
        pass

    def _fake_sign(body, sk, pk, used_alg_id, chain_ctx):
        captured["alg_id"] = used_alg_id
        raise _Stop()

    monkeypatch.setattr(payment, "pq_sign_tx", _fake_sign)
    with pytest.raises(_Stop):
        _sign(store, entry)

    assert captured["alg_id"] == 0x1003
    assert captured["alg_id"] != 0x1001


# --------------------------------------------------------------------------- #
# (f) tx.py signer read path: transparent decrypt with env passphrase, fail
#     closed without one
# --------------------------------------------------------------------------- #
def test_tx_signer_transparent_decrypt(tmp_path: Path, monkeypatch) -> None:
    entry = _new_real_entry("signer")
    store = tmp_path / "wallets.json"
    W._save_store(
        store,
        {"format": "animica.wallets", "version": 2, "wallets": [entry.to_dict()]},
        passphrase=PASSPHRASE,
    )
    monkeypatch.setattr(TX, "_wallet_store_path", lambda: store)
    # Non-interactive: no TTY, so the prompt path is skipped.
    monkeypatch.setattr(TX.sys, "stdin", SimpleNamespace(isatty=lambda: False))

    # Without a passphrase -> the signer path fails closed with a clear error.
    with pytest.raises(RuntimeError) as ei:
        TX._load_wallet_entry(entry.address)
    assert "encrypted" in str(ei.value).lower()

    # With env passphrase -> the entry is transparently unlocked with a usable
    # secret key, and the on-disk store is NOT mutated (stays encrypted).
    monkeypatch.setenv(at_rest.PASSPHRASE_ENV, PASSPHRASE)
    unlocked = TX._load_wallet_entry(entry.address)
    assert unlocked["secret_key_hex"] == entry.secret_key_hex
    disk = json.loads(store.read_text(encoding="utf-8"))
    assert "secret_key_hex" not in disk["wallets"][0]
    assert at_rest.is_encrypted_secret(disk["wallets"][0]["secret_key_enc"])


# --------------------------------------------------------------------------- #
# (g) ANM-M07: insecure/stub keygen is refused on mainnet, allowed off-mainnet
# --------------------------------------------------------------------------- #
def test_mainnet_blocks_insecure_pq_keygen(monkeypatch) -> None:
    alg_info = W._resolve_signature_alg(None)

    # Mainnet + a pre-set unsafe flag must fail closed (never fabricate a stub).
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1")
    monkeypatch.delenv("ANIMICA_NETWORK", raising=False)
    monkeypatch.setenv("ANIMICA_UNSAFE_PQ_FAKE", "1")
    assert W._insecure_fallback_blocked() is True
    with pytest.raises(RuntimeError) as ei:
        W._generate_entry(
            "m", allow_fallback=True, alg_info=alg_info, allow_default_fallback=True
        )
    assert "mainnet" in str(ei.value).lower()

    # Off-mainnet the dev opt-in is allowed and still produces a real key
    # (native ml_dsa_65 is available in this environment).
    monkeypatch.setenv("ANIMICA_CHAIN_ID", "1337")
    assert W._insecure_fallback_blocked() is False
    entry = W._generate_entry(
        "d", allow_fallback=True, alg_info=alg_info, allow_default_fallback=True
    )
    assert entry.public_key_hex and entry.secret_key_hex
    assert entry.public_key_hex != entry.secret_key_hex
