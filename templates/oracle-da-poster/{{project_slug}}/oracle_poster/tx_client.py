"""
Transaction Client: thin, stdlib-only helper to submit contract calls that
update an on-chain oracle with new DA commitments (or other values).

Design goals:
- Zero mandatory third-party deps (only Python stdlib).
- Support multiple backends:
    1) "services" (default): REST-ish service that accepts contract-call JSON.
       POST {services_url}/tx/contractCall
       body: {
         "chain_id": "...",
         "from": "0x...",                  # optional; depends on signer_mode
         "to": "0x<contract>",
         "method": "set(bytes32,bytes)",   # ABI signature or method name
         "args": [...],                    # JSON-serializable arguments
         "gas_limit": 200000,
         "max_fee":  "1000",               # integer or string
         "nonce":    1,                    # optional
         "signer": {                       # optional, see signer_mode below
           "mode": "label|raw",
           "label": "poster",              # use server-side key (label)
           "private_key": "0x...",         # or raw key if server allows
           "mnemonic": "abandon ...",      # or mnemonic, if permitted
         }
       }
       Response: { "ok": true, "tx_hash": "0x...", "receipt": {...}? }

    2) "rpc": JSON-RPC direct to the node. Two shapes are common in devnets:
         - "tx_sendContract": node performs ABI packing & submit.
           params: { chain_id,to,method,args,gas_limit,max_fee,nonce,from?,sig? }
         - "tx_sendRaw": push a pre-signed raw tx blob/hex (advanced; not used here).

  You can adapt paths/methods to your stack via PosterEnv.

Signing options:
- signer_mode="services": you provide a signer label and the *service* signs
  with an HSM or configured key. (Recommended for production.)
- signer_mode="raw": you provide a local secret (private_key/mnemonic) to the
  service which signs for you. (Only for secured/dev environments.)
- signer_mode="external": provide tx already signed (tx_obj includes "sig"),
  client forwards it as-is. (Bring-your-own signer.)

This client does not implement local cryptographic signing to stay dependency-free.
If you need local signing, integrate your chain SDK in this file (see TODO section).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import PosterEnv, get_logger

_LOG = get_logger("oracle_poster.tx_client")


Json = Union[None, bool, int, float, str, List["Json"], Dict[str, "Json"]]


# ======================================================================================
# Errors & result models
# ======================================================================================


class TxClientError(RuntimeError):
    """Raised for transaction submission or polling errors."""


@dataclass(frozen=True)
class TxSubmitResult:
    """
    Outcome of a contract-call attempt.

    Attributes:
        ok:         Whether submission was accepted by the backend.
        tx_hash:    Transaction hash if known.
        endpoint:   URL or RPC endpoint.
        status:     HTTP status (services) or 200 in RPC success path.
        response:   Parsed JSON response body if available.
        elapsed_ms: Milliseconds elapsed for the submit request.
    """

    ok: bool
    tx_hash: Optional[str]
    endpoint: str
    status: Optional[int]
    response: Optional[Dict[str, Any]]
    elapsed_ms: int


@dataclass(frozen=True)
class TxReceipt:
    """
    Minimal transaction receipt representation used by the template.

    Common fields across stacks:
        tx_hash:      0x-prefixed hash
        block_hash:   0x-prefixed hash
        block_height: integer block height
        success:      boolean execution status
        gas_used:     integer gas
        logs:         list of log/event objects (impl-specific)
        raw:          original receipt (unmodified)
    """

    tx_hash: str
    block_hash: Optional[str]
    block_height: Optional[int]
    success: Optional[bool]
    gas_used: Optional[int]
    logs: Optional[List[Dict[str, Any]]]
    raw: Dict[str, Any]


# ======================================================================================
# HTTP / JSON helpers (stdlib)
# ======================================================================================


def _json_request(
    url: str, *, method: str, json_body: Optional[Dict[str, Any]], timeout: int
) -> Tuple[int, Dict[str, Any]]:
    body_bytes: Optional[bytes] = None
    headers = {"Content-Type": "application/json"}
    if json_body is not None:
        body_bytes = json.dumps(json_body).encode("utf-8")
    req = Request(url=url, data=body_bytes, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError as e:
                raise TxClientError(
                    f"Non-JSON response from {url} (status {status}): {raw[:256]!r}"
                ) from e
            return int(status), parsed
    except HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise TxClientError(f"HTTPError {e.code} for {url}: {raw[:256]!r}") from e
    except URLError as e:
        raise TxClientError(f"URLError for {url}: {e}") from e


def _jsonrpc(
    url: str, *, method: str, params: Dict[str, Any], timeout: int
) -> Dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) & 0x7FFFFFFF,
        "method": method,
        "params": params,
    }
    status, parsed = _json_request(
        url, method="POST", json_body=payload, timeout=timeout
    )
    if "error" in parsed:
        raise TxClientError(f"JSON-RPC error on {method}: {parsed['error']}")
    if "result" not in parsed:
        raise TxClientError(f"Malformed JSON-RPC response from {url}: {parsed}")
    return parsed["result"]


# ======================================================================================
# Client
# ======================================================================================


class TxClient:
    """
    Flexible transaction client.

    PosterEnv fields used:
        - tx_mode:              "services" (default) or "rpc"
        - services_url:         Base URL for service mode (no trailing slash)
        - rpc_url:              JSON-RPC endpoint for node
        - http_timeout_sec:     Timeout for HTTP/RPC requests (default 10)
        - tx_path_contract:     Service path for contract call (default: "/tx/contractCall")
        - tx_path_receipt:      Service path to get receipt (default: "/tx/receipt")
        - rpc_method_contract:  RPC method for contract call (default: "tx_sendContract")
        - rpc_method_receipt:   RPC method to get receipt (default: "tx_getReceipt")
        - signer_mode:          "services" | "raw" | "external"  (default: "services")
        - signer_label:         Label for server-side key (services mode)
        - signer_private_key:   Private key if using raw mode (optional; dev only)
        - signer_mnemonic:      Mnemonic if using raw mode (optional; dev only)
        - chain_id:             Chain id / name (string or int)
        - default_gas_limit:    Reasonable default (e.g., 200000)
        - default_max_fee:      Reasonable default (e.g., 1000)
        - tx_poll_interval_sec: Polling cadence when waiting for inclusion (default 1)
        - tx_poll_timeout_sec:  Max seconds to wait for inclusion (default 30)
    """

    def __init__(self, cfg: PosterEnv) -> None:
        self.cfg = cfg
        self.mode = (getattr(cfg, "tx_mode", None) or "services").strip().lower()
        self.services_url = (getattr(cfg, "services_url", None) or "").rstrip("/")
        self.rpc_url = getattr(cfg, "rpc_url", None)
        self.timeout = int(max(1, getattr(cfg, "http_timeout_sec", 10)))

        # Paths & method names
        self.tx_path_contract = getattr(cfg, "tx_path_contract", "/tx/contractCall")
        self.tx_path_receipt = getattr(cfg, "tx_path_receipt", "/tx/receipt")
        self.rpc_method_contract = getattr(
            cfg, "rpc_method_contract", "tx_sendContract"
        )
        self.rpc_method_receipt = getattr(cfg, "rpc_method_receipt", "tx_getReceipt")

        # Signer configuration
        self.signer_mode = (
            (getattr(cfg, "signer_mode", None) or "services").strip().lower()
        )
        self.signer_label = getattr(cfg, "signer_label", None)
        self.signer_private_key = getattr(cfg, "signer_private_key", None)
        self.signer_mnemonic = getattr(cfg, "signer_mnemonic", None)

        # Defaults
        self.chain_id = getattr(cfg, "chain_id", None)
        self.default_gas_limit = int(getattr(cfg, "default_gas_limit", 200_000))
        self.default_max_fee = int(getattr(cfg, "default_max_fee", 1_000))
        self.poll_interval = float(getattr(cfg, "tx_poll_interval_sec", 1.0))
        self.poll_timeout = float(getattr(cfg, "tx_poll_timeout_sec", 30.0))

        # Mode sanity
        if self.mode not in ("services", "rpc"):
            _LOG.warning("Unknown tx_mode=%r; defaulting to 'services'", self.mode)
            self.mode = "services"
        if self.mode == "services" and not self.services_url:
            _LOG.warning("tx_mode='services' but services_url is empty.")
        if self.mode == "rpc" and not self.rpc_url:
            _LOG.warning("tx_mode='rpc' but rpc_url is empty.")

    # ------------------------------------------------------------------------------
    # Public API

    def send_contract_call(
        self,
        *,
        to: str,
        method: str,
        args: List[Json],
        from_addr: Optional[str] = None,
        gas_limit: Optional[int] = None,
        max_fee: Optional[int] = None,
        nonce: Optional[int] = None,
        wait_for_inclusion: bool = True,
    ) -> Tuple[TxSubmitResult, Optional[TxReceipt]]:
        """
        Submit a contract call and (optionally) wait for inclusion.
        Returns (submit_result, receipt_or_none).
        """
        gas = int(gas_limit or self.default_gas_limit)
        fee = int(max_fee or self.default_max_fee)

        submit_start = time.time()
        if self.mode == "services":
            endpoint = f"{self.services_url}{self.tx_path_contract}"
            body: Dict[str, Any] = {
                "chain_id": self.chain_id,
                "from": from_addr,
                "to": to,
                "method": method,
                "args": args,
                "gas_limit": gas,
                "max_fee": fee,
            }
            if nonce is not None:
                body["nonce"] = int(nonce)

            signer = self._build_signer_payload()
            if signer:
                body["signer"] = signer

            _LOG.debug("POST %s (to=%s method=%s)", endpoint, to, method)
            status, parsed = _json_request(
                endpoint, method="POST", json_body=body, timeout=self.timeout
            )
            tx_hash = _extract_tx_hash(parsed)
            elapsed_ms = int((time.time() - submit_start) * 1000)
            ok = bool(parsed.get("ok", status == 200)) and bool(tx_hash)
            submit = TxSubmitResult(
                ok=ok,
                tx_hash=tx_hash,
                endpoint=endpoint,
                status=status,
                response=parsed,
                elapsed_ms=elapsed_ms,
            )

        else:
            # RPC mode
            rpc_url = self.rpc_url or ""
            params: Dict[str, Any] = {
                "chain_id": self.chain_id,
                "from": from_addr,
                "to": to,
                "method": method,
                "args": args,
                "gas_limit": gas,
                "max_fee": fee,
            }
            if nonce is not None:
                params["nonce"] = int(nonce)

            # Signer handling for RPC: many devnets allow "from" to be unlocked or
            # accept "signer" fields; adapt if needed.
            signer = self._build_signer_payload()
            if signer:
                params["signer"] = signer

            _LOG.debug(
                "JSON-RPC %s -> %s (to=%s method=%s)",
                self.rpc_method_contract,
                rpc_url,
                to,
                method,
            )
            result = _jsonrpc(
                rpc_url,
                method=self.rpc_method_contract,
                params=params,
                timeout=self.timeout,
            )
            tx_hash = _extract_tx_hash(result)
            elapsed_ms = int((time.time() - submit_start) * 1000)
            submit = TxSubmitResult(
                ok=bool(tx_hash),
                tx_hash=tx_hash,
                endpoint=rpc_url,
                status=200,
                response=result if isinstance(result, dict) else {"result": result},
                elapsed_ms=elapsed_ms,
            )

        if not submit.ok or not submit.tx_hash:
            return submit, None

        if not wait_for_inclusion:
            return submit, None

        # Poll for receipt
        try:
            receipt = self.poll_receipt(submit.tx_hash)
            return submit, receipt
        except TxClientError as e:
            _LOG.warning("Failed to poll receipt for %s: %s", submit.tx_hash, e)
            return submit, None

    def poll_receipt(self, tx_hash: str) -> TxReceipt:
        """
        Polls receipt until success or timeout. Raises TxClientError on timeout or errors.
        """
        deadline = time.time() + self.poll_timeout
        while time.time() < deadline:
            rcpt = self.get_receipt(tx_hash)
            if rcpt is not None:
                return rcpt
            time.sleep(self.poll_interval)
        raise TxClientError(f"Timed out waiting for receipt: {tx_hash}")

    def get_receipt(self, tx_hash: str) -> Optional[TxReceipt]:
        """
        Single-shot receipt query. Returns TxReceipt or None if not yet available.
        """
        if self.mode == "services":
            endpoint = f"{self.services_url}{self.tx_path_receipt}"
            body = {"tx_hash": tx_hash}
            status, parsed = _json_request(
                endpoint, method="POST", json_body=body, timeout=self.timeout
            )
            if status != 200:
                raise TxClientError(f"Receipt endpoint error {status}: {parsed}")
            if not parsed or not parsed.get("found", False):
                return None
            return _to_receipt(parsed.get("receipt") or parsed)

        # RPC mode
        rpc_url = self.rpc_url or ""
        result = _jsonrpc(
            rpc_url,
            method=self.rpc_method_receipt,
            params={"tx_hash": tx_hash},
            timeout=self.timeout,
        )
        # Conventions: result may be null/None when not found
        if result in (None, {}, []):
            return None
        return _to_receipt(result)

    # ------------------------------------------------------------------------------
    # Internals

    def _build_signer_payload(self) -> Optional[Dict[str, Any]]:
        """
        Build a signer block or return None (external/unlocked account).
        """
        mode = self.signer_mode
        if mode == "services":
            if not self.signer_label:
                _LOG.debug(
                    "signer_mode=services but no signer_label provided; assuming server default"
                )
                return {"mode": "label"}
            return {"mode": "label", "label": self.signer_label}

        if mode == "raw":
            signer: Dict[str, Any] = {"mode": "raw"}
            if self.signer_private_key:
                signer["private_key"] = self.signer_private_key
            if self.signer_mnemonic:
                signer["mnemonic"] = self.signer_mnemonic
            if len(signer) == 1:
                _LOG.warning("signer_mode=raw selected but no secret material set")
            return signer

        if mode == "external":
            # Expect caller to provide 'sig' in tx object (used mostly in RPC paths).
            return None

        _LOG.warning("Unknown signer_mode=%r; proceeding without signer", mode)
        return None


# ======================================================================================
# Mappers
# ======================================================================================


def _extract_tx_hash(obj: Any) -> Optional[str]:
    """
    Normalize how we find tx hash in various responses.
    """
    if isinstance(obj, dict):
        for key in ("tx_hash", "hash", "txHash", "transactionHash"):
            val = obj.get(key)
            if isinstance(val, str) and val.startswith("0x") and len(val) >= 10:
                return val
        # Sometimes nested: {"result": {"tx_hash": ...}}
        if "result" in obj and isinstance(obj["result"], dict):
            return _extract_tx_hash(obj["result"])
    if isinstance(obj, str) and obj.startswith("0x") and len(obj) >= 10:
        return obj
    return None


def _to_receipt(raw: Dict[str, Any]) -> TxReceipt:
    """
    Map heterogeneous receipt shapes to our minimal TxReceipt.
    """
    txh = raw.get("tx_hash") or raw.get("transactionHash") or raw.get("hash") or ""
    bh = raw.get("block_hash") or raw.get("blockHash")
    height = raw.get("block_height") or raw.get("blockNumber") or raw.get("height")
    success = raw.get("success")
    if success is None:
        # Some stacks use status 1/0
        status = raw.get("status")
        if isinstance(status, int):
            success = status == 1
        elif isinstance(status, str) and status.isdigit():
            success = int(status) == 1
    gas_used = raw.get("gas_used") or raw.get("gasUsed")
    logs = raw.get("logs") or raw.get("events")
    return TxReceipt(
        tx_hash=str(txh),
        block_hash=str(bh) if bh is not None else None,
        block_height=(
            int(height)
            if isinstance(height, (int,))
            or (isinstance(height, str) and height.isdigit())
            else None
        ),
        success=bool(success) if success is not None else None,
        gas_used=(
            int(gas_used)
            if isinstance(gas_used, (int,))
            or (isinstance(gas_used, str) and gas_used.isdigit())
            else None
        ),
        logs=logs if isinstance(logs, list) else None,
        raw=raw,
    )


# ======================================================================================
# TODO: Local signing (optional)
# ======================================================================================
#
# If you want to sign locally (without the services), you can:
#
#  1) Plug in your chain's Python SDK here (recommended). Example sketch:
#
#       try:
#           from animica_sdk import Wallet, Client
#       except Exception:
#           Wallet = None
#           Client = None
#
#       def send_with_sdk(...):
#           if Wallet is None or Client is None:
#               raise TxClientError("Animica SDK not installed; pip install animica-sdk")
#           wallet = Wallet.from_mnemonic(self.signer_mnemonic)  # or from_privkey(...)
#           client = Client(self.rpc_url, chain_id=self.chain_id)
#           tx_hash = client.send_contract_call(wallet, to=to, method=method, args=args, gas=gas, fee=fee, nonce=nonce)
#           ...
#
#  2) Implement a deterministic demo signer for your devnet format if it's intentionally
#     simple, then pass the resulting pre-signed fields under signer_mode="external".
#
