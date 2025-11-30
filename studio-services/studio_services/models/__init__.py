from __future__ import annotations

"""
Public model surface for studio-services.

This package exposes typed request/response models used by the HTTP API.
To keep imports fast (and tolerant while files are generated during bootstrap),
we lazily re-export symbols from submodules via __getattr__ (PEP 562).

Submodules:
- common.py      → Hex, Hash, Address, ChainId, Pagination
- deploy.py      → DeployRequest, DeployResponse, PreflightRequest, PreflightResponse
- verify.py      → VerifyRequest, VerifyStatus, VerifyResult
- faucet.py      → FaucetRequest, FaucetResponse
- artifacts.py   → ArtifactPut, ArtifactMeta
- simulate.py    → SimulateCall, SimulateResult
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any, Dict, Tuple

__all__ = [
    # common
    "Hex",
    "Hash",
    "Address",
    "ChainId",
    "Pagination",
    # deploy
    "DeployRequest",
    "DeployResponse",
    "PreflightRequest",
    "PreflightResponse",
    # verify
    "VerifyRequest",
    "VerifyStatus",
    "VerifyResult",
    # faucet
    "FaucetRequest",
    "FaucetResponse",
    # artifacts
    "ArtifactPut",
    "ArtifactMeta",
    # simulate
    "SimulateCall",
    "SimulateResult",
]

# name -> (module, attribute)
_EXPORTS: Dict[str, Tuple[str, str]] = {
    # common
    "Hex": ("studio_services.models.common", "Hex"),
    "Hash": ("studio_services.models.common", "Hash"),
    "Address": ("studio_services.models.common", "Address"),
    "ChainId": ("studio_services.models.common", "ChainId"),
    "Pagination": ("studio_services.models.common", "Pagination"),
    # deploy
    "DeployRequest": ("studio_services.models.deploy", "DeployRequest"),
    "DeployResponse": ("studio_services.models.deploy", "DeployResponse"),
    "PreflightRequest": ("studio_services.models.deploy", "PreflightRequest"),
    "PreflightResponse": ("studio_services.models.deploy", "PreflightResponse"),
    # verify
    "VerifyRequest": ("studio_services.models.verify", "VerifyRequest"),
    "VerifyStatus": ("studio_services.models.verify", "VerifyStatus"),
    "VerifyResult": ("studio_services.models.verify", "VerifyResult"),
    # faucet
    "FaucetRequest": ("studio_services.models.faucet", "FaucetRequest"),
    "FaucetResponse": ("studio_services.models.faucet", "FaucetResponse"),
    # artifacts
    "ArtifactPut": ("studio_services.models.artifacts", "ArtifactPut"),
    "ArtifactMeta": ("studio_services.models.artifacts", "ArtifactMeta"),
    # simulate
    "SimulateCall": ("studio_services.models.simulate", "SimulateCall"),
    "SimulateResult": ("studio_services.models.simulate", "SimulateResult"),
}


def __getattr__(name: str) -> Any:
    """
    Lazily import and return the requested symbol from its submodule.
    """
    try:
        mod_name, attr = _EXPORTS[name]
    except KeyError as e:  # pragma: no cover - standard attribute error path
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from e
    mod = import_module(mod_name)
    return getattr(mod, attr)


def __dir__() -> list[str]:  # pragma: no cover - tiny introspection helper
    return sorted(list(globals().keys()) + __all__)


if TYPE_CHECKING:
    # For static type checkers, import eagerly
    from .artifacts import ArtifactMeta, ArtifactPut
    from .common import Address, ChainId, Hash, Hex, Pagination
    from .deploy import (DeployRequest, DeployResponse, PreflightRequest,
                         PreflightResponse)
    from .faucet import FaucetRequest, FaucetResponse
    from .simulate import SimulateCall, SimulateResult
    from .verify import VerifyRequest, VerifyResult, VerifyStatus
