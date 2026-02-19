"""Signer service for Animica Studio.

Currently implements watch-only mode: addresses can be tracked but sending
is disabled with an explicit ``SigningNotAvailableError`` unless a keystore
integration is wired in.

If ``~/.animica/wallets.json`` or a known keystore file exists, this module
will attempt to integrate with it.  See ``_try_load_keystore``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class SigningNotAvailableError(Exception):
    """Raised when signing is requested but no key is available."""


def _try_load_keystore() -> dict[str, Any] | None:
    """Attempt to load the Animica keystore file.

    Checks common locations:
    * ``~/.animica/wallets.json``
    * ``~/.animica/keystore.json``

    Returns the parsed JSON dict, or ``None`` if not found.
    """
    candidates = [
        Path.home() / ".animica" / "wallets.json",
        Path.home() / ".animica" / "keystore.json",
    ]
    for path in candidates:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    log.info("SignerService: loaded keystore from %s", path)
                    return data
            except Exception as exc:  # noqa: BLE001
                log.warning("SignerService: failed to read keystore %s: %s", path, exc)
    return None


class SignerService:
    """Manages signing keys for wallet accounts.

    In watch-only mode (no keystore found), ``sign_tx`` raises
    :class:`SigningNotAvailableError`.
    """

    def __init__(self) -> None:
        self._keystore = _try_load_keystore()
        self._watch_only = self._keystore is None

    @property
    def is_watch_only(self) -> bool:
        """Return ``True`` if no signing keys are available."""
        return self._watch_only

    def list_signing_addresses(self) -> list[str]:
        """Return addresses for which signing keys are available."""
        if self._keystore is None:
            return []
        # Support a few common keystore layouts
        if "accounts" in self._keystore and isinstance(self._keystore["accounts"], list):
            return [
                str(a.get("address", ""))
                for a in self._keystore["accounts"]
                if a.get("address")
            ]
        if "address" in self._keystore:
            addr = str(self._keystore["address"])
            return [addr] if addr else []
        return []

    def sign_tx(self, tx_dict: dict[str, Any], address: str) -> dict[str, Any]:
        """Sign the transaction body for *address*.

        Raises
        ------
        SigningNotAvailableError
            If no signing key is available for *address*.
        ValueError
            If the keystore does not contain a key for *address*.
        """
        if self._watch_only:
            raise SigningNotAvailableError(
                "No signing key available — this is a watch-only account. "
                "To send transactions, add your keystore file to ~/.animica/"
            )

        # Minimal key lookup — in a real implementation this would call
        # the Animica PQ signing library.
        signing_addrs = self.list_signing_addresses()
        if address not in signing_addrs:
            raise SigningNotAvailableError(
                f"No signing key found for address {address!r}. "
                "Add the key to your keystore file."
            )

        # Placeholder: attach a stub signature so the tx can be identified.
        # Real implementations would call the Dilithium3 signer here.
        log.warning(
            "SignerService: signing stub active for %s — real signing not implemented", address
        )
        signed = dict(tx_dict)
        signed["sigs"] = [{"stub": True, "address": address}]
        return signed
