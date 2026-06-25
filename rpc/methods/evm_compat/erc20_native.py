"""Native-ANM ERC-20 facade.

Presents Animica's native ANM as a read-only ERC-20 token at a fixed address, by
decoding ERC-20 selectors in eth_call and synthesizing the responses from native
chain state. So an EVM wallet can "Import token" and see an ANM balance with the
familiar token UI — without any deployed contract.

This is a READ-ONLY view (a static ABI shim, not a contract): name/symbol/decimals/
totalSupply/balanceOf work; transfer/approve/transferFrom revert with a clear
message (move ANM natively or via the relayer instead). It is gate-INDEPENDENT and
cannot move funds, so it is always safe to serve.

balanceOf is reported in nANM (decimals = 9), consistent with eth_getBalance.
"""

from __future__ import annotations

from typing import Optional

from . import formatters as F

# keccak256("Animica ERC20 ANM Token")[-20:]
ANM_TOKEN_ADDRESS = "0xbb22d4e8fd879ca21b89ef727155e81cb13500af"
# Non-empty code so wallets treat the address as a contract (PUSH1 0 PUSH1 0 REVERT).
STUB_CODE = "0x60006000fd"

_NAME = "Animica"
_SYMBOL = "ANM"
_DECIMALS = 9  # balances are nANM; 1 ANM = 1e9 nANM

# ERC-20 selectors
_SEL = {
    "06fdde03": "name",
    "95d89b41": "symbol",
    "313ce567": "decimals",
    "18160ddd": "totalSupply",
    "70a08231": "balanceOf",
    "a9059cbb": "transfer",
    "095ea7b3": "approve",
    "23b872dd": "transferFrom",
    "dd62ed3e": "allowance",
}
_READONLY_REVERT = {"transfer", "approve", "transferFrom"}


def is_token(addr: Optional[str]) -> bool:
    return bool(addr) and str(addr).lower() == ANM_TOKEN_ADDRESS


def _enc(types, values) -> str:
    import eth_abi
    return "0x" + eth_abi.encode(types, values).hex()


def encode_revert(reason: str) -> str:
    """Solidity Error(string) revert payload: 0x08c379a0 + abi(string)."""
    import eth_abi
    return "0x08c379a0" + eth_abi.encode(["string"], [reason]).hex()


class FacadeRevert(Exception):
    """Raised to signal an eth_call revert with ABI-encoded return data."""

    def __init__(self, data: str):
        super().__init__("execution reverted")
        self.data = data


def _total_supply_nanm() -> int:
    try:
        return F.from_q(F.native("state.getTotalSupply"), 0)
    except Exception:
        return 0


def _balance_of(calldata: bytes) -> int:
    # ERC-20 balanceOf(address): the address is the last 20 bytes of the 32-byte arg.
    if len(calldata) < 4 + 32:
        return 0
    addr20 = calldata[4 + 12:4 + 32]
    evm_addr = "0x" + addr20.hex()
    # resolve the 0x address -> native anim1 (bound alias, managed account, or none)
    anim = None
    try:
        from .bridge import evm_to_anim
        anim = evm_to_anim(evm_addr)
    except Exception:
        anim = None
    if not anim:
        try:
            from .relayer import is_enabled, get_account
            if is_enabled():
                acct = get_account(evm_addr.lower())
                if acct:
                    anim = acct.anim1
        except Exception:
            pass
    if not anim:
        return 0
    try:
        return F.from_q(F.native("state.getBalance", anim), 0)
    except Exception:
        return 0


def handle_call(data: str) -> str:
    """Dispatch an eth_call against the ANM token. Returns 0x-hex ABI data, or
    raises FacadeRevert for reverting selectors. data is the 0x calldata."""
    raw = bytes.fromhex(str(data)[2:] if str(data).startswith(("0x", "0X")) else str(data))
    if len(raw) < 4:
        raise FacadeRevert(encode_revert("no selector"))
    sel = raw[:4].hex()
    name = _SEL.get(sel)
    if name == "name":
        return _enc(["string"], [_NAME])
    if name == "symbol":
        return _enc(["string"], [_SYMBOL])
    if name == "decimals":
        return _enc(["uint8"], [_DECIMALS])
    if name == "totalSupply":
        return _enc(["uint256"], [_total_supply_nanm()])
    if name == "balanceOf":
        return _enc(["uint256"], [_balance_of(raw)])
    if name == "allowance":
        return _enc(["uint256"], [0])
    if name in _READONLY_REVERT:
        raise FacadeRevert(encode_revert(
            "Animica ANM ERC-20 facade is read-only; move ANM natively or via the relayer."))
    raise FacadeRevert(encode_revert("unknown selector"))
