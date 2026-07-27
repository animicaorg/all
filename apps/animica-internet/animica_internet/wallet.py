"""
In-process wallet for the Animica Internet app.

Reuses the SAME wallet the `animica` CLI uses (so a user's existing ANM funds work) via
animica.qt_wallet_bridge — no reimplemented crypto, no forgeable stub schemes. All heavy
lifting (keygen, unlock, tx build/sign/broadcast, balance) is delegated to the audited CLI
code; this module is a thin, GUI-friendly facade plus login-message signing for the .anm
registry.

SECURITY: message signing is a signing ORACLE over the PQ key. This module NEVER signs an
arbitrary page-supplied message on its own — the GUI (bridge.py + wallet_ui.py) gates every
sign/send behind a fail-closed approval dialog. Keep it that way.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .config import RPC_URL, SIGN_MESSAGE_DOMAIN, app_state_dir


class WalletError(RuntimeError):
    pass


def default_wallet_file() -> str:
    # Reuse the CLI wallet if present; else keep the app's own store under its state dir.
    cli = os.path.join(os.path.expanduser("~"), ".animica", "wallet.json")
    if os.path.exists(cli):
        return cli
    return os.path.join(app_state_dir(), "wallet.json")


@dataclass
class Account:
    address: str
    label: str
    alg: str


class Wallet:
    """Facade over animica.qt_wallet_bridge + animica.cli.tx for the desktop app."""

    def __init__(self, wallet_file: Optional[str] = None, rpc_url: Optional[str] = None):
        self.wallet_file = wallet_file or default_wallet_file()
        self.rpc_url = rpc_url or RPC_URL

    # ---- lazy bridge import so the module imports without animica present (tests mock it) ----
    @staticmethod
    def _bridge():
        try:
            from animica import qt_wallet_bridge as b
            return b
        except Exception as e:  # noqa: BLE001
            raise WalletError(f"animica wallet backend unavailable: {e}") from e

    def list_accounts(self) -> list[Account]:
        data = self._bridge().list_wallets(self.wallet_file)
        out = []
        for w in (data.get("wallets") or []):
            out.append(Account(address=w.get("address", ""), label=w.get("label", ""),
                               alg=w.get("algorithm") or w.get("alg") or "ml_dsa_65"))
        return out

    def has_wallet(self) -> bool:
        try:
            return bool(self.list_accounts())
        except Exception:
            return False

    def create(self, label: str = "Animica Internet", algorithm: str = "ml_dsa_65") -> Account:
        w = self._bridge().create_wallet(self.wallet_file, label, algorithm)
        return Account(address=w.get("address", ""), label=w.get("label", label),
                       alg=w.get("algorithm", algorithm))

    def primary_address(self) -> str:
        accts = self.list_accounts()
        if not accts:
            raise WalletError("no wallet — create one first")
        return accts[0].address

    def get_balance_nanm(self, address: Optional[str] = None) -> int:
        addr = address or self.primary_address()
        from animica.cli.wallet import get_balance  # audited RPC balance
        return int(get_balance(addr, self.rpc_url))

    def send(self, to_address: str, amount_nanm: int, *, from_address: Optional[str] = None,
             data_hex: Optional[str] = None) -> dict:
        """Build+sign+broadcast an ANM transfer. Returns the bridge result (incl. tx hash)."""
        frm = from_address or self.primary_address()
        return self._bridge().send_transaction(
            self.wallet_file, self.rpc_url, frm, to_address, int(amount_nanm),
            data_hex=data_hex,
        )

    def tx_status(self, tx_hash: str) -> dict:
        return self._bridge().transaction_status(self.rpc_url, tx_hash)

    # ---- login-message signing (ML-DSA-65 over the marketplace domain) ----
    def sign_login(self, challenge: str, *, address: Optional[str] = None,
                   passphrase: Optional[str] = None) -> tuple[str, str]:
        """Return (signature_hex_0x, public_key_hex_0x) for a marketplace login challenge,
        signed as ML-DSA-65 over UTF8(SIGN_MESSAGE_DOMAIN + challenge). Raises on encrypted
        stores that need a passphrase the caller didn't supply."""
        from animica.cli import tx as tx_cli
        addr = address or self.primary_address()
        entry = tx_cli._load_wallet_entry(addr)
        entry = tx_cli._unlock_entry_secret(entry, addr)
        sk_hex = entry.get("secret_key") or entry.get("secretKey")
        pk_hex = entry.get("public_key") or entry.get("publicKey")
        if not sk_hex or not pk_hex:
            raise WalletError("wallet entry missing key material (encrypted? provide passphrase)")
        from animica.vpn.crypto import sign  # vendored FIPS-204 ML-DSA-65
        sk = bytes.fromhex(sk_hex[2:] if sk_hex.startswith("0x") else sk_hex)
        message = (SIGN_MESSAGE_DOMAIN + challenge).encode()
        sig = sign(sk, message)
        pk = pk_hex if pk_hex.startswith("0x") else ("0x" + pk_hex)
        return ("0x" + sig.hex(), pk)
