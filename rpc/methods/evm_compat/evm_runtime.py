"""Animica EVM execution lane — a REAL EVM (py-evm) where Solidity deploys and runs.

Engine behind the `eth_*` contract methods when ANIMICA_EVM_EXECUTION=1. Embeds
py-evm (a full EVM) configured with chain id 149, so a normal MetaMask/ethers
transaction that deploys or calls a Solidity contract executes for real —
ERC-20/721 included. State persists to the data dir (snapshot of the EVM key/value
store) and reloads on restart.

HONEST BOUNDARY (node-local sequencer):
  * Contract EXECUTION runs on THIS RPC node only and is NOT yet re-validated by
    Animica's PoIES validators (sequencer state; trust-the-sequencer until an
    EVM-execution proof lands in consensus).
  * v1 SCOPE: deploy + call contracts with msg.value == 0 (covers ERC-20/721 and
    typical dApp calls). Gas is SPONSORED by the node (no ANM gas charge yet), so
    NO ANM moves inside the EVM lane — every real ANM transfer still settles as a
    consensus-validated native tx via the relayer. DEFERRED: ANM-metered gas,
    payable value forwarding, PoIES re-validation.

Off unless ANIMICA_EVM_EXECUTION is truthy AND py-evm is installed.
"""

from __future__ import annotations

import logging
import os
import pickle
import threading
import time
from typing import Any, Optional

from . import formatters as F

log = logging.getLogger(__name__)

CHAIN_ID = 149
WEI_PER_NANM = 10 ** 9                      # 1 ANM = 1e9 nANM = 1e18 wei
BLOCK_GAS_LIMIT = 30_000_000
_DEFAULT_TX_GAS_CAP = 12_000_000           # per-tx ceiling (DoS bound)
_DEFAULT_CALL_GAS_CAP = 50_000_000
# Genesis gas-faucet ("treasury"): sponsors gas for external accounts. EVM "ether"
# here is a gas allowance only (no ANM backing in v1, value forwarding disabled),
# so the treasury minting gas cannot move real funds.
_TREASURY_KEY = "0x" + "ee" * 31 + "01"
_GAS_BUDGET_WEI = 10 ** 20                  # generous per-sender gas float

_LOCK = threading.RLock()
_ENGINE = None
_UNAVAILABLE: Optional[str] = None
_RATE: dict[str, list] = {}                # evm_sender -> [window_start, count]


# --------------------------------------------------------------------------- #
# gate / config
# --------------------------------------------------------------------------- #
def is_enabled() -> bool:
    v = os.environ.get("ANIMICA_EVM_EXECUTION") or os.environ.get("ANIMICA_EVM_EXEC") or ""
    return str(v).strip().lower() not in ("", "0", "false", "no", "off")


def tx_gas_cap() -> int:
    try:
        return max(21_000, int(os.environ.get("ANIMICA_EVM_TX_GAS_CAP", str(_DEFAULT_TX_GAS_CAP))))
    except ValueError:
        return _DEFAULT_TX_GAS_CAP


def call_gas_cap() -> int:
    try:
        return max(21_000, int(os.environ.get("ANIMICA_EVM_CALL_GAS_CAP", str(_DEFAULT_CALL_GAS_CAP))))
    except ValueError:
        return _DEFAULT_CALL_GAS_CAP


def _rate_limit_per_min() -> int:
    try:
        return max(0, int(os.environ.get("ANIMICA_EVM_RATE_PER_MIN", "60")))
    except ValueError:
        return 60


def available() -> tuple[bool, str]:
    global _UNAVAILABLE
    if _UNAVAILABLE is None:
        try:
            import eth  # noqa: F401
            from eth.chains.base import MiningChain  # noqa: F401
            _UNAVAILABLE = ""
        except Exception as exc:  # pragma: no cover
            _UNAVAILABLE = f"py-evm not installed: {exc}"
    return (_UNAVAILABLE == "", _UNAVAILABLE)


# --------------------------------------------------------------------------- #
# persistence paths
# --------------------------------------------------------------------------- #
def _state_dir():
    from pathlib import Path
    base = os.environ.get("ANIMICA_EVM_DIR") or os.environ.get("AICF_DB_DIR")
    root = Path(base) if base else (Path.home() / ".animica" / "evm")
    d = root / "evm_state"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except Exception:
        pass
    return d


