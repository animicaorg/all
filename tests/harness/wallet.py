"""
Test wallet helpers
===================

Utilities for tests to work with deterministic mnemonics, derive PQ signers
(Dilithium3 / SPHINCS+), compute/validate addresses, and fund accounts using
either a local faucet signer or the studio-services /faucet/drip endpoint.

This module is *SDK-first*: it delegates to omni_sdk.* where possible and uses
tolerant fallbacks so tests remain stable even if SDK APIs shift slightly.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import struct
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx  # tests/requirements.txt includes httpx
# SDK modules (best-effort optional pieces)
from omni_sdk import address as address_mod
from omni_sdk.wallet import mnemonic as mnemonic_mod
from omni_sdk.wallet import signer as signer_mod

# Local harness RPC client
from tests.harness.clients import HttpRpcClient
# Tx helpers (build/sign/send)
from tests.harness.tx import send_transfer

# -----------------------------------------------------------------------------
# Constants & defaults
# -----------------------------------------------------------------------------

DEFAULT_HRP = os.getenv("ADDR_HRP", "anmc")
DEFAULT_SCHEME = os.getenv("PQ_SCHEME", "dilithium3").lower()

# Deterministic HD-like root for tests (domain-separated from real wallets)
HD_DOMAIN = b"Animica-Test-HD-v1"

# Canonical test mnemonics (en-US, 24 words) — stable across test runs.
# These do not grant access to any live funds; for CI/devnets only.
TEST_MNEMONICS: Dict[str, str] = {
    "alice": "saddle produce brain gaze tomato divorce inform pottery fashion lounge pond jealous "
    "navy glide mantle prefer cricket crater turtle vibrant cozy salmon emotion awkward",
    "bob": "buddy onion unlock flee battle tuna absent grid major swallow into amazing "
    "fragile jelly scare excite sun honest tennis young draft maple drift",
    "carol": "mammal minute quiz evolve bid nest recycle lemon rough journey guess motor "
    "include pipe seek setup river tube magic ensure muffin fire timber",
}


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------


@dataclass
class TestAccount:
    name: str
    mnemonic: str
    scheme: str
    path: str
    address: str
    signer: Any  # PQ signer (Dilithium3/SPHINCS+)


# -----------------------------------------------------------------------------
# Mnemonic and seed handling
# -----------------------------------------------------------------------------


def get_test_mnemonic(name: str = "alice") -> str:
    """
    Return a stable mnemonic for a named test identity (alice, bob, carol).
    If not found, generate a new 24-word mnemonic via SDK as a fallback.
    """
    if name in TEST_MNEMONICS:
        return TEST_MNEMONICS[name]
    gen = getattr(mnemonic_mod, "generate", None)
    if callable(gen):
        # 24 words preferred for tests (256-bit entropy)
        try:
            return gen(24)  # type: ignore[arg-type]
        except TypeError:
            return gen()
    raise RuntimeError("Mnemonic generator missing in omni_sdk.wallet.mnemonic")


def _seed_from_mnemonic(mn: str, passphrase: str = "") -> bytes:
    """
    Convert mnemonic to a master seed using SDK (PBKDF2-like), falling back to BIP39-compatible derivation.
    """
    to_seed = getattr(mnemonic_mod, "to_seed", None)
    if callable(to_seed):
        try:
            return bytes(to_seed(mn, passphrase=passphrase))  # type: ignore[call-arg]
        except TypeError:
            return bytes(to_seed(mn))  # type: ignore[misc]
    # Minimal fallback (BIP39-like)
    salt = ("mnemonic" + passphrase).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha512", mn.encode("utf-8"), salt, 2048, dklen=64)


def _derive_child_seed(master: bytes, scheme: str, path: str) -> bytes:
    """
    Very small, deterministic HD-like derivation suitable for *tests only*.

    We HMAC-SHA512 chain the segments of the path "m/44'/9192'/acct'/0/index":
    key = HMAC(HD_DOMAIN || scheme, prev || index_bytes || hardened_flag)

    This is NOT meant to be a production HD wallet; it is deterministic and
    domain-separated to avoid accidental key reuse.
    """
    key = HD_DOMAIN + b"|" + scheme.encode()
    data = master
    if not path or not path.startswith("m/"):
        path = "m/44'/9192'/0'/0/0"
    parts = path.split("/")[1:]
    for p in parts:
        hardened = p.endswith("'")
        idx_str = p[:-1] if hardened else p
        idx = int(idx_str)
        payload = data + struct.pack(">I", idx) + (b"\x01" if hardened else b"\x00")
        data = hmac.new(key, payload, hashlib.sha512).digest()
    # Return 48 bytes for Dilithium3 seed space if needed; otherwise 32
    return data[:48]


# -----------------------------------------------------------------------------
# Signer factories (tolerant to SDK API names)
# -----------------------------------------------------------------------------


def _new_signer_from_seed(seed: bytes, scheme: str) -> Any:
    """
    Create a PQ signer instance from a seed for the given scheme.

    Tries a few factory names and/or explicit classes in the SDK:
    - signer_mod.new(seed=..., scheme=...)
    - signer_mod.from_seed(seed, scheme=...)
    - signer_mod.create(seed=..., algo=...)
    - signer_mod.Dilithium3Signer.from_seed(...) / SphincsPlusSigner.from_seed(...)
    """
    scheme = scheme.lower()
    # 1) Module-level factories
    for fname, kwargs in (
        ("new", {"seed": seed, "scheme": scheme}),
        ("from_seed", {"seed": seed, "scheme": scheme}),
        ("create", {"seed": seed, "algo": scheme}),
    ):
        fn = getattr(signer_mod, fname, None)
        if callable(fn):
            try:
                return fn(**kwargs)  # type: ignore[misc]
            except TypeError:
                # try positional (seed, scheme)
                try:
                    return fn(seed, scheme)  # type: ignore[misc]
                except Exception:
                    pass

    # 2) Class-specific
    cls_map = {
        "dilithium3": ("Dilithium3Signer",),
        "sphincs+": ("SphincsPlusSigner", "SphincsSha2Signer", "SphincsSigner"),
        "sphincs-sha2": ("SphincsPlusSigner", "SphincsSha2Signer", "SphincsSigner"),
    }
    for cls_name in cls_map.get(scheme, ()):
        cls = getattr(signer_mod, cls_name, None)
        if cls is None:
            continue
        for m in ("from_seed", "new", "__call__"):
            mk = getattr(cls, m, None)
            if callable(mk):
                try:
                    return mk(seed)  # type: ignore[misc]
                except Exception:
                    pass
        # try constructor
        try:
            return cls(seed)  # type: ignore[misc]
        except Exception:
            pass

    raise RuntimeError(f"Could not construct signer for scheme={scheme}")


def _address_from_pubkey(pubkey: bytes, scheme: str, hrp: str = DEFAULT_HRP) -> str:
    """
    Ask the SDK to produce an address from a pubkey+scheme; fall back to hash+bech32.
    """
    # Try canonical function names
    for fname in ("from_pubkey", "derive", "address_from_pubkey", "pubkey_to_address"):
        fn = getattr(address_mod, fname, None)
        if callable(fn):
            try:
                return fn(pubkey=pubkey, scheme=scheme, hrp=hrp)  # type: ignore[call-arg]
            except TypeError:
                try:
                    return fn(pubkey, scheme, hrp)  # type: ignore[misc]
                except Exception:
                    pass

    # Fallback: keccak(pubkey) -> last 20 bytes -> bech32 with hrp
    try:
        from omni_sdk.utils import hash as sdk_hash

        keccak = getattr(sdk_hash, "keccak256", None) or getattr(
            sdk_hash, "keccak", None
        )
        h = keccak(pubkey) if callable(keccak) else hashlib.sha3_256(pubkey).digest()
    except Exception:
        h = hashlib.sha3_256(pubkey).digest()
    raw20 = h[-20:]
    # bech32 from SDK if available
    for b32mod_name in ("bech32", "bech32m"):
        b32mod = getattr(address_mod, b32mod_name, None)
        if b32mod:
            enc = getattr(b32mod, "encode", None)
            if callable(enc):
                return enc(hrp, raw20)  # type: ignore[misc]
    # last resort: hex with 0x
    return "0x" + raw20.hex()


def make_test_account(
    name: str = "alice",
    *,
    scheme: str = DEFAULT_SCHEME,
    path: str = "m/44'/9192'/0'/0/0",
    passphrase: str = "",
    hrp: str = DEFAULT_HRP,
) -> TestAccount:
    """
    Produce a deterministic TestAccount with signer + address.
    """
    mnemonic = get_test_mnemonic(name)
    master = _seed_from_mnemonic(mnemonic, passphrase=passphrase)
    child = _derive_child_seed(master, scheme=scheme, path=path)
    signer = _new_signer_from_seed(child, scheme=scheme)

    # Get pubkey bytes
    pub = None
    for attr in ("pubkey", "public_key", "public_key_bytes"):
        val = getattr(signer, attr, None)
        if callable(val):
            pub = bytes(val())
            break
        if isinstance(val, (bytes, bytearray)):
            pub = bytes(val)
            break
    if pub is None:
        raise AttributeError("Signer missing pubkey accessor/field")

    address = _address_from_pubkey(pub, scheme, hrp=hrp)

    # Stash address back on signer if it exposes a setter or property
    if not hasattr(signer, "address"):
        try:
            setattr(signer, "address", lambda: address)  # type: ignore[attr-defined]
        except Exception:
            pass

    return TestAccount(
        name=name,
        mnemonic=mnemonic,
        scheme=scheme,
        path=path,
        address=address,
        signer=signer,
    )


# -----------------------------------------------------------------------------
# Balance, funding and faucet helpers
# -----------------------------------------------------------------------------


def get_balance(rpc: HttpRpcClient, address: str, tag: str = "latest") -> int:
    """
    Returns the account balance (int) using omni/eth getBalance.
    """
    res = rpc.call_first(["omni_getBalance", "eth_getBalance"], [address, tag])
    if isinstance(res, str) and res.startswith("0x"):
        return int(res, 16)
    return int(res)


def _try_services_faucet(
    address: str, amount: Optional[int] = None, *, timeout: float = 15.0
) -> bool:
    """
    Attempt to drip from studio-services /faucet/drip if configured via env:
      STUDIO_SERVICES_URL (or SERVICES_URL)
      STUDIO_SERVICES_API_KEY (optional, for protected endpoints)

    amount is optional and may be ignored by the server.
    """
    base = os.getenv("STUDIO_SERVICES_URL") or os.getenv("SERVICES_URL")
    if not base:
        return False
    url = base.rstrip("/") + "/faucet/drip"
    headers = {}
    api_key = os.getenv("STUDIO_SERVICES_API_KEY") or os.getenv("SERVICES_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"address": address}
    if amount is not None:
        payload["amount"] = str(amount)

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=payload, headers=headers)
            if r.status_code == 200:
                return True
            # Some servers return 202 for accepted/asynchronous
            if r.status_code in (201, 202):
                return True
            return False
    except Exception:
        return False


def _try_local_faucet_transfer(
    rpc: HttpRpcClient,
    target: str,
    *,
    amount: int,
    scheme: str = DEFAULT_SCHEME,
) -> bool:
    """
    Use a local faucet signer (from env or fixtures) to transfer funds.
    Env options:
      FAUCET_MNEMONIC / FAUCET_SCHEME / FAUCET_PATH
    Fallback fixtures:
      sdk/test-harness/fixtures/accounts.json (first entry treated as faucet)
    """
    faucet_mn = os.getenv("FAUCET_MNEMONIC")
    faucet_scheme = (os.getenv("FAUCET_SCHEME") or scheme).lower()
    faucet_path = os.getenv("FAUCET_PATH", "m/44'/9192'/9'/0/0")

    if not faucet_mn:
        # Try fixtures
        for p in (
            "sdk/test-harness/fixtures/accounts.json",
            "tests/fixtures/accounts.json",
            "fixtures/accounts.json",
        ):
            if os.path.exists(p):
                try:
                    data = json.loads(open(p, "r", encoding="utf-8").read())
                    # Accept either {name: mnemonic} or list of dicts
                    if isinstance(data, dict):
                        # pick the first item deterministically
                        faucet_mn = next(iter(data.values()))
                    elif isinstance(data, list) and data:
                        entry = data[0]
                        faucet_mn = (
                            entry.get("mnemonic")
                            or entry.get("seed")
                            or entry.get("phrase")
                        )
                        faucet_scheme = (entry.get("scheme") or faucet_scheme).lower()
                        faucet_path = entry.get("path") or faucet_path
                except Exception:
                    pass
            if faucet_mn:
                break

    if not faucet_mn:
        return False

    faucet = make_test_account("faucet", scheme=faucet_scheme, path=faucet_path)
    try:
        send_transfer(
            rpc,
            faucet.signer,
            target,
            amount,
            await_receipt=True,
            timeout=60.0,
        )
        return True
    except Exception:
        return False


def ensure_funded(
    rpc: HttpRpcClient,
    address: str,
    *,
    min_balance: int = 1_000_000_000_000_000,  # 0.001 units if 18 decimals
    top_up_amount: Optional[int] = None,
    scheme: str = DEFAULT_SCHEME,
    poll_secs: float = 0.5,
    max_wait: float = 20.0,
) -> int:
    """
    Ensure `address` has at least `min_balance`. If not, try services faucet,
    then a local faucet transfer. Returns the final observed balance (int).
    """
    bal = get_balance(rpc, address)
    if bal >= min_balance:
        return bal

    want = top_up_amount or max(min_balance - bal, min_balance)

    # Try services faucet first
    if _try_services_faucet(address, want):
        # wait for balance
        waited = 0.0
        while waited < max_wait:
            time.sleep(poll_secs)
            bal = get_balance(rpc, address)
            if bal >= min_balance:
                return bal
            waited += poll_secs

    # Try local faucet (devnet/fixtures)
    if _try_local_faucet_transfer(rpc, address, amount=want, scheme=scheme):
        waited = 0.0
        while waited < max_wait:
            time.sleep(poll_secs)
            bal = get_balance(rpc, address)
            if bal >= min_balance:
                return bal
            waited += poll_secs

    # One last read before giving up
    return get_balance(rpc, address)


# -----------------------------------------------------------------------------
# Convenience: create + (optionally) fund a named test account
# -----------------------------------------------------------------------------


def make_and_maybe_fund(
    rpc: HttpRpcClient,
    name: str = "alice",
    *,
    scheme: str = DEFAULT_SCHEME,
    path: str = "m/44'/9192'/0'/0/0",
    min_balance: Optional[int] = 1_000_000_000_000_000,
    hrp: str = DEFAULT_HRP,
) -> TestAccount:
    """
    Construct a TestAccount and, if min_balance is set, attempt to fund it.
    """
    acct = make_test_account(name, scheme=scheme, path=path, hrp=hrp)
    if min_balance is not None:
        ensure_funded(rpc, acct.address, min_balance=min_balance, scheme=scheme)
    return acct
