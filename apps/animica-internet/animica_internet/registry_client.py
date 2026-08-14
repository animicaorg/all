"""
Marketplace / ANS registry client for the desktop app.

Read paths (search, resolve, content) are public + CORS-open. Write paths (register, publish,
reserve, renew) authenticate with an ml_dsa_65 wallet login (session cookie) OR, for name
RESERVATION, an on-chain payment to the Foundation address proven by txid — the payment is the
auth, so no login is needed to reserve.

stdlib only (urllib + cookiejar) so it works in a frozen app and in tests.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Optional

from .config import API_BASE


class RegistryError(RuntimeError):
    pass


class RegistryClient:
    def __init__(self, base: Optional[str] = None):
        self.base = (base or API_BASE).rstrip("/")
        self._jar = CookieJar()
        from .netcfg import ca_context
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ca_context()),
            urllib.request.HTTPCookieProcessor(self._jar),
        )

    def _req(self, method: str, path: str, *, body: Optional[dict] = None,
             auth_key: Optional[str] = None, timeout: int = 15):
        url = self.base + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"accept": "application/json"}
        if data is not None:
            headers["content-type"] = "application/json"
        if auth_key:
            headers["authorization"] = f"Bearer {auth_key}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=timeout) as r:
                raw = r.read().decode() or "{}"
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            payload = e.read().decode(errors="replace")
            try:
                msg = json.loads(payload).get("error", {}).get("message") or payload
            except Exception:  # noqa: BLE001
                msg = payload
            raise RegistryError(f"{method} {path} -> {e.code}: {msg}") from e
        except urllib.error.URLError as e:
            raise RegistryError(f"{method} {path} unreachable: {e.reason}") from e

    # ------------------------------------------------------------------ public reads
    def search(self, query: str = "", kind: Optional[str] = None) -> list[dict]:
        q = []
        if query:
            q.append("search=" + urllib.parse.quote(query))
        if kind:
            q.append("kind=" + urllib.parse.quote(kind))
        qs = ("?" + "&".join(q)) if q else ""
        d = self._req("GET", f"/names{qs}")
        return d.get("results") or []

    def resolve(self, name: str) -> dict:
        return self._req("GET", f"/names/{urllib.parse.quote(name)}")

    # ------------------------------------------------------------------ wallet login
    def login(self, wallet, *, address: Optional[str] = None) -> None:
        """Establish a full-rights (scopes ['*']) session cookie via the ml_dsa_65 challenge
        flow, so publish/register/renew work. `wallet` is animica_internet.wallet.Wallet."""
        addr = address or wallet.primary_address()
        ch = self._req("GET", f"/auth/challenge?address={urllib.parse.quote(addr)}")
        challenge = ch.get("challenge")
        if not challenge:
            raise RegistryError("registry returned no challenge")
        sig_hex, pub_hex = wallet.sign_login(challenge, address=addr)
        self._req("POST", "/auth/verify", body={
            "address": addr, "challenge": challenge,
            "signature": sig_hex, "publicKey": pub_hex,
        })

    # ------------------------------------------------------------------ authed writes
    def register(self, name: str, *, years: int = 1, kind: str = "app") -> dict:
        return self._req("POST", "/names", body={"name": name, "years": years, "kind": kind})

    def publish(self, name: str, html: str) -> dict:
        return self._req("POST", f"/names/{urllib.parse.quote(name)}/publish", body={"html": html})

    def renew(self, name: str, years: int = 1) -> dict:
        return self._req("POST", f"/names/{urllib.parse.quote(name)}/renew", body={"years": years})

    def mine(self) -> list[dict]:
        d = self._req("GET", "/names/mine")
        return d.get("domains") or d.get("results") or []

    # ------------------------------------------------------------------ balance / funding
    def deposit_address(self, purpose: str = "names") -> str:
        """The buyer's personal on-chain deposit address; funding it credits the marketplace
        balance that name-reservation fees are drawn from (12-conf finality)."""
        d = self._req("POST", "/deposits/address", body={"purpose": purpose})
        return d.get("address") or (d.get("deposit") or {}).get("address") or ""

    def balance_nanm(self) -> int:
        d = self._req("GET", "/balance")
        raw = d.get("balance") or d.get("confirmed") or d.get("nanm") or 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0
