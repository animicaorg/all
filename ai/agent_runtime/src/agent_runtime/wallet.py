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


def _select_wallet_file(explicit: Optional[str]) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise WalletError(f"wallet file not found: {p}")
        return p
    # v2 multi-wallet bundle (preferred, written by `animica wallet new` today).
    bundle = _default_animica_dir() / "wallets.json"
    if bundle.is_file():
        return bundle
    wallet_dir = _default_wallet_dir()
    # Prefer the wallet pinned via animica CLI state when available.
    pin = wallet_dir / ".active"
    if pin.is_file():
        try:
            target = pin.read_text(encoding="utf-8").strip()
            tgt = (wallet_dir / target) if not Path(target).is_absolute() \
                else Path(target)
            if tgt.is_file():
                return tgt
        except OSError:
            pass
    # Fallback: first .json in wallet_dir, alphabetical.
    if wallet_dir.is_dir():
        for cand in sorted(wallet_dir.glob("*.json")):
            return cand
    raise WalletError(
        f"no wallet configured; expected {bundle} or a file under "
        f"{wallet_dir}; pass --wallet <path> to override",
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
    path = _select_wallet_file(wallet_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WalletError(f"failed to read wallet at {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise WalletError(f"wallet at {path} is not a JSON object")

    entry = _resolve_wallet_entry(raw, path, wallet_label)
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
        except Exception as exc:   # noqa: BLE001 — non-fatal here
            info.notes.append(f"balance_lookup_failed: {exc}")
    return info


def _fetch_balance(rpc_url: str, address: str) -> tuple[float, float]:
    """Read balance via the same JSON-RPC method animica wallet uses."""
    import httpx
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "animica.getBalance",
        "params": {"address": address},
    }
    r = httpx.post(rpc_url, json=body, timeout=10.0)
    r.raise_for_status()
    data = r.json()
    if "error" in data and data["error"]:
        raise WalletError(f"getBalance rpc error: {data['error']}")
    result = data.get("result") or {}
    balance = float(result.get("balance", 0.0))
    pending = float(result.get("pending", 0.0))
    return balance, pending


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
                 ) -> SignedPayment:
    """Construct + sign an AICF payment transaction.

    Delegates to ``animica.wallet`` if importable. Otherwise raises so we
    never silently produce an unsigned txn that the network would reject.

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

    try:
        signed_hex = sign_payment_tx(
            wallet_path=wallet.backing_file,
            recipient=recipient,
            amount=amount_animica,
            nonce=nonce,
            chain_id=chain_id,
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
    import httpx
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "animica.getNextNonce",
        "params": {"address": address},
    }
    r = httpx.post(rpc_url, json=body, timeout=10.0)
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        raise WalletError(f"getNextNonce rpc error: {data['error']}")
    result = data.get("result") or {}
    return int(result.get("nonce", 0))
