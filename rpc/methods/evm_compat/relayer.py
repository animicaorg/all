"""Custodial EVM <-> native relayer for Animica.

Lets a MetaMask/ethers user (secp256k1, 0x address) actually MOVE value to/from
native Animica (post-quantum, anim1). Animica accounts are post-quantum, so a
secp256k1 signature cannot directly control an anim1 account. The relayer bridges
that gap with a CUSTODIAL managed-account model:

  * Each EVM address E (recovered via ecrecover) maps to a relayer-controlled
    native account A(E): a fresh ML-DSA-65 keypair generated lazily and persisted.
  * The EVM signature proves intent; the relayer enforces it by signing the
    native tx FROM A(E) with A(E)'s post-quantum key.
  * Fund native->EVM: send ANM to A(E)'s anim1 (animica_evmAccount(E) publishes
    it); the balance shows under eth_getBalance(E).
  * Spend EVM->EVM or EVM->native: sign an EVM tx {from E, to, value, nonce,
    chainId=149}; the relayer recovers E, resolves the recipient, and submits a
    native transfer A(E) -> recipient of `value` nANM.

CUSTODIAL BY CONSTRUCTION: the operator holds every A(E) secret key. This is NOT
trustless and NOT secp256k1<->PQ key-equivalence. It is OFF unless
ANIMICA_EVM_RELAYER is set.

Security (S1..S10 in docs/EVM_RPC_COMPAT.md). Highlights — the debited account is
the ecrecover'd sender ONLY (S1); chainId must equal 149 (EIP-155 replay, S2b/S4);
an ATOMIC SQL nonce reservation (`UPDATE ... WHERE evm_nonce=?`) + keccak
idempotency stop same-chain / multi-process replay (S2a/S3); recipient is never
burned (S5); value is nANM with an inclusive sanity cap + a value+fee balance
check (S5-units/S6); secret keys are AES-256-GCM encrypted at rest with a KEK and
zeroized after use (S7); the spend path never generates a keypair before funds are
proven and total managed accounts are capped (S9 DoS).
"""

from __future__ import annotations

import base64
import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

from . import formatters as F
from .bridge import evm_to_anim
from .errors_evm import (RPC_INTERNAL_ERROR, RPC_INVALID_PARAMS,
                         RPC_METHOD_NOT_SUPPORTED, RPC_SERVER_ERROR,
                         RPC_TRANSACTION_REJECTED, rpc_error)

log = logging.getLogger(__name__)

ML_DSA_65_ID = 0x1003
_GAS_LIMIT = 21_000
# Sanity cap (nANM): reject absurd values that usually mean a units error (e.g. a
# wallet that assumed 18 decimals and sent 1e18 for "1 ANM"). The check is
# INCLUSIVE (value >= cap) so the canonical 1e18 mistake is itself rejected.
_DEFAULT_MAX_NANM = 10 ** 18  # = 1e9 ANM
_DEFAULT_MAX_ACCOUNTS = 100_000

_LOCK = threading.RLock()          # in-process critical-section lock
_CONN: Optional[sqlite3.Connection] = None
_KEK: Optional[bytes] = None
_KEK_RESOLVED = False


# --------------------------------------------------------------------------- #
# config / feature gate
# --------------------------------------------------------------------------- #
def is_enabled() -> bool:
    v = os.environ.get("ANIMICA_EVM_RELAYER", "")
    return str(v).strip().lower() not in ("", "0", "false", "no", "off")


def _value_scale() -> int:
    """Divisor applied to the EVM `value` to obtain nANM. Default 1 (nANM 1:1).

    Operators whose wallets assume 18-decimal native currency (e.g. some MetaMask
    flows) set ANIMICA_EVM_RELAYER_VALUE_SCALE=1000000000 so 1e18 wei -> 1e9 nANM.
    """
    try:
        return max(1, int(os.environ.get("ANIMICA_EVM_RELAYER_VALUE_SCALE", "1")))
    except ValueError:
        return 1


def _max_nanm() -> int:
    try:
        return int(os.environ.get("ANIMICA_EVM_RELAYER_MAX_NANM", str(_DEFAULT_MAX_NANM)))
    except ValueError:
        return _DEFAULT_MAX_NANM


