"""eth_* gas/call/logs/send + the EVM<->Animica address binding methods.

Phase 1 is read + connect; the write path (eth_sendRawTransaction) is bounded by
the account/identity model: Ethereum txs are secp256k1-signed, Animica accounts
are post-quantum, so a raw EVM tx can't be admitted without an explicit binding +
a relayer that re-signs with the anim1 key. We decode the tx and return a clear
-32004 (method-not-supported) explaining the binding requirement, rather than
silently failing — honest "facade, with a path toward full EVM execution".
"""

from __future__ import annotations

import os
from typing import Any, Optional

from rpc.methods import method

from . import formatters as F
from .bridge import anim_to_evm, bind, evm_to_anim
from .errors_evm import (RPC_INVALID_PARAMS, RPC_METHOD_NOT_SUPPORTED,
                         RPC_SERVER_ERROR, RPC_TRANSACTION_REJECTED,
                         rpc_error, to_eth_error)


# ---- gas / fees (synthetic; Animica fee is account-model, not gas-market) ----
@method("eth_gasPrice", desc="EVM-compat: gas price (synthetic; Animica fee is fixed).")
def eth_gasPrice() -> str:
    return F.q(1)


@method("eth_maxPriorityFeePerGas", desc="EVM-compat: priority fee (synthetic 0).")
def eth_maxPriorityFeePerGas() -> str:
    return F.q(0)


@method("eth_estimateGas", desc="EVM-compat: gas estimate. Real EVM estimate when ANIMICA_EVM_EXECUTION=1; else synthetic.")
def eth_estimateGas(transaction: Optional[dict] = None, tag: Any = None) -> str:
    tx = transaction or {}
    try:
        from .evm_runtime import is_enabled as _evm_on, estimate_gas
        if _evm_on():
            g = estimate_gas(sender=tx.get("from"), to=tx.get("to"),
                             value_wei=F.from_q(tx.get("value"), 0),
                             data=tx.get("data") or tx.get("input") or "0x")
            if g:
                return F.q(g)
    except Exception:
        pass
    data = tx.get("data") or tx.get("input")
    if data and len(str(data)) > 2:
        return F.q(F.DEFAULT_GAS + 30000)
    return F.q(F.DEFAULT_GAS)


@method("eth_feeHistory", desc="EVM-compat: fee history (synthetic flat values).")
def eth_feeHistory(block_count: Any, newest_block: Any = "latest",
                   reward_percentiles: Optional[list] = None) -> dict:
    n = max(1, min(F.from_q(block_count, 1), 1024))
    newest = F.resolve_block_tag(newest_block) or F.head_height()
    oldest = max(0, newest - n + 1)
    base = [F.q(0)] * (n + 1)
    used = [0.0] * n
    out = {"oldestBlock": F.q(oldest), "baseFeePerGas": base, "gasUsedRatio": used}
    if reward_percentiles:
        out["reward"] = [[F.q(0)] * len(reward_percentiles) for _ in range(n)]
    return out


# ---- read-only call ---------------------------------------------------------
@method("eth_call", desc="EVM-compat: read-only call. Native-ANM ERC-20 facade always; real EVM static_call when ANIMICA_EVM_EXECUTION=1.")
def eth_call(transaction: Optional[dict] = None, tag: Any = "latest",
             state_overrides: Optional[dict] = None) -> str:
    tx = transaction or {}
    to = tx.get("to")
    data = tx.get("data") or tx.get("input") or "0x"

    # 1) Native-ANM ERC-20 facade — gate-independent, read-only, can't move funds.
    from .erc20_native import is_token, handle_call, FacadeRevert
    if is_token(to):
        try:
            return handle_call(data)
        except FacadeRevert as rv:
            raise rpc_error(3, "execution reverted", data=rv.data)

    # 2) Real EVM execution lane (read-only static call) when enabled.
    try:
        from .evm_runtime import is_enabled as _evm_on, static_call
        if _evm_on():
            frm = tx.get("from")
            out = static_call(sender=frm, to=to, value_wei=F.from_q(tx.get("value"), 0), data=data)
            return F.d(out.hex() if isinstance(out, (bytes, bytearray)) else out)
    except rpc_error.__class__ if False else Exception:
        pass
    return "0x"