# --------------------------------------------------------------------------- #
# engine
# --------------------------------------------------------------------------- #
class _Engine:
    def __init__(self) -> None:
        from eth.chains.base import MiningChain
        from eth.db.atomic import AtomicDB
        from eth.db.backends.memory import MemoryDB
        from eth_account import Account
        from eth_utils import to_canonical_address

        try:
            from eth.vm.forks import CancunVM as _VM
        except Exception:  # pragma: no cover
            from eth.vm.forks import ShanghaiVM as _VM

        self._to_canon = to_canonical_address
        self.treasury = Account.from_key(_TREASURY_KEY)
        self.treasury_canon = to_canonical_address(self.treasury.address)
        klass = MiningChain.configure(__name__="AnimicaEVM", chain_id=CHAIN_ID,
                                      vm_configuration=((0, _VM),))

        snap = _state_dir() / "evm_chain.pickle"
        if snap.exists():
            kv = pickle.loads(snap.read_bytes())
            self.chain = klass(AtomicDB(MemoryDB(kv)))
            log.info("EVM lane: reloaded state (%d kv) — chain id %d", len(kv), CHAIN_ID)
        else:
            genesis = {"difficulty": 0, "gas_limit": BLOCK_GAS_LIMIT,
                       "timestamp": 1_700_000_000, "coinbase": b"\x00" * 20}
            state = {self.treasury_canon: {"balance": 10 ** 36, "nonce": 0, "code": b"", "storage": {}}}
            self.chain = klass.from_genesis(AtomicDB(MemoryDB()), genesis, state)
            log.warning("EVM lane: fresh genesis (chain id %d, node-local sequencer).", CHAIN_ID)

        self.index: dict[str, dict] = {}
        idx = _state_dir() / "evm_index.pickle"
        if idx.exists():
            try:
                self.index = pickle.loads(idx.read_bytes())
            except Exception:
                self.index = {}

    # ---- persistence (atomic) ----
    def _persist(self) -> None:
        kv = dict(self.chain.chaindb.db.wrapped_db.kv_store)
        d = _state_dir()
        for name, obj in (("evm_chain.pickle", kv), ("evm_index.pickle", self.index)):
            tmp = d / (name + ".tmp")
            tmp.write_bytes(pickle.dumps(obj))
            os.replace(tmp, d / name)

    def _vm(self):
        return self.chain.get_vm()

    # ---- reads ----
    def chain_id(self) -> int:
        return CHAIN_ID

    def block_number(self) -> int:
        return self.chain.get_canonical_head().block_number

    def get_code(self, addr: str) -> bytes:
        return self._vm().state.get_code(self._to_canon(addr))

    def has_code(self, addr: Optional[str]) -> bool:
        if not addr:
            return False
        try:
            return len(self.get_code(addr)) > 0
        except Exception:
            return False

    def get_storage(self, addr: str, slot: int) -> str:
        val = self._vm().state.get_storage(self._to_canon(addr), int(slot))
        return "0x" + int(val).to_bytes(32, "big").hex()

    def get_balance_wei(self, addr: str) -> int:
        return self._vm().state.get_balance(self._to_canon(addr))

    # ---- gas faucet (sponsored) ----
    def _ensure_gas(self, addr_canon: bytes) -> None:
        if self._vm().state.get_balance(addr_canon) >= _GAS_BUDGET_WEI:
            return
        from eth_utils import to_checksum_address
        full = {"chainId": CHAIN_ID, "to": to_checksum_address(addr_canon), "value": _GAS_BUDGET_WEI,
                "nonce": self._vm().state.get_nonce(self.treasury_canon), "gas": 21_000,
                "maxFeePerGas": 1_000_000_000, "maxPriorityFeePerGas": 1_000_000_000}
        raw = bytes.fromhex(self.treasury.sign_transaction(full).raw_transaction.hex())
        tx = self._vm().get_transaction_builder().decode(raw)
        self.chain.apply_transaction(tx)
        self.chain.mine_block()

    # ---- read-only call / estimate ----
    def static_call(self, sender: Optional[str], to: Optional[str], value_wei: int, data) -> bytes:
        from eth.vm.spoof import SpoofTransaction
        vm = self._vm()
        frm = self._to_canon(sender) if sender else self.treasury_canon
        d = _to_bytes(data)
        u = vm.create_unsigned_transaction(nonce=vm.state.get_nonce(frm), gas_price=0,
                                           gas=call_gas_cap(), to=(self._to_canon(to) if to else b""),
                                           value=int(value_wei or 0), data=d)
        return self.chain.get_transaction_result(SpoofTransaction(u, from_=frm),
                                                  self.chain.get_canonical_head())

    def estimate_gas(self, sender: Optional[str], to: Optional[str], value_wei: int, data) -> int:
        from eth.vm.spoof import SpoofTransaction
        vm = self._vm()
        frm = self._to_canon(sender) if sender else self.treasury_canon
        d = _to_bytes(data)
        u = vm.create_unsigned_transaction(nonce=vm.state.get_nonce(frm), gas_price=0,
                                           gas=call_gas_cap(), to=(self._to_canon(to) if to else b""),
                                           value=int(value_wei or 0), data=d)
        est = self.chain.estimate_gas(SpoofTransaction(u, from_=frm), self.chain.get_canonical_head())
        return int(est * 12 // 10)

    # ---- state-changing: deploy/call (sponsored gas, value must be 0) ----
    def execute_raw(self, raw: bytes, evm_hash: str, evm_sender: str,
                    force_nonce: Optional[int] = None) -> dict:
        vm = self._vm()
        tx = vm.get_transaction_builder().decode(raw)
        if tx.gas > tx_gas_cap():
            raise ValueError(f"gas {tx.gas} exceeds the EVM-lane per-tx cap {tx_gas_cap()}")
        if int(getattr(tx, "value", 0)) != 0:
            raise ValueError("EVM-lane v1 does not forward value (msg.value must be 0); "
                             "use a native transfer for ANM value.")
        sender_canon = self._to_canon(evm_sender)
        self._ensure_gas(sender_canon)                 # sponsor gas
        if force_nonce is not None:
            # Single nonce authority: align the EVM account nonce with the relayer's
            # evm_nonce so value (relayer) and contract (EVM lane) txs never collide.
            self._vm().state.set_nonce(sender_canon, int(force_nonce))
        block, receipt, comp = self.chain.apply_transaction(tx)
        self.chain.mine_block()
        head = self.chain.get_canonical_head()
        is_create = (tx.to == b"")
        contract_addr = ("0x" + comp.msg.storage_address.hex()) if (is_create and not comp.is_error) else None
        logs = self._fmt_logs(receipt, evm_hash, head.block_number, len(self.index))
        rec = {
            "transactionHash": evm_hash,
            "status": "0x0" if comp.is_error else "0x1",
            "blockNumber": F.q(head.block_number),
            "blockHash": "0x" + head.hash.hex(),
            "from": "0x" + tx.sender.hex(),
            "to": (None if is_create else "0x" + tx.to.hex()),
            "contractAddress": contract_addr,
            "gasUsed": F.q(receipt.gas_used),
            "cumulativeGasUsed": F.q(receipt.gas_used),
            "logs": logs,
            "logsBloom": "0x" + ("00" * 256),
            "effectiveGasPrice": F.q(0),
            "type": "0x2",
            "evmError": (str(comp.error) if comp.is_error else None),
        }
        self.index[evm_hash.lower()] = rec
        self._persist()
        return rec

    def _fmt_logs(self, receipt, txhash, block_num, base_idx) -> list:
        out = []
        for i, lg in enumerate(receipt.logs):
            topics = []
            for t in lg.topics:
                tb = t.to_bytes(32, "big") if isinstance(t, int) else bytes(t)
                topics.append("0x" + tb.rjust(32, b"\x00").hex())
            out.append({
                "address": "0x" + bytes(lg.address).hex(),
                "topics": topics, "data": "0x" + bytes(lg.data).hex(),
                "blockNumber": F.q(block_num), "transactionHash": txhash,
                "logIndex": F.q(base_idx + i), "removed": False,
                "transactionIndex": "0x0", "blockHash": "0x" + b"\x00".hex() * 0,
            })
        return out

    def receipt(self, evm_hash: str) -> Optional[dict]:
        return self.index.get(str(evm_hash).lower())

    def get_logs(self, from_block: int, to_block: int, addresses, topics) -> list:
        addrs = set(a.lower() for a in (addresses or []) if a)
        out = []
        for rec in self.index.values():
            bn = F.from_q(rec.get("blockNumber"), 0)
            if bn < from_block or bn > to_block:
                continue
            for lg in rec.get("logs", []):
                if addrs and lg["address"].lower() not in addrs:
                    continue
                if topics and not _match_topics(lg["topics"], topics):
                    continue
                out.append(lg)
        return out


def _to_bytes(data) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    s = str(data or "0x")
    return bytes.fromhex(s[2:] if s.startswith(("0x", "0X")) else s)


def _match_topics(log_topics: list, filt: list) -> bool:
    for i, want in enumerate(filt or []):
        if want is None:
            continue
        if i >= len(log_topics):
            return False
        wants = [want] if isinstance(want, str) else list(want)
        if log_topics[i].lower() not in (w.lower() for w in wants):
            return False
    return True


# --------------------------------------------------------------------------- #
# module API
# --------------------------------------------------------------------------- #
def engine() -> Optional["_Engine"]:
    global _ENGINE
    if not is_enabled():
        return None
    ok, _r = available()
    if not ok:
        return None
    with _LOCK:
        if _ENGINE is None:
            _ENGINE = _Engine()
        return _ENGINE


def _rate_ok(evm_sender: str) -> bool:
    lim = _rate_limit_per_min()
    if lim <= 0:
        return True
    now = int(time.time())
    win = _RATE.get(evm_sender)
    if not win or now - win[0] >= 60:
        _RATE[evm_sender] = [now, 1]
        return True
    if win[1] >= lim:
        return False
    win[1] += 1
    return True


def is_contract_tx(parsed: dict) -> bool:
    """True if this EVM tx should run on the EVM lane: a deploy (to is None) or a
    call to an address that already holds EVM code."""
    e = engine()
    if e is None:
        return False
    to = parsed.get("to")
    if to is None:
        return True
    try:
        return e.has_code(to)
    except Exception:
        return False


def execute(raw: bytes, evm_hash: str, evm_sender: str, force_nonce: Optional[int] = None) -> dict:
    e = engine()
    if e is None:
        raise RuntimeError("EVM execution lane not available")
    if not _rate_ok(str(evm_sender).lower()):
        raise ValueError("EVM-lane rate limit exceeded; retry shortly.")
    with _LOCK:
        return e.execute_raw(raw, evm_hash, evm_sender, force_nonce)


def static_call(*, sender=None, to=None, value_wei=0, data="0x"):
    e = engine()
    if e is None:
        return b""
    with _LOCK:
        return e.static_call(sender, to, value_wei, data)


def estimate_gas(*, sender=None, to=None, value_wei=0, data="0x") -> Optional[int]:
    e = engine()
    if e is None:
        return None
    with _LOCK:
        return e.estimate_gas(sender, to, value_wei, data)


def get_code(addr: str) -> bytes:
    e = engine()
    return e.get_code(addr) if e else b""


def has_code(addr: Optional[str]) -> bool:
    e = engine()
    return e.has_code(addr) if e else False


def get_storage(addr: str, slot: int) -> str:
    e = engine()
    return e.get_storage(addr, slot) if e else F.ZERO32


def get_balance_wei(addr: str) -> int:
    e = engine()
    return e.get_balance_wei(addr) if e else 0


def get_logs(from_block: int, to_block: int, addresses=None, topics=None) -> list:
    e = engine()
    return e.get_logs(from_block, to_block, addresses, topics) if e else []


def receipt(evm_hash: str) -> Optional[dict]:
    e = engine()
    return e.receipt(evm_hash) if e else None


def info() -> dict:
    ok, reason = available()
    return {
        "enabled": is_enabled(),
        "available": ok,
        "reason": reason or None,
        "chainId": CHAIN_ID,
        "engine": "py-evm (Cancun)",
        "scope": "deploy + call with msg.value==0; gas sponsored (no ANM gas charge in v1)",
        "boundary": ("Node-local EVM sequencer: contract execution runs on this RPC node only and "
                     "is NOT yet re-validated by PoIES validators. Real ANM value still moves only via "
                     "consensus-validated native transactions (the relayer). 1 ANM = 1e9 nANM = 1e18 wei."),
        "deferred": ["ANM-metered gas", "payable value forwarding", "PoIES re-validation"],
    }


def reset_for_tests() -> None:  # pragma: no cover
    global _ENGINE
    with _LOCK:
        _ENGINE = None