def _max_accounts() -> int:
    try:
        return int(os.environ.get("ANIMICA_EVM_RELAYER_MAX_ACCOUNTS", str(_DEFAULT_MAX_ACCOUNTS)))
    except ValueError:
        return _DEFAULT_MAX_ACCOUNTS


def _evm_chain_id() -> int:
    return F.evm_chain_id()  # 149


# --------------------------------------------------------------------------- #
# at-rest key handling (S7)
# --------------------------------------------------------------------------- #
def _resolve_kek() -> Optional[bytes]:
    global _KEK, _KEK_RESOLVED
    if _KEK_RESOLVED:
        return _KEK
    raw = os.environ.get("ANIMICA_EVM_RELAYER_KEK", "").strip()
    key: Optional[bytes] = None
    if raw:
        for dec in (lambda s: bytes.fromhex(s[2:] if s.startswith(("0x", "0X")) else s),
                    base64.b64decode):
            try:
                k = dec(raw)
                if len(k) == 32:
                    key = k
                    break
            except Exception:
                continue
        if key is None:
            raise rpc_error(RPC_INTERNAL_ERROR,
                            "ANIMICA_EVM_RELAYER_KEK must decode to 32 bytes (hex or base64).")
    _KEK = key
    _KEK_RESOLVED = True
    return _KEK


def at_rest_mode() -> str:
    if _resolve_kek() is not None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
            return "aes-256-gcm"
        except Exception:
            return "chacha20-poly1305"
    return "plaintext"


def _allow_plaintext() -> bool:
    return str(os.environ.get("ANIMICA_EVM_RELAYER_ALLOW_PLAINTEXT", "")).strip().lower() in (
        "1", "true", "yes", "on")


def _aead():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
    return AESGCM, ChaCha20Poly1305


def _encrypt_sk(sk: bytes, evm: str) -> Tuple[bytes, bytes]:
    """Return (ciphertext, nonce). Plaintext only if explicitly allowed."""
    kek = _resolve_kek()
    if kek is None:
        if not _allow_plaintext():
            raise rpc_error(
                RPC_INTERNAL_ERROR,
                "EVM relayer refuses to store native keys in plaintext. Set "
                "ANIMICA_EVM_RELAYER_KEK (32-byte hex/base64), or explicitly opt in "
                "with ANIMICA_EVM_RELAYER_ALLOW_PLAINTEXT=1.")
        log.warning("EVM relayer storing native secret keys in PLAINTEXT (no KEK set).")
        return bytes(sk), b""
    AESGCM, ChaCha20Poly1305 = _aead()
    nonce = os.urandom(12)
    try:
        ct = AESGCM(kek).encrypt(nonce, bytes(sk), evm.encode())  # AAD binds ct to its account
    except Exception:
        ct = ChaCha20Poly1305(kek).encrypt(nonce, bytes(sk), evm.encode())
    return ct, nonce


def _decrypt_sk(ct: bytes, nonce: bytes, evm: str) -> bytearray:
    """Decrypt into a MUTABLE bytearray so the plaintext key can be zeroized."""
    kek = _resolve_kek()
    if kek is None or not nonce:
        return bytearray(ct)  # plaintext path (only if it was stored that way)
    AESGCM, ChaCha20Poly1305 = _aead()
    try:
        return bytearray(AESGCM(kek).decrypt(nonce, ct, evm.encode()))
    except Exception:
        return bytearray(ChaCha20Poly1305(kek).decrypt(nonce, ct, evm.encode()))


def _zeroize(b: Any) -> None:
    """Wipe a mutable bytearray IN PLACE (immutable bytes cannot be zeroized)."""
    if isinstance(b, bytearray):
        for i in range(len(b)):
            b[i] = 0


# --------------------------------------------------------------------------- #
# keystore (S7 perms; S9 cap/indices)
# --------------------------------------------------------------------------- #
def _db_path() -> Path:
    base = os.environ.get("ANIMICA_EVM_DIR") or os.environ.get("AICF_DB_DIR")
    root = Path(base) if base else (Path.home() / ".animica" / "evm")
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except Exception:
        pass
    return root / "relayer.sqlite3"


