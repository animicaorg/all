"""
Animica Omni SDK — Python
Convenience exports for the most common client APIs.
"""

# Addresses
from .address import bech32_decode, bech32_encode, from_pubkey  # noqa: F401
# AICF (AI/Quantum)
# from .aicf.client import AICFClient  # noqa: F401
# Core config & errors
from .config import SDKConfig as Config  # noqa: F401
# Contracts
# from .contracts.client import ContractClient  # noqa: F401
# from .contracts.codegen import generate_client  # noqa: F401
# from .contracts.deployer import deploy_package  # noqa: F401
# from .contracts.events import EventDecoder  # noqa: F401
# Data Availability
# from .da.client import DAClient  # noqa: F401
from .errors import AbiError, RpcError, TxError, VerifyError  # noqa: F401
# Light client
# from .light_client.verify import verify_light_proof  # noqa: F401
# Randomness beacon
# from .randomness.client import RandomnessClient  # noqa: F401
# RPC
from .rpc.http import RpcClient  # noqa: F401
from .rpc.ws import WsClient  # noqa: F401
# Tx helpers
# from .tx.build import build_call, build_deploy, build_transfer  # noqa: F401
# from .tx.encode import build_sign_bytes  # noqa: F401
# from .tx.send import send_and_wait  # noqa: F401
# Utilities
from .utils.bytes import from_hex as hex_to_bytes, to_hex  # noqa: F401
from .version import __version__  # noqa: F401
# from .wallet.keystore import Keystore  # noqa: F401
# Wallet
# from .wallet.mnemonic import create_mnemonic, mnemonic_to_seed  # noqa: F401
# from .wallet.signer import (Dilithium3Signer,  # noqa: F401
#                             SphincsShake128sSigner)

__all__ = [
    "__version__",
    "Config",
    "RpcError",
    "TxError",
    "AbiError",
    "VerifyError",
    "RpcClient",
    "WsClient",
    "bech32_encode",
    "bech32_decode",
    "from_pubkey",
    "to_hex",
    "hex_to_bytes",
]
