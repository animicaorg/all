from __future__ import annotations

import ipaddress
import math
import os
import re
import threading
from pathlib import Path
from typing import Any

from rpc import errors as rpc_errors
from rpc.methods import method

from animica.cli import tx as tx_cli
from animica.cli import wallet as wallet_cli
from rpc.methods import chain as chain_methods
from rpc.methods import state as state_methods
from rpc.methods import tx as tx_methods

try:  # pragma: no cover - Windows fallback for local developer runs
    import fcntl
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,120}$")
_STORE_LOCK = threading.Lock()


class _LockedWalletStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self.lock_file: Any | None = None
        self.store: dict[str, Any] | None = None
        self.thread_lock_acquired = False

    def __enter__(self) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _STORE_LOCK.acquire()
        self.thread_lock_acquired = True
        try:
            self.lock_file = self.lock_path.open("a+", encoding="utf-8")
            if fcntl is not None:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX)
            self.store = wallet_cli._load_store(self.path)
            return self.store
        except BaseException:
            self._release()
            raise

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        try:
            if exc_type is None and self.store is not None:
                wallet_cli._save_store(self.path, self.store)
        finally:
            self._release()
        return False

    def _release(self) -> None:
        try:
            if self.lock_file is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                finally:
                    self.lock_file.close()
                    self.lock_file = None
        finally:
            if self.thread_lock_acquired:
                self.thread_lock_acquired = False
                _STORE_LOCK.release()


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_int(value: Any, *, field: str, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise rpc_errors.InvalidParams(f"{field} must be an integer")
    if isinstance(value, int):
        out = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise rpc_errors.InvalidParams(f"{field} is required")
        try:
            out = int(text, 16) if text.startswith(("0x", "0X")) else int(text)
        except ValueError as exc:
            raise rpc_errors.InvalidParams(f"{field} must be an integer") from exc
    else:
        raise rpc_errors.InvalidParams(f"{field} must be an integer")
    if minimum is not None and out < minimum:
        raise rpc_errors.InvalidParams(f"{field} must be >= {minimum}")
    return out


def _clean_label(value: Any | None) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        prefix = os.getenv("ANIMICA_WALLET_RPC_LABEL_PREFIX", "rpc-wallet").strip() or "rpc-wallet"
        return f"{prefix}-{os.urandom(4).hex()}"
    if not isinstance(value, str):
        raise rpc_errors.InvalidParams("label must be a string")
    label = value.strip()
    if not _LABEL_RE.match(label):
        raise rpc_errors.InvalidParams(
            "label may only contain letters, numbers, dot, underscore, colon, at, slash, and dash"
        )
    return label


def _wallet_path(wallet_file: Any | None = None) -> Path:
    if wallet_file:
        return wallet_cli._wallet_file_path(Path(str(wallet_file)))
    return wallet_cli._wallet_file_path(None)


def _locked_store(path: Path) -> _LockedWalletStore:
    return _LockedWalletStore(path)


def _ctx_headers(ctx: Any | None) -> dict[str, str]:
    try:
        return {str(k).lower(): str(v) for k, v in getattr(ctx, "headers", {}).items()}
    except Exception:
        return {}


def _ctx_client_ip(ctx: Any | None) -> str | None:
    if ctx is None:
        return None
    client = getattr(ctx, "client", None)
    if isinstance(client, tuple) and client:
        return str(client[0])
    host = getattr(client, "host", None)
    if host:
        return str(host)
    return None


def _is_local_ip(value: str | None) -> bool:
    if not value:
        return True
    try:
        ip_obj = ipaddress.ip_address(value)
        if ip_obj.is_loopback or ip_obj.is_unspecified:
            return True
        allow_private = os.getenv("ANIMICA_WALLET_RPC_ALLOW_PRIVATE", "1").strip().lower()
        if allow_private not in {"0", "false", "no", "off"}:
            return bool(ip_obj.is_private or ip_obj.is_link_local)
        return False
    except Exception:
        return False


def _is_public_edge_request(ctx: Any | None) -> bool:
    """True if the request reached us through the public nginx edge.

    The node RPC binds 127.0.0.1 only, so a genuine local/internal caller connects
    directly with NONE of these headers, while every public vhost sets X-Real-IP /
    X-Forwarded-For. Presence => treat as REMOTE regardless of the loopback socket
    peer (which is always nginx/docker-proxy) or any client-spoofed header value.

    Closes the unauthenticated wallet.* spend/key oracle (incident 2026-07-09):
    behind nginx every request looked loopback, so _is_local_ip() authorized
    wallet.send/importprivkey/etc. for the whole internet. Internal direct-localhost
    callers keep working because they send no proxy headers.
    """
    h = _ctx_headers(ctx)
    return bool(
        h.get("x-forwarded-for") or h.get("x-real-ip") or h.get("x-animica-public-edge")
    )


def _authorize_wallet_rpc(ctx: Any | None) -> None:
    if _env_truthy("ANIMICA_WALLET_RPC_ALLOW_REMOTE"):
        return
    client_ip = _ctx_client_ip(ctx)
    if not _is_public_edge_request(ctx) and _is_local_ip(client_ip):
        return
    token = os.getenv("ANIMICA_RPC_ADMIN_TOKEN")
    if token and _ctx_headers(ctx).get("x-animica-admin-token") == token:
        return
    raise rpc_errors.AccessDenied(
        "wallet RPC requires localhost, a trusted private network, or a valid admin token",
        method="wallet",
        required="localhost/private network or x-animica-admin-token",
    )


def _safe_wallet_view(entry: dict[str, Any], *, wallet_file: Path, created: bool) -> dict[str, Any]:
    return {
        "address": entry.get("address"),
        "label": entry.get("label"),
        "algId": int(entry.get("alg_id") or entry.get("algId") or 0),
        "algName": entry.get("alg_name") or entry.get("algName"),
        "createdAt": entry.get("created_at") or entry.get("createdAt"),
        "walletFile": str(wallet_file),
        "created": created,
    }


def _find_wallet_entry(
    store: dict[str, Any],
    *,
    label: str | None = None,
    address: str | None = None,
    identifier: str | None = None,
) -> dict[str, Any] | None:
    needle_label = label.lower() if isinstance(label, str) else None
    needle_addr = address.lower() if isinstance(address, str) else None
    needle_id = identifier.lower() if isinstance(identifier, str) else None
    for raw in store.get("wallets", []):
        if not isinstance(raw, dict):
            continue
        raw_label = str(raw.get("label") or "").lower()
        raw_addr = str(raw.get("address") or "").lower()
        raw_pub = str(raw.get("public_key_hex") or raw.get("publicKeyHex") or "").lower()
        if needle_label and raw_label == needle_label:
            return raw
        if needle_addr and raw_addr == needle_addr:
            return raw
        if needle_id and needle_id in {raw_label, raw_addr, raw_pub}:
            return raw
    return None


def _default_wallet_identifier(store: dict[str, Any]) -> str | None:
    for key in ("ANIMICA_HOT_WALLET_LABEL", "ANIMICA_WALLET_RPC_LABEL", "ANIMICA_DEFAULT_ADDRESS"):
        value = os.getenv(key)
        if value and value.strip():
            return value.strip()
    for key in ("default_address", "default"):
        value = store.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    wallets = [entry for entry in store.get("wallets", []) if isinstance(entry, dict)]
    if len(wallets) == 1:
        value = wallets[0].get("address") or wallets[0].get("label")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_send_payload(
    to: Any = None,
    amount: Any = None,
    fee: Any = None,
    **kwargs: Any,
) -> dict[str, Any]:
    if isinstance(to, dict) and amount is None and fee is None:
        payload = {k: v for k, v in kwargs.items() if v is not None}
        payload.update(dict(to))
    else:
        payload = {k: v for k, v in kwargs.items() if v is not None}
        payload.setdefault("to", to)
        payload.setdefault("amount", amount)
        payload.setdefault("fee", fee)

    if payload.get("amount") is None:
        payload["amount"] = payload.get("amountAtoms") or payload.get("value") or payload.get("valueAtoms")
    if payload.get("fee") is None:
        payload["fee"] = payload.get("feeAtoms") or payload.get("flatFeeAtoms")
    if payload.get("from_addr") is None:
        payload["from_addr"] = (
            payload.get("from")
            or payload.get("fromAddress")
            or payload.get("from_address")
        )
    return payload


def _height_from_mapping(source: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        try:
            return _coerce_int(value, field=f"head.{key}", minimum=0)
        except rpc_errors.InvalidParams:
            continue
    return None


def _height_from_object(source: Any, keys: tuple[str, ...]) -> int | None:
    if isinstance(source, dict):
        return _height_from_mapping(source, keys)
    for key in keys:
        value = getattr(source, key, None)
        if value is None:
            continue
        try:
            return _coerce_int(value, field=f"head.{key}", minimum=0)
        except rpc_errors.InvalidParams:
            continue
    return None


def _head_height_from_snapshot(head: Any) -> int | None:
    active_keys = (
        "height",
        "headHeight",
        "head_height",
        "blockHeight",
        "block_height",
        "number",
    )
    canonical_keys = ("canonicalHeight", "canonical_height")

    sources: list[Any] = []
    if isinstance(head, dict):
        sources.append(head)
        header = head.get("header")
        if header is not None:
            sources.append(header)
    elif isinstance(head, (tuple, list)):
        if head:
            try:
                first = _coerce_int(head[0], field="head.height", minimum=0)
            except rpc_errors.InvalidParams:
                first = 0
            if first > 0:
                return first
            if len(head) >= 2:
                nested = _head_height_from_snapshot(head[1])
                if nested is not None and nested > 0:
                    return nested
            return first
    else:
        try:
            return _coerce_int(head, field="head.height", minimum=0)
        except rpc_errors.InvalidParams:
            return None

    zero_height: int | None = None
    for source in sources:
        height = _height_from_object(source, active_keys)
        if height is None:
            continue
        if height > 0:
            return height
        zero_height = 0

    for source in sources:
        height = _height_from_object(source, canonical_keys)
        if height is not None:
            return height

    return zero_height


def _head_height() -> int:
    try:
        head = chain_methods.chain_get_head()
        height = _head_height_from_snapshot(head)
        if height is not None:
            return height
    except Exception:
        pass
    return 0


def _chain_identity() -> dict[str, Any]:
    try:
        identity = chain_methods.chain_get_chain_identity()
        if isinstance(identity, dict):
            return dict(identity)
    except Exception:
        pass
    return {"chainId": chain_methods.chain_get_chain_id()}


def _extract_txid(result: Any) -> str:
    if isinstance(result, str) and result.strip():
        return result.strip()
    if isinstance(result, dict):
        for key in ("txid", "txId", "tx_hash", "txHash", "hash", "transactionHash"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    raise rpc_errors.InternalError("tx.sendRawTransaction did not return a txid")


def _record_pending(
    store: dict[str, Any],
    *,
    from_addr: str,
    to_addr: str,
    txid: str,
    amount_atoms: int,
    fee_reserved_atoms: int,
    chain_id: int,
    nonce: int,
) -> None:
    entry = _find_wallet_entry(store, address=from_addr)
    if entry is None:
        return
    pending = entry.setdefault("pending_txs", [])
    if not isinstance(pending, list):
        pending = []
        entry["pending_txs"] = pending
    payload = {
        "tx_hash": txid,
        "from": from_addr,
        "to": to_addr,
        "value": int(amount_atoms),
        "fee_reserved": int(fee_reserved_atoms),
        "reserve_amount": int(amount_atoms) + int(fee_reserved_atoms),
        "nonce": int(nonce),
        "chain_id": int(chain_id),
        "status": "broadcast",
    }
    for existing in pending:
        if isinstance(existing, dict) and existing.get("tx_hash") == txid:
            existing.update(payload)
            return
    pending.append(payload)


@method(
    "wallet.createAddress",
    desc="Create a new Animica wallet address in the node wallet store.",
    aliases=("wallet_createAddress",),
)
def wallet_create_address(
    label: str | None = None,
    alg: str | None = None,
    allow_insecure_fallback: bool | None = None,
    wallet_file: str | None = None,
    ctx: Any | None = None,
    **_: Any,
) -> dict[str, Any]:
    _authorize_wallet_rpc(ctx)
    clean_label = _clean_label(label)
    path = _wallet_path(wallet_file)
    with _locked_store(path) as store:
        if _find_wallet_entry(store, label=clean_label) is not None:
            raise rpc_errors.AlreadyExists("wallet label", label=clean_label)

        try:
            alg_info = wallet_cli._resolve_signature_alg(alg)
            entry = wallet_cli._generate_entry(
                clean_label,
                allow_fallback=bool(allow_insecure_fallback)
                or _env_truthy("ANIMICA_WALLET_RPC_ALLOW_INSECURE_FALLBACK"),
                alg_info=alg_info,
                allow_default_fallback=alg is None,
            )
            if wallet_cli.HAVE_PQ:
                wallet_cli.validate_address(entry.address, expect_hrp="anim")
        except rpc_errors.RpcError:
            raise
        except Exception as exc:
            raise rpc_errors.InternalError(
                "failed to create Animica wallet address",
                reason=exc.__class__.__name__,
            ) from exc

        raw = entry.to_dict()
        store.setdefault("wallets", []).append(raw)
        if not store.get("default_address"):
            store["default_address"] = entry.address
            store["default"] = entry.label
        return _safe_wallet_view(raw, wallet_file=path, created=True)


@method(
    "wallet.send",
    desc="Build, sign, and submit an Animica value transfer from a node wallet.",
    aliases=("wallet_send",),
)
def wallet_send(
    to: Any = None,
    amount: Any = None,
    fee: Any = None,
    label: str | None = None,
    from_addr: str | None = None,
    wallet_file: str | None = None,
    gas_limit: Any = None,
    max_fee: Any = None,
    ctx: Any | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    _authorize_wallet_rpc(ctx)
    payload = _normalize_send_payload(
        to,
        amount,
        fee,
        label=label,
        from_addr=from_addr,
        wallet_file=wallet_file,
        gas_limit=gas_limit,
        max_fee=max_fee,
        **kwargs,
    )

    to_addr = payload.get("to") or payload.get("destination") or payload.get("destinationAddress")
    if not isinstance(to_addr, str) or not to_addr.strip():
        raise rpc_errors.InvalidParams("to address is required")
    to_addr = to_addr.strip()
    amount_atoms = _coerce_int(payload.get("amount"), field="amount", minimum=1)
    fee_atoms = _coerce_int(payload.get("fee") or 0, field="fee", minimum=0)

    resolved_gas_limit = _coerce_int(
        payload.get("gas_limit") or payload.get("gasLimit") or os.getenv("ANIMICA_WALLET_RPC_GAS_LIMIT", "21000"),
        field="gasLimit",
        minimum=1,
    )
    max_fee_value = payload.get("max_fee") or payload.get("maxFee")
    if max_fee_value is None:
        if fee_atoms > 0:
            resolved_max_fee = max(1, math.ceil(fee_atoms / resolved_gas_limit))
        else:
            resolved_max_fee = _coerce_int(
                os.getenv("ANIMICA_WALLET_RPC_MAX_FEE", "1"),
                field="maxFee",
                minimum=0,
            )
    else:
        resolved_max_fee = _coerce_int(max_fee_value, field="maxFee", minimum=0)
    fee_reserved_atoms = int(resolved_gas_limit) * int(resolved_max_fee)

    path = _wallet_path(payload.get("wallet_file"))
    with _locked_store(path) as store:
        identifier = (
            payload.get("from_addr")
            or payload.get("label")
            or payload.get("wallet")
            or payload.get("walletId")
            or _default_wallet_identifier(store)
        )
        if not isinstance(identifier, str) or not identifier.strip():
            raise rpc_errors.InvalidParams(
                "No sending wallet configured; set a default wallet or pass label/from"
            )
        entry = _find_wallet_entry(store, identifier=identifier.strip())
        if entry is None:
            raise rpc_errors.NotFound("wallet", identifier=identifier)

        from_address = str(entry.get("address") or "").strip()
        pk_hex = str(entry.get("public_key_hex") or entry.get("publicKeyHex") or "").strip()
        sk_hex = str(entry.get("secret_key_hex") or entry.get("secretKeyHex") or "").strip()
        if not from_address or not pk_hex or not sk_hex:
            raise rpc_errors.InvalidParams("wallet entry is missing address or signing keys")
        used_alg_id = _coerce_int(entry.get("alg_id") or entry.get("algId") or 0x1001, field="algId")

        chain_identity = _chain_identity()
        chain_id = _coerce_int(chain_identity.get("chainId") or chain_identity.get("chain_id"), field="chainId")
        head_height = _head_height()
        ttl_blocks = _coerce_int(
            os.getenv("ANIMICA_WALLET_RPC_TTL_BLOCKS", str(tx_cli.DEFAULT_TX_TTL_BLOCKS)),
            field="ttlBlocks",
            minimum=1,
        )
        max_ttl_blocks = _coerce_int(
            os.getenv("ANIMICA_MAX_TX_TTL_BLOCKS", "200"),
            field="maxTtlBlocks",
            minimum=1,
        )
        ttl_blocks = min(ttl_blocks, max_ttl_blocks)
        valid_after_lag = _coerce_int(
            os.getenv("ANIMICA_WALLET_RPC_VALID_AFTER_LAG_BLOCKS", "5"),
            field="validAfterLagBlocks",
            minimum=0,
        )
        valid_after_lag = min(valid_after_lag, max(0, max_ttl_blocks - ttl_blocks))
        valid_after = max(0, head_height - valid_after_lag)
        valid_until = head_height + ttl_blocks
        try:
            nonce = int(state_methods.state_get_next_nonce(from_address))
        except Exception:
            nonce = int(state_methods.state_get_nonce(from_address, tag="pending"))

        body = tx_cli._build_tx_body(
            chain_id=chain_id,
            from_addr=from_address,
            to_addr=to_addr,
            nonce=nonce,
            value_base_units=amount_atoms,
            gas_limit=resolved_gas_limit,
            max_fee=resolved_max_fee,
            data=b"",
            valid_after=valid_after,
            valid_until=valid_until,
            salt=os.urandom(16),
        )
        chain_ctx = tx_cli._chain_context_from_identity(
            chain_identity,
            chain_id=chain_id,
            domain=os.getenv("ANIMICA_WALLET_RPC_SIGN_DOMAIN", tx_cli.DEFAULT_DOMAIN),
            prehash=os.getenv("ANIMICA_WALLET_RPC_PREHASH", tx_cli.DEFAULT_PREHASH),
        )
        pk = tx_cli._hex_to_bytes(pk_hex)
        sk = tx_cli._hex_to_bytes(sk_hex)
        pq = tx_cli.pq_sign_tx(body, sk, pk, used_alg_id, chain_ctx)
        verify = tx_cli.pq_verify_tx(body, pq, pk, chain_ctx, from_addr=from_address)
        if not verify.ok:
            raise rpc_errors.BadSignature(
                "local wallet signature verification failed",
                reason=getattr(verify, "reason", "verify_failed"),
            )
        raw = tx_cli._build_raw_tx(
            body=body,
            alg_id=pq.alg_id,
            pk=pk,
            sig=pq.sig,
            domain=chain_ctx.domain,
            prehash=chain_ctx.prehash,
            chain_id=chain_id,
        )
        send_result = tx_methods.tx_send_raw_transaction("0x" + raw.hex())
        txid = _extract_txid(send_result)
        _record_pending(
            store,
            from_addr=from_address,
            to_addr=to_addr,
            txid=txid,
            amount_atoms=amount_atoms,
            fee_reserved_atoms=fee_reserved_atoms,
            chain_id=chain_id,
            nonce=nonce,
        )

    return {
        "txid": txid,
        "txHash": txid,
        "from": from_address,
        "to": to_addr,
        "amount": str(amount_atoms),
        "amountAtoms": str(amount_atoms),
        "feeAtoms": str(fee_atoms),
        "feeReservedAtoms": str(fee_reserved_atoms),
        "gasLimit": resolved_gas_limit,
        "maxFee": resolved_max_fee,
        "nonce": nonce,
        "chainId": chain_id,
        "validAfter": valid_after,
        "validUntil": valid_until,
    }