def _conn() -> sqlite3.Connection:
    global _CONN
    with _LOCK:
        if _CONN is None:
            p = _db_path()
            _CONN = sqlite3.connect(str(p), check_same_thread=False, timeout=10)
            _CONN.execute("PRAGMA journal_mode=WAL")
            _CONN.execute("PRAGMA busy_timeout=5000")
            _CONN.execute(
                "CREATE TABLE IF NOT EXISTS managed_accounts ("
                " evm TEXT PRIMARY KEY, anim1 TEXT NOT NULL, alg_id INTEGER NOT NULL,"
                " pubkey BLOB NOT NULL, sk_enc BLOB NOT NULL, sk_nonce BLOB NOT NULL,"
                " evm_nonce INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL)")
            _CONN.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_managed_anim1 ON managed_accounts(anim1)")
            _CONN.execute(
                "CREATE TABLE IF NOT EXISTS evm_tx_map ("
                " evm_hash TEXT PRIMARY KEY, native_txid TEXT NOT NULL, evm_from TEXT NOT NULL,"
                " evm_to TEXT, anim_to TEXT NOT NULL, value INTEGER NOT NULL,"
                " evm_nonce INTEGER NOT NULL, chain_id INTEGER NOT NULL, created_at INTEGER NOT NULL,"
                " status TEXT NOT NULL DEFAULT 'submitted',"
                " UNIQUE(evm_from, evm_nonce))")
            _CONN.execute(
                "CREATE INDEX IF NOT EXISTS idx_evm_tx_native ON evm_tx_map(native_txid)")
            _CONN.execute(
                "CREATE INDEX IF NOT EXISTS idx_evm_tx_from ON evm_tx_map(evm_from)")
            _CONN.commit()
            for sidecar in (p, Path(str(p) + "-wal"), Path(str(p) + "-shm")):
                try:
                    if sidecar.exists():
                        os.chmod(sidecar, 0o600)
                except Exception:
                    pass
        return _CONN


@dataclass(frozen=True)
class ManagedAccount:
    evm: str          # 0x… lowercased (recovered EVM sender; primary key)
    anim1: str        # A(E) address
    alg_id: int       # 0x1003
    pubkey: bytes
    # NOTE: deliberately NO secret_key — never crosses an RPC boundary.


def get_account(evm: str) -> Optional[ManagedAccount]:
    e = str(evm).lower()
    with _LOCK:
        row = _conn().execute(
            "SELECT evm, anim1, alg_id, pubkey FROM managed_accounts WHERE evm=?", (e,)
        ).fetchone()
    if not row:
        return None
    return ManagedAccount(evm=row[0], anim1=row[1], alg_id=int(row[2]), pubkey=bytes(row[3]))


def get_or_create_account(evm: str) -> ManagedAccount:
    e = str(evm).lower()
    existing = get_account(e)
    if existing:
        return existing
    with _LOCK:
        # Re-check under the lock (race), then enforce the S9 cap BEFORE the heavy
        # ML-DSA-65 keygen so a flood of fresh addresses can't exhaust CPU/disk.
        existing = get_account(e)
        if existing:
            return existing
        cnt = _conn().execute("SELECT COUNT(*) FROM managed_accounts").fetchone()[0]
        if int(cnt) >= _max_accounts():
            raise rpc_error(RPC_SERVER_ERROR,
                            f"relayer managed-account cap reached ({_max_accounts()}); "
                            f"raise ANIMICA_EVM_RELAYER_MAX_ACCOUNTS.")
        from pq.py.keygen import keygen_sig
        kp = keygen_sig(ML_DSA_65_ID)
        sk_enc, sk_nonce = _encrypt_sk(bytes(kp.secret_key), e)
        c = _conn()
        c.execute(
            "INSERT OR IGNORE INTO managed_accounts"
            "(evm, anim1, alg_id, pubkey, sk_enc, sk_nonce, evm_nonce, created_at)"
            " VALUES(?,?,?,?,?,?,0,?)",
            (e, kp.address, int(kp.alg_id), bytes(kp.public_key), sk_enc, sk_nonce, int(time.time())))
        c.commit()
    acct = get_account(e)
    if acct is None:  # pragma: no cover
        raise rpc_error(RPC_INTERNAL_ERROR, "failed to persist relayer account")
    return acct