# ---- logs / filters ---------------------------------------------------------
@method("eth_getLogs", desc="EVM-compat: event logs from the EVM execution lane (when enabled); else empty.")
def eth_getLogs(filter_params: Optional[dict] = None) -> list:
    try:
        from .evm_runtime import is_enabled as _evm_on, get_logs
        if _evm_on():
            fp = filter_params or {}
            # The EVM lane has its own block numbering; treat latest/pending/None
            # as unbounded (get_logs filters against the index's real numbers).
            def _bound(tag, default):
                s = str(tag).lower() if tag is not None else ""
                if s in ("", "latest", "pending", "safe", "finalized"):
                    return default
                if s == "earliest":
                    return 0
                return F.from_q(tag, default)
            frm = _bound(fp.get("fromBlock"), 0)
            to = _bound(fp.get("toBlock"), 1 << 62)
            addr = fp.get("address")
            addrs = [addr] if isinstance(addr, str) else (addr or [])
            return get_logs(int(frm), int(to), addrs, fp.get("topics"))
    except Exception:
        pass
    return []


@method("eth_newFilter", desc="EVM-compat: install a log filter (stub id).")
def eth_newFilter(filter_params: Optional[dict] = None) -> str:
    return F.q(1)


@method("eth_newBlockFilter", desc="EVM-compat: install a block filter (stub id).")
def eth_newBlockFilter() -> str:
    return F.q(2)


@method("eth_newPendingTransactionFilter", desc="EVM-compat: pending-tx filter (stub id).")
def eth_newPendingTransactionFilter() -> str:
    return F.q(3)


@method("eth_uninstallFilter", desc="EVM-compat: uninstall a filter.")
def eth_uninstallFilter(filter_id: str) -> bool:
    return True


@method("eth_getFilterChanges", desc="EVM-compat: filter changes (empty).")
def eth_getFilterChanges(filter_id: str) -> list:
    return []


@method("eth_getFilterLogs", desc="EVM-compat: filter logs (empty).")
def eth_getFilterLogs(filter_id: str) -> list:
    return []


# ---- write path (bounded by the identity model) ------------------------------
@method("eth_sendRawTransaction", desc="EVM-compat: submit a signed EVM tx. With the relayer enabled (ANIMICA_EVM_RELAYER) this moves value between EVM and native Animica; otherwise it is bounded (see -32004).")
def eth_sendRawTransaction(raw_tx: str) -> str:
    raw = str(raw_tx or "")

    # 0) EVM execution lane: a contract DEPLOY/CALL (msg.value==0) runs as real
    #    Solidity on the node-local EVM sequencer when ANIMICA_EVM_EXECUTION=1.
    #    Pure value transfers fall through to the relayer (real native ANM).
    from .evm_runtime import is_enabled as _evm_on, is_contract_tx, execute
    if _evm_on():
        parsed = None
        try:
            from .decode import decode_evm_tx, RelayerReject, evm_tx_hash
            parsed = decode_evm_tx(raw)
        except RelayerReject as exc:
            raise rpc_error(RPC_TRANSACTION_REJECTED, str(exc))
        except Exception:
            parsed = None
        if parsed is not None and is_contract_tx(parsed):
            if int(parsed.get("chainId") or 0) != F.evm_chain_id():
                raise rpc_error(RPC_TRANSACTION_REJECTED, f"wrong chainId; expected {F.evm_chain_id()}.")
            sender = parsed["sender"]
            raw_bytes = parsed["raw"]
            ehash = evm_tx_hash(raw_bytes)
            # Single nonce authority via the relayer's managed account.
            from .relayer import (is_enabled as _relayer_on, get_account, get_nonce,
                                  _reserve_nonce, _release_nonce)
            if not _relayer_on():
                raise rpc_error(RPC_METHOD_NOT_SUPPORTED,
                                "EVM execution requires the relayer (ANIMICA_EVM_RELAYER=1) for nonce/account coordination.")
            acct = get_account(sender.lower())
            if acct is None:
                raise rpc_error(RPC_TRANSACTION_REJECTED,
                                "unknown EVM account; provision it first via animica_evmAccount.")
            expected = get_nonce(sender.lower())
            n = int(parsed.get("nonce", 0))
            if n < expected:
                raise rpc_error(RPC_TRANSACTION_REJECTED, f"nonce too low ({n} < {expected}).")
            if n > expected:
                raise rpc_error(RPC_TRANSACTION_REJECTED, f"nonce too high ({n} > {expected}).")
            if not _reserve_nonce(sender.lower(), expected):
                raise rpc_error(RPC_TRANSACTION_REJECTED, "nonce race; retry.")
            try:
                execute(raw_bytes, ehash, sender, expected)
            except Exception as exc:
                _release_nonce(sender.lower(), expected)
                raise rpc_error(RPC_SERVER_ERROR, f"EVM execution failed: {exc}")
            return ehash

    # 1) Relayer path (only when ANIMICA_EVM_RELAYER is set): decode the EVM tx
    #    and relay it as a native transfer from the sender's managed account.
    from .relayer import is_enabled
    if is_enabled():
        parsed = None
        try:
            from .decode import decode_evm_tx, RelayerReject
            parsed = decode_evm_tx(raw)
        except RelayerReject as exc:
            # Decodable EVM tx that violates policy (high-s, pre-EIP-155, bad
            # yParity, out-of-range r/s): surface the real reason, don't mask it
            # as the generic -32004.
            raise rpc_error(RPC_TRANSACTION_REJECTED, str(exc))
        except Exception:
            parsed = None  # genuinely undecodable -> native passthrough below
        if parsed is not None:
            from .relayer import submit
            return submit(raw, parsed["sender"], parsed)  # rpc_error(...) propagates

    # 2) Back-compat: an Animica-format raw tx (the old eth_sendRawTransaction alias
    #    behavior) is submitted natively. Only Ethereum-RLP txs fall through.
    try:
        rid = F.native("tx.sendRawTransaction", raw if raw.startswith(("0x", "0X")) else "0x" + raw)
        if isinstance(rid, dict):
            rid = rid.get("txid") or rid.get("hash")
        if rid:
            return F.hexhash(rid)
    except Exception:
        pass  # not an Animica raw -> treat as an Ethereum tx below

    # 3) Relayer disabled (or tx undecodable): honest, bounded guidance.
    sender_alias = None
    try:
        import eth_account  # base dep
        sender_alias = eth_account.Account.recover_transaction(raw)
    except Exception:
        sender_alias = None
    bound = evm_to_anim(sender_alias) if sender_alias else None
    raise rpc_error(
        RPC_METHOD_NOT_SUPPORTED,
        "Animica is post-quantum; a secp256k1-signed EVM transaction cannot be admitted "
        "directly. Enable the custodial relayer (ANIMICA_EVM_RELAYER=1) to move value "
        "between EVM and native Animica, or use the native tx.sendRawTransaction path.",
        data={"recoveredEvmSender": sender_alias, "bound": bound, "relayer": False},
    )


