from __future__ import annotations

from animica_studio.models.wallet_models import is_valid_address


class AddressUtil:
    @staticmethod
    def is_valid_bech32(address: str) -> bool:
        return is_valid_address(address)
