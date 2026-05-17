"""Wallet adapter for AICF payments.

Loads a configured Animica wallet, reads balance via JSON-RPC, signs
payment transactions for AICF job submission, and surfaces pre-flight
cost previews to the chat REPL.

Design constraints (no chain regression):

- This module **does not** alter how wallets are stored, generated, or
  signed elsewhere in Animica. It reads existing wallet files under
  ``~/.animica/wallets/`` (or ``$ANIMICA_DATA_DIR``) and uses the
  pre-existing ``animica.wallet`` Python helpers when available.
- When ``animica.wallet`` is importable, we delegate to it for signing.
  When it's not, we surface a clear error and refuse to construct
  payment transactions (we never invent a signer).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from agent_runtime.errors import WalletError


# Base-unit conversion factor: 1 ANIMICA (ANM) = 10**9 nANM (base units).
# Mirrors animica.coin and the chain's getBalance return shape.
_NANM_PER_ANM = 1_000_000_000


@dataclass
class WalletInfo:
    """Public-facing summary of the configured wallet."""

    address: str
    balance_animica: float = 0.0
    pending_animica: float = 0.0
    network: str = ""
    chain_id: int = 0
    backing_file: str = ""
    scheme: str = ""              # ed25519, secp256k1, dilithium, sphincs, ...
    balance_lookup_ok: bool = False
    balance_lookup_error: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PaymentPreview:
    """Sketch of a single chat turn's expected cost."""

    estimated_cost_animica: float
    wallet_balance_animica: float
    sufficient: bool
    reason: str = ""


@dataclass
class SignedPayment:
    """Output of sign_payment(); fed to AICFClient.submit()."""

    txn_hex: str
    from_address: str
    amount_animica: float
    nonce: int
    chain_id: int
    job_metadata: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Wallet loading                                                              #
# --------------------------------------------------------------------------- #

def _default_wallet_dir() -> Path:
    override = os.environ.get("ANIMICA_DATA_DIR")
    if override:
        return Path(override).expanduser() / "wallets"
    return Path("~/.animica/wallets").expanduser()