def _load_secret(evm: str) -> Tuple[bytearray, bytes, int]:
    """(secret_key bytearray, public_key, alg_id) — in-process only; never via RPC."""
    e = str(evm).lower()
    with _LOCK:
        row = _conn().execute(
            "SELECT sk_enc, sk_nonce, pubkey, alg_id FROM managed_accounts WHERE evm=?", (e,)
        ).fetchone()
    if not row:
        raise rpc_error(RPC_INTERNAL_ERROR, "relayer account missing")
    sk = _decrypt_sk(bytes(row[0]), bytes(row[1]), e)
    return sk, bytes(row[2]), int(row[3])


# --------------------------------------------------------------------------- #
# nonce — atomic, cross-process-safe reservation (S3)
# --------------------------------------------------------------------------- #
def get_nonce(evm: str) -> int:
    e = str(evm).lower()
    with _LOCK:
        row = _conn().execute(
            "SELECT evm_nonce FROM managed_accounts WHERE evm=?", (e,)).fetchone()
    return int(row[0]) if row else 0


def _reserve_nonce(evm: str, expected: int) -> bool:
    """Atomically bump evm_nonce expected->expected+1. SQLite's write lock makes
    this safe across threads AND processes (multi-worker). True iff we won."""
    e = str(evm).lower()
    with _LOCK:
        c = _conn()
        cur = c.execute(
            "UPDATE managed_accounts SET evm_nonce=evm_nonce+1 WHERE evm=? AND evm_nonce=?",
            (e, int(expected)))
        c.commit()
        return cur.rowcount == 1


def _release_nonce(evm: str, expected: int) -> None:
    """Roll back a reservation if the native submit failed (best-effort)."""
    e = str(evm).lower()
    with _LOCK:
        c = _conn()
        c.execute(
            "UPDATE managed_accounts SET evm_nonce=evm_nonce-1 WHERE evm=? AND evm_nonce=?",
            (e, int(expected) + 1))
        c.commit()


# --------------------------------------------------------------------------- #
# recipient resolution (S5)
# --------------------------------------------------------------------------- #
def resolve_recipient(to_evm: Optional[str]) -> Tuple[str, str]:
    """0x recipient -> (anim1, kind). 'bound' = a real anim1 explicitly bound to
    this alias (EVM->native); 'managed' = the recipient's own managed account
    (EVM->EVM). Never returns the zero address; `to is None` is rejected upstream.

    Only called from submit() AFTER the sender's balance is proven, so any
    keypair created for a fresh recipient is gated behind a funded, fee-paying
    spend (and the global account cap)."""
    to = str(to_evm or "").lower()
    bound = evm_to_anim(to)
    if bound:
        return bound, "bound"
    return get_or_create_account(to).anim1, "managed"


# --------------------------------------------------------------------------- #
# receipt tracking (S3)
# --------------------------------------------------------------------------- #
def record_submission(*, evm_hash: str, native_txid: str, evm: str, evm_to: Optional[str],
                      anim_to: str, value: int, evm_nonce: int, chain_id: int,
                      status: str = "submitted") -> None:
    with _LOCK:
        c = _conn()
        c.execute(
            "INSERT OR IGNORE INTO evm_tx_map"
            "(evm_hash, native_txid, evm_from, evm_to, anim_to, value, evm_nonce, chain_id, created_at, status)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (evm_hash, native_txid, str(evm).lower(), (evm_to or None), anim_to,
             int(value), int(evm_nonce), int(chain_id), int(time.time()), status))
        c.commit()


