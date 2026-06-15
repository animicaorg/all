"""
animica.ena.walletconnect
========================

Server-side handshake for connecting the **web** wallet (wallet.animica.org)
and **mobile** wallet to ena.animica.org. Mirrors the proven flow used by the
OTC desk:

1. ``start`` builds a base64url request blob ``{payload:{app, origin, chainId,
   scopes, ts, callback, nonce}}`` and returns a popup URL
   ``https://wallet.animica.org/connect/?request=<blob>&id=<requestId>``.
2. The user approves in the wallet UI; the wallet sidecar POSTs the result to
   our ``callback`` URL.
3. The browser polls ``poll`` until the request is approved/rejected/expired.

The **browser extension** path doesn't use this — it talks to ``window.animica``
directly in the page.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
from typing import Any, Optional

from .errors import ENAError

_WALLET_CONNECT_BASE = "https://wallet.animica.org/connect/"
_TTL_SECONDS = 5 * 60
_ANIM_ADDR = re.compile(r"^anim1[02-9ac-hj-np-z]{8,}$", re.IGNORECASE)


class WalletConnectError(ENAError):
    code = "ENA/WALLETCONNECT"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class WalletConnectService:
    def __init__(self, cfg, store) -> None:
        self.cfg = cfg
        self.store = store

    def callback_url(self) -> str:
        return self.cfg.public_url.rstrip("/") + "/api/wallet/callback"

    def start(self, app_origin: Optional[str] = None) -> dict[str, Any]:
        from .models import new_uuid, now_ts
        request_id = new_uuid()
        nonce = secrets.token_hex(32)
        now = now_ts()
        payload = {
            "app": "pool.animica.org",
            "origin": app_origin or self.cfg.public_url,
            "chainId": self.cfg.chain_id,
            "scopes": ["accounts"],
            "ts": now,
            "callback": self.callback_url(),
            "nonce": nonce,
        }
        blob = _b64url(json.dumps({"payload": payload}).encode("utf-8"))
        self.store.create_wc({
            "request_id": request_id, "nonce": nonce, "status": "pending",
            "address": None, "accounts": [], "created_at": now,
            "expires_at": now + _TTL_SECONDS})
        popup_url = f"{_WALLET_CONNECT_BASE}?request={blob}&id={request_id}"
        return {"requestId": request_id, "popupUrl": popup_url,
                "expiresAt": now + _TTL_SECONDS}

    def callback(self, request_id: str, approved: bool,
                 accounts: list[str]) -> dict[str, Any]:
        row = self.store.get_wc(request_id)
        if not row:
            raise WalletConnectError("unknown requestId")
        from .models import now_ts
        if now_ts() > int(row["expires_at"]):
            self.store.set_wc_result(request_id, "expired", None, [])
            return {"ok": False, "status": "expired"}
        accounts = [a for a in (accounts or []) if isinstance(a, str)]
        if approved:
            for a in accounts:
                if not _ANIM_ADDR.match(a):
                    raise WalletConnectError(f"invalid account in payload: {a}")
        status = "approved" if approved else "rejected"
        primary = accounts[0] if (approved and accounts) else None
        self.store.set_wc_result(request_id, status, primary, accounts)
        return {"ok": True, "status": status}

    def poll(self, request_id: str) -> dict[str, Any]:
        row = self.store.get_wc(request_id)
        if not row:
            return {"status": "unknown"}
        from .models import now_ts
        if row["status"] == "pending" and now_ts() > int(row["expires_at"]):
            self.store.set_wc_result(request_id, "expired", None, [])
            return {"status": "expired"}
        out: dict[str, Any] = {"status": row["status"]}
        if row["status"] == "approved":
            out["address"] = row["address"]
            out["accounts"] = row["accounts"]
        return out
