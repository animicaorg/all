from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from web3 import Web3
from web3.contract import Contract

from ..addressing import validate_evm_address


ROUTER_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "internalType": "bytes32", "name": "orderId", "type": "bytes32"},
            {"indexed": True, "internalType": "address", "name": "sender", "type": "address"},
            {"indexed": False, "internalType": "uint256", "name": "amount", "type": "uint256"},
            {"indexed": False, "internalType": "address", "name": "vault", "type": "address"},
        ],
        "name": "DepositRegistered",
        "type": "event",
    },
    {
        "inputs": [
            {"internalType": "bytes32", "name": "orderId", "type": "bytes32"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
        ],
        "name": "deposit",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

CONTROLLER_ABI = [
    {
        "inputs": [
            {"internalType": "bytes32", "name": "orderId", "type": "bytes32"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "amount", "type": "uint256"},
            {"internalType": "uint256", "name": "feeAmount", "type": "uint256"},
            {"internalType": "string", "name": "externalRef", "type": "string"},
        ],
        "name": "executeMint",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]

VAULT_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "orderId", "type": "bytes32"}],
        "name": "burnForOrder",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

TOKEN_ABI = [
    {
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


@dataclass(frozen=True)
class EvmTxStatus:
    tx_hash: str
    confirmations: int
    included: bool
    block_number: int | None
    success: bool | None
    raw: dict[str, Any] | None


@dataclass(frozen=True)
class EvmDepositObservation:
    tx_hash: str
    sender: str
    order_id_hex: str
    amount: int
    token_address: str
    confirmations: int
    block_number: int | None
    log_index: int | None
    raw: dict[str, Any] | None


@dataclass(frozen=True)
class EvmSettlementResult:
    tx_hash: str
    nonce: int


class EvmAdapter:
    def total_supply(self) -> int:  # pragma: no cover - interface
        raise NotImplementedError

    def tx_status(self, tx_hash: str) -> EvmTxStatus:  # pragma: no cover - interface
        raise NotImplementedError

    def inspect_router_deposit(self, tx_hash: str) -> EvmDepositObservation:  # pragma: no cover - interface
        raise NotImplementedError

    def submit_mint(self, *, order_id: str, to_address: str, amount: int, fee_amount: int) -> EvmSettlementResult:  # pragma: no cover - interface
        raise NotImplementedError

    def submit_burn(self, *, order_id: str) -> EvmSettlementResult:  # pragma: no cover - interface
        raise NotImplementedError

    @staticmethod
    def order_id_to_bytes32(order_id: str) -> bytes:
        if order_id.startswith("0x") and len(order_id) == 66:
            return bytes.fromhex(order_id[2:])
        return Web3.keccak(text=order_id)


class Web3EvmAdapter(EvmAdapter):
    def __init__(
        self,
        *,
        rpc_url: str,
        chain_id: int,
        token_address: str,
        controller_address: str,
        vault_address: str,
        router_address: str,
        operator_private_key: str,
    ):
        self._w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 20}))

        self._chain_id = chain_id
        self._token = self._w3.eth.contract(address=validate_evm_address(token_address), abi=TOKEN_ABI)
        self._controller = self._w3.eth.contract(
            address=validate_evm_address(controller_address),
            abi=CONTROLLER_ABI,
        )
        self._vault = self._w3.eth.contract(address=validate_evm_address(vault_address), abi=VAULT_ABI)
        self._router = self._w3.eth.contract(address=validate_evm_address(router_address), abi=ROUTER_ABI)
        self._private_key = operator_private_key.strip()
        if not self._private_key:
            raise RuntimeError("EVM_OPERATOR_PRIVATE_KEY is required for settlement submission")
        self._account = self._w3.eth.account.from_key(self._private_key)

    def total_supply(self) -> int:
        return int(self._token.functions.totalSupply().call())

    def tx_status(self, tx_hash: str) -> EvmTxStatus:
        tx_hash_hex = self._normalize_tx_hash(tx_hash)
        receipt = self._w3.eth.get_transaction_receipt(tx_hash_hex)
        latest = self._w3.eth.block_number
        confirmations = max(0, int(latest - receipt.blockNumber + 1))
        return EvmTxStatus(
            tx_hash=tx_hash_hex,
            confirmations=confirmations,
            included=receipt.blockNumber is not None,
            block_number=int(receipt.blockNumber) if receipt.blockNumber is not None else None,
            success=(receipt.status == 1) if receipt.status is not None else None,
            raw=dict(receipt),
        )

    def inspect_router_deposit(self, tx_hash: str) -> EvmDepositObservation:
        tx_hash_hex = self._normalize_tx_hash(tx_hash)
        receipt = self._w3.eth.get_transaction_receipt(tx_hash_hex)
        matched_log = None
        parsed = None
        for log in receipt.logs:
            if log.address.lower() != self._router.address.lower():
                continue
            try:
                parsed = self._router.events.DepositRegistered().process_log(log)
                matched_log = log
                break
            except Exception:
                continue

        if not parsed or matched_log is None:
            raise RuntimeError("DepositRegistered event not found in router tx receipt")

        latest = self._w3.eth.block_number
        confirmations = max(0, int(latest - receipt.blockNumber + 1))
        args = parsed["args"]
        order_id_bytes = bytes(args["orderId"])
        return EvmDepositObservation(
            tx_hash=tx_hash_hex,
            sender=Web3.to_checksum_address(args["sender"]),
            order_id_hex="0x" + order_id_bytes.hex(),
            amount=int(args["amount"]),
            token_address=self._token.address,
            confirmations=confirmations,
            block_number=int(receipt.blockNumber),
            log_index=int(matched_log.logIndex),
            raw={
                "receipt": dict(receipt),
                "event": {"args": {k: str(v) for k, v in args.items()}},
            },
        )

    def submit_mint(
        self,
        *,
        order_id: str,
        to_address: str,
        amount: int,
        fee_amount: int,
    ) -> EvmSettlementResult:
        order_key = self.order_id_to_bytes32(order_id)
        fn = self._controller.functions.executeMint(
            order_key,
            validate_evm_address(to_address),
            int(amount),
            int(fee_amount),
            order_id,
        )
        tx_hash, nonce = self._send_tx(fn)
        return EvmSettlementResult(tx_hash=tx_hash, nonce=nonce)

    def submit_burn(self, *, order_id: str) -> EvmSettlementResult:
        order_key = self.order_id_to_bytes32(order_id)
        fn = self._vault.functions.burnForOrder(order_key)
        tx_hash, nonce = self._send_tx(fn)
        return EvmSettlementResult(tx_hash=tx_hash, nonce=nonce)

    def _send_tx(self, fn) -> tuple[str, int]:
        nonce = self._w3.eth.get_transaction_count(self._account.address, block_identifier="pending")
        gas_price = self._w3.eth.gas_price
        tx = fn.build_transaction(
            {
                "chainId": self._chain_id,
                "from": self._account.address,
                "nonce": nonce,
                "gasPrice": gas_price,
            }
        )
        if "gas" not in tx:
            tx["gas"] = self._w3.eth.estimate_gas(tx)
        signed = self._w3.eth.account.sign_transaction(tx, self._private_key)
        submitted = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        return submitted.hex(), int(nonce)

    @staticmethod
    def _normalize_tx_hash(value: str) -> str:
        candidate = value.strip()
        if not candidate.startswith("0x") or len(candidate) != 66:
            raise ValueError("invalid EVM tx hash")
        return candidate