def lookup_by_evm_hash(evm_hash: str) -> Optional[dict]:
    h = str(evm_hash or "").lower()
    with _LOCK:
        row = _conn().execute(
            "SELECT evm_hash, native_txid, evm_from, evm_to, anim_to, value, evm_nonce, chain_id, status"
            " FROM evm_tx_map WHERE evm_hash=?", (h,)).fetchone()
    if not row:
        return None
    keys = ("evm_hash", "native_txid", "evm_from", "evm_to", "anim_to", "value",
            "evm_nonce", "chain_id", "status")
    return dict(zip(keys, row))


# --------------------------------------------------------------------------- #
# admission error mapping (S6) — EXACT reason-code matching (no substrings)
# --------------------------------------------------------------------------- #
_BUILD_BUG_REASONS = {"invalid_signature", "invalid_format", "chain_id_mismatch",
                      "bad_signature", "malformed", "deserialization", "policy_reject"}
_ALREADY_KNOWN = {"tx_already_known", "already_known"}
_FUNDS_REASONS = {"insufficient_funds", "insufficient_balance", "insufficient"}


def _reason_of(exc: Exception) -> str:
    data = getattr(exc, "data", None)
    if isinstance(data, dict):
        me = data.get("mempoolError") or data.get("reject") or data
        if isinstance(me, dict):
            r = me.get("reason_code") or me.get("reason") or me.get("code") or me.get("error")
            if r:
                return str(r).lower()
        for k in ("reason_code", "reason", "code"):
            if data.get(k):
                return str(data[k]).lower()
    return str(getattr(exc, "message", exc) or "").lower()


def _native_txid_from_exc(exc: Exception) -> Optional[str]:
    data = getattr(exc, "data", None)
    if isinstance(data, dict):
        me = data.get("mempoolError") or {}
        ctx = (me.get("context") if isinstance(me, dict) else None) or {}
        h = ctx.get("tx_hash") or (me.get("tx_hash") if isinstance(me, dict) else None)
        if h:
            return F.hexhash(h)
    return None


def _map_admission_error(exc: Exception, acct: "ManagedAccount"):
    r = _reason_of(exc)
    if r in _ALREADY_KNOWN:
        return None  # caller treats None as "already known => idempotent success"
    if r in _FUNDS_REASONS or "insufficient" in r:
        return rpc_error(RPC_TRANSACTION_REJECTED,
                         f"insufficient funds in managed account; fund {acct.anim1} "
                         f"(see animica_evmAccount).", data={"fund": acct.anim1})
    if r in _BUILD_BUG_REASONS:
        # The relayer built/signed a bad native tx — never the user's fault.
        return rpc_error(RPC_SERVER_ERROR, f"relayer build error: {r}")
    return rpc_error(RPC_TRANSACTION_REJECTED,
                     f"native admission rejected: {r}" if r else "native admission rejected")


