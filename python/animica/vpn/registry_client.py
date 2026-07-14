"""
Client for the Animica dVPN registry (the marketplace, /api/mkt/v1/vpn/*).

Auth reuses the marketplace's ML-DSA-65 challenge flow: fetch a challenge, sign it with
the wallet, register to mint a scoped ('vpn') API key, then Bearer-auth every call. The
registry only ever sees PUBLIC keys, endpoints, regions and signed byte receipts — never
a WireGuard or wallet private key.

Stdlib-only (urllib) so the node picks up no new runtime dependency.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from .crypto import Wallet


class RegistryError(RuntimeError):
    pass


def default_base_url() -> str:
    return os.environ.get("ANIMICA_VPN_REGISTRY", "https://animica.dev/api/mkt/v1")


def default_key_cache() -> str:
    base = os.environ.get("ANIMICA_VPN_STATE", os.path.expanduser("~/.animica/vpn"))
    return os.path.join(base, "registry-key.txt")


@dataclass
class Exit:
    id: str
    label: str
    region: str
    country: str
    city: str
    wgPubkey: str
    endpoint: str            # host:port for WireGuard
    httpProxy: Optional[str] # host:port for the browser-proxy companion (optional)
    load: float
    priceHintNanmPerGb: int
    reputation: float
    online: bool

    @classmethod
    def from_json(cls, d: dict) -> "Exit":
        return cls(
            id=d["id"], label=d.get("label", d["id"]),
            region=d.get("region", "unknown"), country=d.get("country", ""),
            city=d.get("city", ""), wgPubkey=d.get("wgPubkey", ""),
            endpoint=d.get("endpoint", ""), httpProxy=d.get("httpProxy"),
            load=float(d.get("load", 0)), priceHintNanmPerGb=int(d.get("priceHintNanmPerGb", 0)),
            reputation=float(d.get("reputation", 0)), online=bool(d.get("online", True)),
        )


class RegistryClient:
    def __init__(self, wallet: Wallet, *, base_url: Optional[str] = None,
                 key_cache: Optional[str] = None, is_agent: bool = False):
        self.wallet = wallet
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self.key_cache = key_cache or default_key_cache()
        self.is_agent = is_agent
        self._api_key: Optional[str] = self._read_cached_key()

    # ------------------------------------------------------------------ transport
    def _req(self, method: str, path: str, *, body: Optional[dict] = None,
             auth: bool = True, idem: Optional[str] = None) -> Any:
        url = self.base_url + path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"content-type": "application/json", "accept": "application/json"}
        if auth:
            headers["authorization"] = f"Bearer {self._ensure_key()}"
        if idem:
            headers["idempotency-key"] = idem
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            payload = e.read().decode(errors="replace")
            if e.code == 401 and auth:
                # key expired/invalid — re-mint once
                self._api_key = None
                headers["authorization"] = f"Bearer {self._ensure_key(force=True)}"
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.loads(r.read().decode() or "{}")
            raise RegistryError(f"{method} {path} -> {e.code}: {payload}") from e
        except urllib.error.URLError as e:
            raise RegistryError(f"{method} {path} unreachable: {e}") from e

    # ------------------------------------------------------------------ auth
    def _read_cached_key(self) -> Optional[str]:
        try:
            with open(self.key_cache) as f:
                k = f.read().strip()
                return k or None
        except OSError:
            return None

    def _write_cached_key(self, key: str) -> None:
        os.makedirs(os.path.dirname(self.key_cache), mode=0o700, exist_ok=True)
        fd = os.open(self.key_cache, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key.encode())
        finally:
            os.close(fd)

    def _ensure_key(self, force: bool = False) -> str:
        if self._api_key and not force:
            return self._api_key
        # challenge -> sign -> register to mint a 'vpn'-scoped key
        ch = self._req("GET", f"/auth/challenge?address={urllib.parse.quote(self.wallet.address)}", auth=False)
        challenge = ch["challenge"]
        sig = "0x" + self.wallet.sign_login(challenge)
        reg = self._req("POST", "/accounts/register", auth=False, body={
            "address": self.wallet.address, "challenge": challenge, "signature": sig,
            "publicKey": "0x" + self.wallet.public_key_hex,
            "isAgent": self.is_agent, "scopes": ["vpn"],
        })
        key = reg.get("apiKey")
        if not key:
            raise RegistryError(f"registry did not return an API key: {reg}")
        self._api_key = key
        self._write_cached_key(key)
        return key

    # ------------------------------------------------------------------ VPN API
    def list_exits(self, *, region: Optional[str] = None, country: Optional[str] = None) -> list[Exit]:
        q = []
        if region:
            q.append(f"region={urllib.parse.quote(region)}")
        if country:
            q.append(f"country={urllib.parse.quote(country)}")
        qs = ("?" + "&".join(q)) if q else ""
        d = self._req("GET", f"/vpn/exits{qs}")
        return [Exit.from_json(x) for x in (d.get("exits") or [])]

    def register_exit(self, descriptor: dict, *, idem: Optional[str] = None) -> dict:
        return self._req("POST", "/vpn/exits", body=descriptor, idem=idem)

    def heartbeat(self, exit_id: str, stats: dict) -> dict:
        return self._req("POST", f"/vpn/exits/{urllib.parse.quote(exit_id)}/heartbeat", body=stats)

    def request_session(self, exit_id: str, client_wg_pub: str, *, idem: Optional[str] = None) -> dict:
        body = {"clientWgPub": client_wg_pub, "address": self.wallet.address}
        return self._req("POST", f"/vpn/exits/{urllib.parse.quote(exit_id)}/peer", body=body, idem=idem)

    def end_session(self, exit_id: str, session_id: str) -> dict:
        return self._req("DELETE", f"/vpn/exits/{urllib.parse.quote(exit_id)}/peer",
                         body={"sessionId": session_id})

    def post_report(self, session_id: str, report: dict) -> dict:
        return self._req("POST", f"/vpn/sessions/{urllib.parse.quote(session_id)}/report",
                         body=report, idem=f"{session_id}:{report.get('seq')}:{report.get('side')}")

    def denylist(self) -> dict:
        return self._req("GET", "/vpn/denylist")

    def me_vpn(self) -> dict:
        return self._req("GET", "/me/vpn")
