"""
Animica Omni SDK — Python
Convenience exports for the most common client APIs.
"""

from .version import __version__  # noqa: F401

# Core config & errors
from .config import Config  # noqa: F401
from .errors import RpcError, TxError, AbiError, VerifyError  # noqa: F401

# RPC
from .rpc.http import RpcClient  # noqa: F401
from .rpc.ws import WsClient  # noqa: F401

# Addresses
from .address import (  # noqa: F401
    bech32_encode,
    bech32_decode,
)

# Wallet
from .wallet.mnemonic import (  # noqa: F401
    create_mnemonic,
    mnemonic_to_seed,
)
from .wallet.keystore import Keystore  # noqa: F401
from .wallet.signer import (  # noqa: F401
    Dilithium3Signer,
    SphincsShake128sSigner,
)

# Tx helpers
from .tx.build import (  # noqa: F401
    build_transfer,
    build_call,
    build_deploy,
)
from .tx.encode import build_sign_bytes  # noqa: F401
from .tx.send import send_and_wait  # noqa: F401

# Contracts
from .contracts.client import ContractClient  # noqa: F401
from .contracts.deployer import deploy_package  # noqa: F401
from .contracts.events import EventDecoder  # noqa: F401
from .contracts.codegen import generate_client  # noqa: F401

# Data Availability
from .da.client import DAClient  # noqa: F401

# AICF (AI/Quantum)
from .aicf.client import AICFClient  # noqa: F401

# Randomness beacon
from .randomness.client import RandomnessClient  # noqa: F401

# Light client
from .light_client.verify import verify_light_proof  # noqa: F401

# Utilities
from .utils.bytes import to_hex, hex_to_bytes  # noqa: F401

__all__ = [
    "__version__",
    # Core
    "Config",
    "RpcError", "TxError", "AbiError", "VerifyError",
    # RPC
    "RpcClient", "WsClient",
    # Address
    "bech32_encode", "bech32_decode",
    # Wallet
    "create_mnemonic", "mnemonic_to_seed",
    "Keystore",
    "Dilithium3Signer", "SphincsShake128sSigner",
    # Tx
    "build_transfer", "build_call", "build_deploy",
    "build_sign_bytes", "send_and_wait",
    # Contracts
    "ContractClient", "deploy_package", "EventDecoder", "generate_client",
    # DA / AICF / Randomness
    "DAClient", "AICFClient", "RandomnessClient",
    # Light client
    "verify_light_proof",
    # Utils
    "to_hex", "hex_to_bytes",
]