# --------------------------------------------------------------------------- #
# core submit (S1..S9)
# --------------------------------------------------------------------------- #
def submit(raw_tx: str, evm_sender: str, parsed: dict) -> str:
    """Relay a decoded EVM tx as a native transfer. Returns the EVM tx hash."""
    if not is_enabled():
        raise rpc_error(RPC_METHOD_NOT_SUPPORTED, "EVM relayer disabled.")

    # S1: the debited account is the ecrecover'd sender ONLY — derived from the
    # decoded tx, never a client-supplied field. (evm_sender must agree.)
    evm = str(parsed.get("sender") or "").lower()
    if not evm or evm != str(evm_sender or "").lower():
        raise rpc_error(RPC_SERVER_ERROR, "sender mismatch")

    # S2b/S4: EIP-155 chainId must equal the EVM facade id exactly.
    cid_evm = parsed.get("chainId")
    if cid_evm is None:
        raise rpc_error(RPC_TRANSACTION_REJECTED, "pre-EIP-155 transaction (no chainId) rejected.")
    if int(cid_evm) != _evm_chain_id():
        raise rpc_error(RPC_TRANSACTION_REJECTED,
                        f"wrong chainId {int(cid_evm)}; expected {_evm_chain_id()}.")

    # value-transfer only (no contract create / calldata).
    if parsed.get("to") is None:
        raise rpc_error(RPC_TRANSACTION_REJECTED, "contract creation is unsupported.")
    if str(parsed.get("data") or "0x").lower() not in ("0x", ""):
        raise rpc_error(RPC_TRANSACTION_REJECTED, "calldata unsupported (value-transfer only).")

    # S5-units: EVM value -> nANM (scale, default 1:1) + INCLUSIVE sanity cap.
    raw_value = int(parsed.get("value", 0))
    scale = _value_scale()
    if scale != 1 and raw_value % scale != 0:
        raise rpc_error(RPC_TRANSACTION_REJECTED,
                        f"value not divisible by scale {scale} (sub-nANM dust).")
    value = raw_value // scale
    if value <= 0:
        raise rpc_error(RPC_TRANSACTION_REJECTED, "value must be > 0.")
    if value >= _max_nanm():
        raise rpc_error(RPC_TRANSACTION_REJECTED,
                        f"value {value} nANM exceeds the relayer cap {_max_nanm()} "
                        f"(likely a decimals/units error — set ANIMICA_EVM_RELAYER_VALUE_SCALE "
                        f"or ANIMICA_EVM_RELAYER_MAX_NANM).")

    raw_bytes = parsed.get("raw") or _raw_bytes(raw_tx)
    evm_hash = "0x" + F.keccak256(raw_bytes).hex()

    with _LOCK:  # in-process serialization; the SQL nonce CAS handles cross-process
        # S2a: idempotency — a re-broadcast of the SAME signed tx returns the same hash.
        prior = lookup_by_evm_hash(evm_hash)
        if prior:
            return evm_hash

        # S9: the SENDER must be a pre-provisioned managed account. The spend path
        # NEVER generates a keypair for an unknown sender, so an unfunded flood of
        # fresh senders does zero ML-DSA keygen.
        acct = get_account(evm)
        if acct is None:
            raise rpc_error(RPC_TRANSACTION_REJECTED,
                            "unknown EVM account; provision and fund it first via animica_evmAccount.")

        # S3: strict per-E nonce (no gaps, no replays). Exact-match the expected.
        expected = get_nonce(evm)
        n = int(parsed.get("nonce", 0))
        if n < expected:
            raise rpc_error(RPC_TRANSACTION_REJECTED, f"nonce too low ({n} < {expected}).")
        if n > expected:
            raise rpc_error(RPC_TRANSACTION_REJECTED, f"nonce too high ({n} > {expected}); no gaps.")

        # S6: A(E) must cover value + the node's fee = value + gasLimit*gasPrice.
        # Checked BEFORE any recipient keypair is created.
        gas_price = _default_gas_price()
        required = value + _GAS_LIMIT * gas_price
        bal = F.from_q(F.native("state.getBalance", acct.anim1), 0)
        if bal < required:
            raise rpc_error(RPC_TRANSACTION_REJECTED,
                            f"insufficient funds: {acct.anim1} has {bal} nANM < value+fee "
                            f"{required}; fund it (animica_evmAccount).", data={"fund": acct.anim1})

        # Now resolve the recipient (may lazily create a managed account, but only
        # for this funded, fee-paying spend) and build+sign the native transfer.
        anim_to, _kind = resolve_recipient(parsed["to"])
        sk, pk, alg_id = _load_secret(evm)
        try:
            raw_hex = _build_native_transfer(acct.anim1, anim_to, value, gas_price, sk, pk, alg_id)
        finally:
            _zeroize(sk)  # wipe the decrypted key as soon as the tx is signed

        # S3: reserve the nonce ATOMICALLY (cross-process) immediately before
        # broadcast. Two workers racing the same nonce: exactly one wins the CAS.
        if not _reserve_nonce(evm, expected):
            raise rpc_error(RPC_TRANSACTION_REJECTED, "nonce race; retry.")
        try:
            native_txid = F.native("tx.sendRawTransaction", raw_hex)
        except Exception as exc:  # noqa: BLE001
            mapped = _map_admission_error(exc, acct)
            if mapped is None:  # already known on-chain -> idempotent success
                ntid = _native_txid_from_exc(exc) or evm_hash
                record_submission(evm_hash=evm_hash, native_txid=ntid, evm=evm,
                                  evm_to=parsed.get("to"), anim_to=anim_to, value=value,
                                  evm_nonce=n, chain_id=_evm_chain_id(), status="already_known")
                return evm_hash  # keep the reservation (the native tx exists)
            _release_nonce(evm, expected)  # roll back; nothing was broadcast
            raise mapped

        if isinstance(native_txid, dict):
            native_txid = (native_txid.get("tx_hash") or native_txid.get("hash")
                           or native_txid.get("txHash"))
        native_txid = F.hexhash(native_txid) or evm_hash
        record_submission(evm_hash=evm_hash, native_txid=native_txid, evm=evm,
                          evm_to=parsed.get("to"), anim_to=anim_to, value=value,
                          evm_nonce=n, chain_id=_evm_chain_id())
        return evm_hash  # nonce already reserved by the CAS above