@method("eth_sendTransaction", desc="EVM-compat: send from a node-managed account (none; unsupported).")
def eth_sendTransaction(transaction: Optional[dict] = None) -> str:
    raise rpc_error(RPC_METHOD_NOT_SUPPORTED,
                    "No node-managed EVM accounts. Sign client-side and use the binding flow.")


@method("eth_sign", desc="EVM-compat: eth_sign (no node-managed keys; unsupported).")
def eth_sign(address: str, data: str) -> str:
    raise rpc_error(RPC_METHOD_NOT_SUPPORTED, "No node-managed EVM keys.")


# ---- EVM <-> Animica binding (the address bridge surface) --------------------
@method("animica_evmBind", desc="Bind an Animica address to its deterministic EVM alias; returns both.")
def animica_evmBind(address: str) -> dict:
    a = str(address or "")
    if a.startswith("anim1"):
        return bind(a, explicit=True)
    if a.startswith(("0x", "0X")):
        anim = evm_to_anim(a)
        if anim:
            return {"evm_alias": a, "anim_address": anim, "bound": True}
        raise rpc_error(RPC_SERVER_ERROR, "Unknown EVM alias; bind from the anim1 side first.")
    raise rpc_error(RPC_SERVER_ERROR, "Expected an anim1... or 0x... address.")


@method("animica_evmAlias", desc="Get the deterministic EVM alias (0x) for an Animica address.")
def animica_evmAlias(address: str) -> dict:
    return {"evm_alias": anim_to_evm(address), "anim_address": str(address)}


# ---- Relayer (custodial EVM<->native value transfer) -------------------------
@method("animica_evmAccount",
        desc="Relayer (CUSTODIAL): the managed native account for an EVM address. Fund this anim1 to give the EVM address an on-chain balance.")
def animica_evmAccount(evm_address: str) -> dict:
    from .relayer import is_enabled, get_or_create_account
    if not is_enabled():
        raise rpc_error(RPC_METHOD_NOT_SUPPORTED,
                        "EVM relayer is disabled (set ANIMICA_EVM_RELAYER=1).")
    a = str(evm_address or "")
    if not a.startswith(("0x", "0X")) or len(a) != 42:
        raise rpc_error(RPC_INVALID_PARAMS, "Expected a 20-byte 0x EVM address.")
    acct = get_or_create_account(a.lower())
    return {
        "evm_address": a.lower(),
        "anim1": acct.anim1,
        "fund_native": acct.anim1,   # send ANM here; it shows under eth_getBalance(evm_address)
        "alg_id": acct.alg_id,
        "alg": "ml_dsa_65",
        "managed": True,
        "custodial": True,
    }


@method("animica_evmRelayerInfo",
        desc="Relayer status. CUSTODIAL: the operator holds all managed native keys.")
def animica_evmRelayerInfo() -> dict:
    from .relayer import info
    return info()
