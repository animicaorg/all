"""
omni_sdk.contracts.client
=========================

A small, ergonomic contract client that:
- Encodes function calls from an ABI
- Builds/sends transactions using PQ signatures
- (Optionally) simulates read-only calls when a simulator is provided
- Decodes return values and logs with the same ABI

The client is intentionally thin and delegates to:
- `omni_sdk.types.abi` for ABI validation/encoding/decoding
- `omni_sdk.tx.build/encode/send` for tx lifecycle
- `omni_sdk.wallet.signer.PQSigner` for PQ signing
- `omni_sdk.address` for address validation/formatting

Example
-------
    from omni_sdk.rpc.http import HttpClient
    from omni_sdk.wallet.signer import Dilithium3Signer
    from omni_sdk.contracts.client import ContractClient

    rpc = HttpClient(url="http://127.0.0.1:8545")
    signer = Dilithium3Signer.from_mnemonic("... 24 words ...")

    # Load ABI JSON (dict) and construct client
    abi = {...}
    c = ContractClient(rpc=rpc, address="anim1xyz...", abi=abi, chain_id=1)

    # Read-only simulation (if simulator provided; see notes below)
    # result = c.call("get", args=[], simulator=studio_services_sim)

    # Build + sign + send a state-changing tx, then await receipt
    receipt = c.send(
        fn="inc",
        args=[1],
        signer=signer,
        nonce=c.get_nonce(signer.address),
        max_fee=2_000_000,   # units/gas * price; pick policy for your network
    )

Simulation
----------
Animica node RPC (per the baseline OpenRPC) does not expose a standard "call"
endpoint for read-only execution. To support local dev flows, pass a
`simulator` callable:

    def simulator(address: str, calldata: bytes, value: int | None = 0) -> bytes: ...

For production verification-grade simulation, use `studio-services`' /simulate
endpoint via a tiny adapter that implements the callable above.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple, Union

# --- SDK imports (typed where available) --------------------------------------

# Tx lifecycle
from omni_sdk.tx import build as tx_build
from omni_sdk.tx import encode as tx_encode
from omni_sdk.tx import send as tx_send

# PQ signer
from omni_sdk.wallet.signer import PQSigner  # type: ignore

# Address helpers (validation/normalization)
try:
    from omni_sdk.address import is_valid as _addr_is_valid  # type: ignore
except Exception:  # pragma: no cover
    def _addr_is_valid(a: str) -> bool:
        return isinstance(a, str) and a.startswith("anim1") and len(a) > 10

# ABI helpers
try:
    # Expected surface from omni_sdk.types.abi:
    #  - normalize_abi(abi_obj) -> dict
    #  - encode_call(abi, fn_name, args) -> bytes
    #  - decode_return(abi, fn_name, data) -> Any
    from omni_sdk.types.abi import normalize_abi, encode_call, decode_return  # type: ignore
except Exception as _e:  # pragma: no cover
    raise RuntimeError("omni_sdk.types.abi is required by ContractClient") from _e

# Errors
try:
    from omni_sdk.errors import RpcError, TxError, AbiError  # type: ignore
except Exception:  # pragma: no cover
    class RpcError(RuntimeError): ...
    class TxError(RuntimeError): ...
    class AbiError(RuntimeError): ...


# --- Minimal RPC client protocol ---------------------------------------------

class _RpcClient(Protocol):
    def call(self, method: str, params: Optional[dict | list] = None) -> Any: ...


# --- Types -------------------------------------------------------------------

SimulatorFn = Callable[[str, bytes, int], bytes]
"""Callable that executes a read-only call and returns raw return bytes."""

JsonDict = Dict[str, Any]


@dataclass(frozen=True)
class FeeHints:
    """
    Optional fee policy hints for convenience.
    """
    base_fee: Optional[int] = None
    tip: int = 0
    surge_multiplier: float = 1.0
    floor: Optional[int] = None
    cap: Optional[int] = None


# --- Client ------------------------------------------------------------------

class ContractClient:
    """
    ABI-driven client bound to a deployed contract address.

    Parameters
    ----------
    rpc : RPC client with a `.call(method, params)` function.
    address : bech32m address string (anim1…).
    abi : dict or list conforming to sdk/common/schemas/abi.schema.json.
    chain_id : integer chain id for tx sign-bytes domain separation.

    Notes
    -----
    The client does *not* own funds or a nonce. To send calls, provide:
      - a `PQSigner` (for signing)
      - the `nonce` for the sender
      - a `max_fee` and optional `gas_limit` (or let the client suggest)
    """

    def __init__(self, *, rpc: _RpcClient, address: str, abi: Mapping[str, Any] | Sequence[Mapping[str, Any]], chain_id: int):
        if not _addr_is_valid(address):
            raise ValueError(f"Invalid contract address: {address!r}")
        self._rpc: _RpcClient = rpc
        self._address: str = address
        self._abi: JsonDict = normalize_abi(abi)
        self._chain_id: int = int(chain_id)

    # ------------------------------------------------------------------ Accessors

    @property
    def address(self) -> str:
        return self._address

    @property
    def chain_id(self) -> int:
        return self._chain_id

    @property
    def abi(self) -> JsonDict:
        return self._abi

    # ------------------------------------------------------------------ RPC helpers

    def get_nonce(self, addr: str) -> int:
        """
        Read the sender's current nonce via RPC.
        """
        try:
            res = self._rpc.call("state.getNonce", [addr])
        except Exception as e:  # pragma: no cover
            raise RpcError(f"state.getNonce failed: {e}") from e
        if not isinstance(res, int):
            raise RpcError(f"unexpected nonce payload: {res!r}")
        return res

    def get_balance(self, addr: str) -> int:
        """
        Read the address balance via RPC.
        """
        try:
            res = self._rpc.call("state.getBalance", [addr])
        except Exception as e:  # pragma: no cover
            raise RpcError(f"state.getBalance failed: {e}") from e
        if not isinstance(res, int):
            raise RpcError(f"unexpected balance payload: {res!r}")
        return res

    # ------------------------------------------------------------------ Encoding/decoding

    def encode_call_data(self, fn: str, args: Sequence[Any]) -> bytes:
        """
        Encode function call data (function selector + encoded args) using the ABI.
        """
        try:
            return encode_call(self._abi, fn, list(args))
        except Exception as e:
            raise AbiError(f"encode_call failed for {fn}({args}): {e}") from e

    def decode_return(self, fn: str, data: bytes) -> Any:
        """
        Decode return bytes for `fn` according to the ABI.
        """
        try:
            return decode_return(self._abi, fn, bytes(data))
        except Exception as e:
            raise AbiError(f"decode_return failed for {fn}: {e}") from e

    # ------------------------------------------------------------------ Gas & fees

    def suggest_gas_limit(self, calldata: bytes, *, kind: str = "call", safety_multiplier: float = 1.10) -> int:
        """
        Suggest a gasLimit using intrinsic gas + safety factor.
        """
        kind_lit = "call" if kind not in ("call", "deploy", "transfer") else kind
        return tx_build.suggest_gas_limit(kind_lit, calldata_len=len(calldata), safety_multiplier=float(safety_multiplier))

    def suggest_max_fee(self, *, base_fee: int, tip: int = 0, surge_multiplier: float = 1.0, floor: Optional[int] = None, cap: Optional[int] = None) -> int:
        """
        Suggest a maxFee using the `tx.build.suggest_max_fee` helper.
        """
        return tx_build.suggest_max_fee(base_fee=base_fee, tip=tip, surge_multiplier=surge_multiplier, floor=floor, cap=cap)

    # ------------------------------------------------------------------ Read-only call

    def call(
        self,
        fn: str,
        args: Sequence[Any] | None = None,
        *,
        value: int = 0,
        simulator: Optional[SimulatorFn] = None,
    ) -> Any:
        """
        Execute a read-only call via a provided simulator.

        Parameters
        ----------
        fn : function name in the ABI
        args : positional argument list
        value : optional value to pass (rare; typically 0)
        simulator : callable(address, calldata, value) -> bytes
            Provide an adapter to a local VM or studio-services /simulate endpoint.

        Returns
        -------
        Decoded return value according to the ABI.

        Raises
        ------
        NotImplementedError if `simulator` is not provided.
        """
        args = list(args or [])
        calldata = self.encode_call_data(fn, args)

        if simulator is None:
            raise NotImplementedError(
                "No simulator provided. Pass `simulator=(address, data, value)->bytes` "
                "or use `send()` to execute on-chain."
            )

        raw = simulator(self._address, calldata, int(value))
        if not isinstance(raw, (bytes, bytearray)):
            raise RuntimeError("simulator must return bytes")
        return self.decode_return(fn, bytes(raw))

    # ------------------------------------------------------------------ State-changing send

    def build_tx(
        self,
        fn: str,
        args: Sequence[Any] | None,
        *,
        sender: str,
        nonce: int,
        max_fee: int,
        gas_limit: Optional[int] = None,
        value: int = 0,
    ):
        """
        Build a contract-call transaction dataclass (not signed).
        """
        if not _addr_is_valid(sender):
            raise ValueError(f"Invalid sender address: {sender!r}")

        calldata = self.encode_call_data(fn, list(args or []))
        gl = gas_limit if gas_limit is not None else self.suggest_gas_limit(calldata, kind="call")

        return tx_build.call(
            from_addr=sender,
            to_addr=self._address,
            data=calldata,
            nonce=int(nonce),
            gas_limit=int(gl),
            max_fee=int(max_fee),
            chain_id=self._chain_id,
            value=int(value),
        )

    def send(
        self,
        fn: str,
        args: Sequence[Any] | None,
        *,
        signer: PQSigner,
        nonce: int,
        max_fee: int,
        gas_limit: Optional[int] = None,
        value: int = 0,
        await_receipt: bool = True,
        timeout_s: float = 60.0,
        poll_interval_s: float = 0.5,
    ) -> JsonDict:
        """
        Build, sign, and submit a state-changing contract call.

        Parameters
        ----------
        fn, args : ABI function name and arguments.
        signer : PQSigner with an address/alg_id/public_key.
        nonce : sender nonce to use.
        max_fee : max fee to include in the tx.
        gas_limit : optional gas limit; if None, we estimate from calldata size.
        value : optional value to send along with the call.
        await_receipt : if True, wait for the receipt and return it; otherwise returns {"txHash": ...}.

        Returns
        -------
        dict : receipt (if await_receipt) or {"txHash": "0x…"}.
        """
        # 1) Build tx (dataclass)
        tx = self.build_tx(
            fn, args,
            sender=signer.address,  # property of PQSigner
            nonce=nonce,
            max_fee=max_fee,
            gas_limit=gas_limit,
            value=value,
        )

        # 2) Prepare sign-bytes and sign with PQ
        signbytes = tx_encode.sign_bytes(tx)
        signature = signer.sign(signbytes)  # domain separation handled inside signer
        raw = tx_encode.pack_signed(
            tx,
            signature=signature,
            alg_id=signer.alg_id,
            public_key=signer.public_key,
        )

        # 3) Submit raw & optionally await receipt
        tx_hash = tx_send.submit_raw(self._rpc, raw)
        if not await_receipt:
            return {"txHash": tx_hash}

        receipt = tx_send.wait_for_receipt(
            self._rpc,
            tx_hash,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
        )
        return receipt


__all__ = ["ContractClient", "FeeHints", "SimulatorFn"]