def _raw_bytes(raw_tx: Any) -> bytes:
    if isinstance(raw_tx, (bytes, bytearray)):
        return bytes(raw_tx)
    s = str(raw_tx)
    return bytes.fromhex(s[2:] if s.startswith(("0x", "0X")) else s)


def _default_gas_price() -> int:
    """Per-gas price (nANM/gas). Most nodes don't expose a gas-price RPC -> 1."""
    for m in ("tx.gasPrice", "fee.getGasPrice"):
        try:
            v = F.native(m)
            iv = F.from_q(v, 0)
            if iv > 0:
                return iv
        except Exception:
            continue
    return 1


def _build_native_transfer(from_anim1: str, to_anim1: str, value_nanm: int,
                           gas_price: int, sk: bytes, pk: bytes, alg_id: int) -> str:
    """Build + sign a native v2 transfer in-process (reuses the proven CLI helpers).

    `gas_price` is the per-gas price written into the body's maxFee; the node
    charges gasLimit*gas_price."""
    from animica.cli import tx as T
    from animica.tx.signing import pq_sign_tx, pq_verify_tx

    ident = F.native("chain.getChainIdentity") or {}
    cid = int(ident.get("chainId") or F.from_q((F.native("node.health") or {}).get("chain_id"), 1))
    ctx = T._chain_context_from_identity(ident, chain_id=cid, domain="tx", prehash="sha3-512")

    head = F.head_height()
    valid_after, valid_until = head, head + 120

    body = T._build_tx_body(
        chain_id=cid, from_addr=from_anim1, to_addr=to_anim1, nonce=0,
        value_base_units=int(value_nanm), gas_limit=_GAS_LIMIT, max_fee=int(gas_price),
        data=b"", valid_after=valid_after, valid_until=valid_until, salt=os.urandom(16))

    sig = pq_sign_tx(body, bytes(sk), bytes(pk), alg_id, ctx)
    vr = pq_verify_tx(body, sig, bytes(pk), ctx, from_addr=from_anim1)
    if not getattr(vr, "ok", False):
        raise rpc_error(RPC_SERVER_ERROR,
                        f"relayer self-verify failed: {getattr(vr, 'reason', '?')}")
    raw = T._build_raw_tx(body=body, alg_id=sig.alg_id, pk=bytes(pk), sig=sig.sig,
                          domain="tx", prehash="sha3-512", chain_id=cid)
    return "0x" + raw.hex()


# --------------------------------------------------------------------------- #
# status (S10 disclosure)
# --------------------------------------------------------------------------- #
def info() -> dict:
    return {
        "enabled": is_enabled(),
        "custodial": True,
        "evmChainId": _evm_chain_id(),
        "atRestEncryption": at_rest_mode(),
        "valueScale": _value_scale(),
        "maxValueNanm": _max_nanm(),
        "maxAccounts": _max_accounts(),
        "note": ("CUSTODIAL: the node operator holds every managed native key. "
                 "An EVM signature authorizes a spend from its managed account; it is "
                 "not key-equivalence. Value is in nANM (configure your wallet for the "
                 "chain's 9 native decimals). Run with a single worker; the spend path "
                 "uses an atomic SQL nonce reservation but workers=1 is recommended."),
    }