def _default_animica_dir() -> Path:
    override = os.environ.get("ANIMICA_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path("~/.animica").expanduser()


def _select_wallet_file(explicit: Optional[str]) -> tuple[Path, Optional[str]]:
    """Resolve ``--wallet <X>`` to (bundle_path, override_label).

    ``X`` may be a file path, a label inside the default bundle, or a
    bech32 address / public-key hex of an entry inside the default bundle.
    The override_label (when non-None) tells the caller to pick that entry
    out of the bundle, regardless of the bundle's own ``default``.
    """
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p, None
        # Not a file — interpret as label / address / public_key_hex against
        # the default v2 bundle. This is what users get when they paste an
        # address from `animica wallet list` into `chat --wallet ...`.
        bundle = _default_animica_dir() / "wallets.json"
        if bundle.is_file():
            try:
                raw = json.loads(bundle.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise WalletError(
                    f"failed to read wallet bundle at {bundle}: {exc}"
                ) from exc
            wallets = raw.get("wallets") if isinstance(raw, Mapping) else None
            if isinstance(wallets, list):
                for w in wallets:
                    if not isinstance(w, Mapping):
                        continue
                    label = str(w.get("label", "") or "")
                    addr = str(w.get("address") or w.get("addr") or "")
                    pkh = str(w.get("public_key_hex") or "")
                    if explicit in {label, addr, pkh}:
                        return bundle, label or None
                labels = ", ".join(
                    str(w.get("label", "?"))
                    for w in wallets if isinstance(w, Mapping)
                )
                raise WalletError(
                    f"wallet identifier {explicit!r} is not a file and was "
                    f"not found by label/address in {bundle}",
                    hint=f"available labels: {labels or '(none)'}",
                )
        raise WalletError(f"wallet file not found: {p}")
    # v2 multi-wallet bundle (preferred, written by `animica wallet new` today).
    bundle = _default_animica_dir() / "wallets.json"
    if bundle.is_file():
        return bundle, None
    wallet_dir = _default_wallet_dir()
    # Prefer the wallet pinned via animica CLI state when available.
    pin = wallet_dir / ".active"
    if pin.is_file():
        try:
            target = pin.read_text(encoding="utf-8").strip()
            tgt = (wallet_dir / target) if not Path(target).is_absolute() \
                else Path(target)
            if tgt.is_file():
                return tgt, None
        except OSError:
            pass
    # Fallback: first .json in wallet_dir, alphabetical.
    if wallet_dir.is_dir():
        for cand in sorted(wallet_dir.glob("*.json")):
            return cand, None
    raise WalletError(
        f"no wallet configured; expected {bundle} or a file under "
        f"{wallet_dir}; pass --wallet <path|label|address> to override",
        hint="`animica wallet new` to mint one; "
             "or `animica wallet import` to bring an existing one.",
    )


def _resolve_wallet_entry(
    raw: Mapping[str, Any], path: Path, label: Optional[str]
) -> Mapping[str, Any]:
    """Pick the right wallet from a file.

    Supports two on-disk shapes:

      • flat:   ``{"address": "...", "scheme": ..., "chain_id": ...}``
        — older wallets exported by external tooling. Returned as-is.
      • v2 bundle: ``{"format": "animica.wallets", "version": 2,
                      "wallets": [{label, address, ...}, ...], "default": "..."}``
        — what `animica wallet new` writes. We pick by `label` if given,
        else the bundle's ``default`` entry, else the first wallet.

    Raises ``WalletError`` with an actionable hint if no usable entry is
    found.
    """
    # Flat layout — return as-is.
    if raw.get("address") or raw.get("addr"):
        return raw

    wallets = raw.get("wallets")
    if isinstance(wallets, list) and wallets:
        # Selector precedence: explicit label > $ANIMICA_WALLET_LABEL >
        # bundle["default"] > first entry.
        selector = label or os.environ.get("ANIMICA_WALLET_LABEL") or raw.get("default")
        chosen: Optional[Mapping[str, Any]] = None
        if selector:
            for w in wallets:
                if isinstance(w, Mapping) and str(w.get("label", "")) == str(selector):
                    chosen = w
                    break
            if chosen is None:
                labels = ", ".join(
                    str(w.get("label", "?")) for w in wallets if isinstance(w, Mapping)
                )
                raise WalletError(
                    f"wallet label {selector!r} not found in {path}",
                    hint=f"available labels: {labels}",
                )
        if chosen is None:
            chosen = wallets[0]
        if not isinstance(chosen, Mapping) or not (
            chosen.get("address") or chosen.get("addr")
        ):
            raise WalletError(
                f"selected wallet entry in {path} has no 'address' field"
            )
        return chosen

    raise WalletError(
        f"wallet at {path} has no 'address' field and no 'wallets' bundle",
        hint="wallet files must include a top-level 'address' (flat) "
             "or a 'wallets' array (v2 bundle)",
    )


def load_wallet_info(*, wallet_path: Optional[str] = None,
                     rpc_url: Optional[str] = None,
                     network: str = "",
                     wallet_label: Optional[str] = None) -> WalletInfo:
    """Read the wallet file + query balance via JSON-RPC. Never raises on
    transient RPC failure — balance stays 0.0 with a note appended.

    Accepts both the flat single-wallet schema and the v2 multi-wallet
    bundle. ``wallet_label`` picks an entry from the v2 bundle by label;
    omit it (or the ``ANIMICA_WALLET_LABEL`` env var) to use the bundle's
    ``default`` wallet, or the first entry if no default is pinned.
    """
    path, identifier_label = _select_wallet_file(wallet_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WalletError(f"failed to read wallet at {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise WalletError(f"wallet at {path} is not a JSON object")

    # Explicit --wallet-label still wins; otherwise honor the label we
    # matched from the --wallet identifier itself.
    entry = _resolve_wallet_entry(raw, path, wallet_label or identifier_label)
    address = str(entry.get("address") or entry.get("addr") or "")
    if not address:
        raise WalletError(
            f"wallet at {path} has no 'address' field",
            hint="wallet files must include the public address",
        )
    info = WalletInfo(
        address=address,
        network=network,
        chain_id=int(entry.get("chain_id", 0) or 0),
        backing_file=str(path),
        scheme=str(entry.get("scheme") or entry.get("alg_name") or ""),
    )
    if rpc_url:
        try:
            info.balance_animica, info.pending_animica = _fetch_balance(
                rpc_url, address,
            )
            info.balance_lookup_ok = True
        except Exception as exc:   # noqa: BLE001 — non-fatal here
            info.balance_lookup_error = str(exc)
            info.notes.append(f"balance_lookup_failed: {exc}")
    return info


def _parse_balance_nanm(result: Any) -> int:
    """Parse a balance RPC result into integer nANM (base units).

    Tolerates the three response shapes the chain emits across method
    variants: hex string (state.getBalance), decimal string
    (state.getAddressBalance.confirmed_balance), or raw int/float.
    """
    if isinstance(result, Mapping):
        # state.getAddressBalance returns a dict; pick the confirmed field.
        for k in ("confirmed_balance", "balance", "spendable_balance"):
            if k in result:
                return _parse_balance_nanm(result[k])
        raise WalletError(f"unexpected balance dict shape: {dict(result)!r}")
    if isinstance(result, str):
        s = result.strip()
        if not s:
            return 0
        try:
            if s.lower().startswith("0x"):
                return int(s, 16)
            return int(s)
        except ValueError as exc:
            raise WalletError(f"invalid balance string: {result!r}") from exc
    if isinstance(result, bool):    # narrower than int — refuse it
        raise WalletError(f"invalid balance bool: {result!r}")
    if isinstance(result, (int, float)):
        return int(result)
    raise WalletError(f"unexpected balance type: {type(result).__name__}")


# Order matches python/animica/cli/wallet.py::BALANCE_METHODS plus the rich
# variant used by Studio. We try each until one succeeds — different node
# builds expose different aliases and we want a single source of truth here.
_BALANCE_METHODS = (
    "state.getBalance",
    "state_getBalance",
    "chain_getBalance",
    "eth_getBalance",
    "state.getAddressBalance",
)

_NONCE_METHODS = (
    "state.getNextNonce",
    "state_getNextNonce",
    "state.getPendingNonce",
    "state.getNonce",
    "state_getNonce",
)


def _rpc_call(rpc_url: str, method: str, params: Any, *,
              timeout: float = 60.0) -> Any:
    import httpx
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = httpx.post(rpc_url, json=body, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        err = data["error"]
        msg = err.get("message") if isinstance(err, Mapping) else str(err)
        raise WalletError(f"{method} rpc error: {msg}")
    return data.get("result")


def _fetch_balance(rpc_url: str, address: str) -> tuple[float, float]:
    """Read balance via the same JSON-RPC methods animica wallet uses.

    Returns balance in ANIMICA (ANM), converted from the chain's native
    nANM (10**9 nANM per ANM). Pending is best-effort: only the rich
    method exposes it, and most node builds report 0 for non-mempool
    wallets. We don't fail the whole call if pending parsing trips.
    """
    last_exc: Optional[Exception] = None
    for method in _BALANCE_METHODS:
        # state.getBalance variants accept positional [address]; the rich
        # variant tolerates either positional or object params.
        params: Any = [address]
        try:
            result = _rpc_call(rpc_url, method, params)
        except (WalletError, Exception) as exc:    # noqa: BLE001
            last_exc = exc
            continue
        if result is None:
            last_exc = WalletError(f"{method} returned null result")
            continue
        balance_nanm = _parse_balance_nanm(result)
        pending_nanm = 0
        if isinstance(result, Mapping):
            for k in ("pending_outgoing", "pending"):
                if k in result and result[k] is not None:
                    try:
                        pending_nanm = _parse_balance_nanm(result[k])
                    except WalletError:
                        pending_nanm = 0
                    break
        return (balance_nanm / _NANM_PER_ANM,
                pending_nanm / _NANM_PER_ANM)
    assert last_exc is not None
    raise WalletError(
        f"balance lookup failed via all known methods: {last_exc}",
    ) from last_exc


# --------------------------------------------------------------------------- #
# Pre-flight                                                                  #
# --------------------------------------------------------------------------- #

def preview_payment(wallet: WalletInfo, estimated_cost: float, *,
                    min_reserve: float = 0.001) -> PaymentPreview:
    """Decide whether the wallet can afford ``estimated_cost`` plus a tiny
    reserve to cover txn fees / rounding.

    The reserve floor prevents a wallet from emptying itself on a single
    turn and getting stuck unable to pay future turns or chain txn fees.
    """
    headroom = wallet.balance_animica - estimated_cost - min_reserve
    if headroom >= 0:
        return PaymentPreview(
            estimated_cost_animica=estimated_cost,
            wallet_balance_animica=wallet.balance_animica,
            sufficient=True,
        )
    return PaymentPreview(
        estimated_cost_animica=estimated_cost,
        wallet_balance_animica=wallet.balance_animica,
        sufficient=False,
        reason=(f"insufficient balance: need {estimated_cost:.6f} ANIMICA + "
                f"{min_reserve:.6f} reserve, have {wallet.balance_animica:.6f}"),
    )


# --------------------------------------------------------------------------- #
# Signing                                                                     #
# --------------------------------------------------------------------------- #

def sign_payment(wallet: WalletInfo, *, amount_animica: float,
                 recipient: str, chain_id: int, nonce: int,
                 job_metadata: Optional[Mapping[str, Any]] = None,
                 rpc_url: Optional[str] = None,
                 ) -> SignedPayment:
    """Construct + sign an AICF payment transaction.

    Delegates to ``animica.wallet`` if importable. Otherwise raises so we
    never silently produce an unsigned txn that the network would reject.

    When ``rpc_url`` is given, fetches the chain identity (genesis hash,
    fork id, network name) from the node and passes it to the signer.
    This is required for the signature to verify on chains where genesis
    is part of the PQ signing domain — which is all production chains.

    This function deliberately does not re-implement signing; the chain's
    canonical signer lives in the existing Python wallet helpers, and
    "no chain regression" requires we reuse that signer untouched.
    """
    try:
        from animica.wallet import sign_payment_tx  # type: ignore
    except Exception as exc:
        raise WalletError(
            "animica.wallet.sign_payment_tx not importable from this venv; "
            "cannot construct AICF payment txn safely",
            hint="ensure the animica python package is installed in the "
                 "same env as agent_runtime",
            detail={"import_error": str(exc)},
        ) from exc

    chain_identity: Optional[Mapping[str, Any]] = None
    current_height: Optional[int] = None
    if rpc_url:
        try:
            ident = _rpc_call(rpc_url, "chain.getChainIdentity", [])
        except Exception:
            ident = None
        if isinstance(ident, Mapping):
            chain_identity = ident
        try:
            head = _rpc_call(rpc_url, "chain.getHead", [])
        except Exception:
            head = None
        if isinstance(head, Mapping):
            try:
                current_height = int(head.get("height") or head.get("number") or 0)
            except (TypeError, ValueError):
                current_height = None

    try:
        signed_hex = sign_payment_tx(
            wallet_path=wallet.backing_file,
            from_address=wallet.address,
            recipient=recipient,
            amount=amount_animica,
            nonce=nonce,
            chain_id=chain_id,
            chain_identity=chain_identity,
            current_height=current_height,
        )
    except Exception as exc:    # noqa: BLE001 — wrap into WalletError
        raise WalletError(
            f"signing failed: {exc}",
            hint="check wallet password / scheme compatibility",
        ) from exc
    return SignedPayment(
        txn_hex=signed_hex,
        from_address=wallet.address,
        amount_animica=amount_animica,
        nonce=nonce,
        chain_id=chain_id,
        job_metadata=dict(job_metadata or {"ts": time.time()}),
    )


def get_next_nonce(rpc_url: str, address: str) -> int:
    last_exc: Optional[Exception] = None
    for method in _NONCE_METHODS:
        try:
            result = _rpc_call(rpc_url, method, [address])
        except Exception as exc:    # noqa: BLE001
            last_exc = exc
            continue
        if result is None:
            last_exc = WalletError(f"{method} returned null result")
            continue
        # Some node builds return a dict {"nonce": N}; most return int.
        if isinstance(result, Mapping):
            if "nonce" in result:
                return int(result["nonce"])
            if "next_nonce" in result:
                return int(result["next_nonce"])
        if isinstance(result, str):
            s = result.strip()
            return int(s, 16) if s.lower().startswith("0x") else int(s)
        if isinstance(result, (int, float)):
            return int(result)
        last_exc = WalletError(
            f"{method} returned unexpected shape: {type(result).__name__}",
        )
    assert last_exc is not None
    raise WalletError(
        f"nonce lookup failed via all known methods: {last_exc}",
    ) from last_exc
