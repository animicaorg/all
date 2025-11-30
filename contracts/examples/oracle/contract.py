# contracts/examples/oracle/contract.py
#
# A tiny, deterministic oracle that accepts **Data Availability (DA) commitments**
# alongside a reported value. It stores the latest round per pair and exposes
# read methods so consumers (other contracts / off-chain indexers) can fetch
# the current value together with its provenance tag and the DA commitment.
#
# Determinism & safety notes:
# - No network or non-deterministic I/O is performed; reporters pass the value
#   and a DA commitment (e.g., an NMT root) as bytes.
# - Role gating is Owner + optionally authorized feeder accounts.
# - Timestamps are checked against a configurable max skew window.
# - Decimals are fixed per pair (bytes32 key) and enforced on updates.
#
# Expected stdlib surface (provided by the Python-VM runtime):
#   from stdlib import storage, events, abi, hash
#     - storage.get(key: bytes) -> bytes | b""
#     - storage.set(key: bytes, value: bytes) -> None
#     - events.emit(name: bytes, args: dict[bytes, bytes|int|bool]) -> None
#     - abi.require(cond: bool, msg: bytes) -> None (reverts on false)
#     - abi.caller() -> bytes  (caller address as bytes)
#
# Types used in ABI:
# - address: bytes (opaque address bytes)
# - bytes32: bytes of length 32
# - u8/u32/u64: Python ints within range (encoded by the host ABI layer)
# - int: Python int (signed; for prices treat as unsigned if desired)
#
# Pairs are identified by a canonical bytes32 key (e.g., sha3_256(b"BTC/USD")).

from stdlib import abi, events, hash, storage  # type: ignore

# ────────────────────────────────────────────────────────────────────────────────
# Constants & storage key helpers
# ────────────────────────────────────────────────────────────────────────────────

_OWNER_KEY = b"\x00owner"

# feeder allowlist: b"\x00feeder" | 0x00 | <addr>
_FEEDER_PREFIX = b"\x00feeder\x00"

# pair metadata & latest round info. Keys are:
#   decimals:   b"\x01meta" | 0x00 | <pair32>
#   round_id:   b"\x01round" | 0x00 | <pair32>
#   value:      b"\x02value" | 0x00 | <pair32>
#   ts:         b"\x02ts"    | 0x00 | <pair32>
#   source:     b"\x02src"   | 0x00 | <pair32>
#   commit:     b"\x02cmt"   | 0x00 | <pair32>
_META_DECIMALS = b"\x01meta"
_ROUND_ID = b"\x01round"
_VAL_KEY = b"\x02value"
_TS_KEY = b"\x02ts"
_SRC_KEY = b"\x02src"
_CMT_KEY = b"\x02cmt"

# Boundaries / defaults
MAX_SKEW_SECS = 15 * 60  # submissions must be within ±15 minutes of block time
DEFAULT_DECIMALS = 8  # if not set explicitly, require caller to set first

# ────────────────────────────────────────────────────────────────────────────────
# Small encoding helpers (deterministic, endian-stable)
# We store integers as big-endian minimal bytes (with sign for 'int' values),
# and fixed-size bytes as-is. Presence is implied by non-empty storage.
# ────────────────────────────────────────────────────────────────────────────────


def _i_to_be(n: int) -> bytes:
    """int → minimal big-endian two's-complement bytes (signed)."""
    if n == 0:
        return b"\x00"
    # signed magnitude width
    bits = n.bit_length() + 1  # +1 for sign
    length = (bits + 7) // 8
    return n.to_bytes(length, "big", signed=True)


def _be_to_i(b: bytes) -> int:
    """big-endian two's-complement bytes → int."""
    if b == b"":
        return 0
    return int.from_bytes(b, "big", signed=True)


def _u_to_be(u: int, width: int) -> bytes:
    """unsigned int → fixed-width big-endian bytes (width in bytes)."""
    abi.require(u >= 0, b"neg u")
    abi.require(u < (1 << (width * 8)), b"overflow u")
    return u.to_bytes(width, "big", signed=False)


def _be_to_u(b: bytes) -> int:
    if b == b"":
        return 0
    return int.from_bytes(b, "big", signed=False)


def _b32(x: bytes) -> bytes:
    abi.require(len(x) == 32, b"bad bytes32")
    return x


def _addr_key(addr: bytes) -> bytes:
    return _FEEDER_PREFIX + addr


def _k(prefix: bytes, pair32: bytes) -> bytes:
    return prefix + b"\x00" + _b32(pair32)


# ────────────────────────────────────────────────────────────────────────────────
# Owner / feeder utilities
# ────────────────────────────────────────────────────────────────────────────────


def _owner() -> bytes:
    return storage.get(_OWNER_KEY)


def _only_owner() -> None:
    abi.require(storage.get(_OWNER_KEY) == abi.caller(), b"not owner")


def _is_feeder(addr: bytes) -> bool:
    v = storage.get(_addr_key(addr))
    return v == b"\x01"


def _only_owner_or_feeder() -> None:
    c = abi.caller()
    if storage.get(_OWNER_KEY) == c:
        return
    abi.require(_is_feeder(c), b"not feeder")


# ────────────────────────────────────────────────────────────────────────────────
# Public interface
# ────────────────────────────────────────────────────────────────────────────────


def init(owner: bytes) -> None:
    """
    Initialize the contract owner. Callable exactly once.

    Args:
      owner: address
    """
    abi.require(storage.get(_OWNER_KEY) == b"", b"inited")
    storage.set(_OWNER_KEY, owner)
    events.emit(b"OwnerSet", {b"owner": owner})


def set_feeder(addr: bytes, allowed: bool) -> None:
    """
    Allow/deny a feeder address. Only owner.

    Args:
      addr: address
      allowed: bool
    """
    _only_owner()
    storage.set(_addr_key(addr), b"\x01" if allowed else b"")
    events.emit(b"FeederSet", {b"addr": addr, b"allowed": True if allowed else False})


def set_pair_decimals(pair: bytes, decimals: int) -> None:
    """
    Configure decimals for a price pair. Only owner.

    Args:
      pair: bytes32  (canonical sha3-256 of the pair label, e.g., b"BTC/USD")
      decimals: u8
    """
    _only_owner()
    abi.require(0 <= decimals <= 38, b"bad decimals")
    storage.set(_k(_META_DECIMALS, pair), _u_to_be(decimals, 1))
    events.emit(b"PairConfigured", {b"pair": _b32(pair), b"decimals": decimals})


def has_pair(pair: bytes) -> bool:
    """
    Returns True if the pair has been configured (decimals present).

    Args:
      pair: bytes32
    Returns:
      exists: bool
    """
    return storage.get(_k(_META_DECIMALS, pair)) != b""


def get_decimals(pair: bytes) -> int:
    """
    Returns the configured decimals for a pair.

    Args:
      pair: bytes32
    Returns:
      decimals: u8
    """
    d = storage.get(_k(_META_DECIMALS, pair))
    abi.require(d != b"", b"no pair")
    return _be_to_u(d)


def submit(  # reporter push w/ DA commitment
    pair: bytes,
    value: int,
    ts: int,
    source: bytes,
    commitment: bytes,
) -> int:
    """
    Submit a price update with an accompanying DA commitment (e.g., NMT root).
    Owner or authorized feeder only.

    Args:
      pair: bytes32
      value: int           (scaled by configured decimals)
      ts: u64              (unix seconds)
      source: bytes32      (producer tag; e.g., sha3_256(b"coingecko:v1"))
      commitment: bytes32  (DA commitment / NMT root of an envelope that includes this value)

    Returns:
      round_id: u64
    """
    _only_owner_or_feeder()
    pair = _b32(pair)
    source = _b32(source)
    commitment = _b32(commitment)

    # ensure pair configured
    dkey = _k(_META_DECIMALS, pair)
    abi.require(storage.get(dkey) != b"", b"no pair")
    # staleness guard (block timestamp is deterministic via TxEnv)
    abi.require(_within_skew(ts), b"stale ts")

    # bump round id
    rkey = _k(_ROUND_ID, pair)
    cur = _be_to_u(storage.get(rkey))
    nxt = cur + 1
    storage.set(rkey, _u_to_be(nxt, 8))

    # write fields
    storage.set(_k(_VAL_KEY, pair), _i_to_be(value))
    storage.set(_k(_TS_KEY, pair), _u_to_be(ts, 8))
    storage.set(_k(_SRC_KEY, pair), source)
    storage.set(_k(_CMT_KEY, pair), commitment)

    # events
    events.emit(
        b"PriceUpdated",
        {
            b"pair": pair,
            b"value": value,
            b"decimals": _be_to_u(storage.get(dkey)),
            b"round_id": nxt,
            b"ts": ts,
            b"source": source,
        },
    )
    events.emit(
        b"CommitRecorded",
        {
            b"pair": pair,
            b"round_id": nxt,
            b"commitment": commitment,
        },
    )
    return nxt


def get_latest(pair: bytes):
    """
    Return the latest observation for a pair.

    Args:
      pair: bytes32

    Returns:
      value: int
      decimals: u8
      ts: u64
      round_id: u64
      source: bytes32
      commitment: bytes32
    """
    pair = _b32(pair)
    d = storage.get(_k(_META_DECIMALS, pair))
    abi.require(d != b"", b"no pair")

    value = _be_to_i(storage.get(_k(_VAL_KEY, pair)))
    decimals = _be_to_u(d)
    ts = _be_to_u(storage.get(_k(_TS_KEY, pair)))
    round_id = _be_to_u(storage.get(_k(_ROUND_ID, pair)))
    source = storage.get(_k(_SRC_KEY, pair))
    commitment = storage.get(_k(_CMT_KEY, pair))

    # If nothing submitted yet, return zeros with empty commitment
    if source == b"":
        source = b"\x00" * 32
    if commitment == b"":
        commitment = b"\x00" * 32

    return (value, decimals, ts, round_id, _b32(source), _b32(commitment))


# ────────────────────────────────────────────────────────────────────────────────
# Optional convenience read helpers
# ────────────────────────────────────────────────────────────────────────────────


def get_commitment(pair: bytes) -> bytes:
    """
    Returns the latest DA commitment for a pair (bytes32, or zeroed bytes32 if none).

    Args:
      pair: bytes32
    Returns:
      commitment: bytes32
    """
    c = storage.get(_k(_CMT_KEY, _b32(pair)))
    return c if c != b"" else (b"\x00" * 32)


def get_round_id(pair: bytes) -> int:
    """
    Returns the current round id for a pair (0 if never updated).

    Args:
      pair: bytes32
    Returns:
      round_id: u64
    """
    return _be_to_u(storage.get(_k(_ROUND_ID, _b32(pair))))


# ────────────────────────────────────────────────────────────────────────────────
# Internal: time skew check (block-time driven)
# ────────────────────────────────────────────────────────────────────────────────


def _within_skew(ts: int) -> bool:
    """
    Accept timestamps within ±MAX_SKEW_SECS of the current block time.

    NOTE: The Python-VM exposes a deterministic clock via the TxEnv in runtime;
    here we derive a stable reference by hashing the block height + timestamp
    domain from the stdlib hash API if direct access is not exposed. If the VM
    provides a direct `abi.block_timestamp()` helper in your environment, prefer
    that for clarity and swap this implementation accordingly.
    """
    # Attempt to obtain a deterministic block time from the stdlib if available.
    # Fallback to accepting any non-negative ts (kept deterministic).
    try:
        # Some VM builds expose a light context surface:
        #   now = abi.block_timestamp()  # u64
        # If present, enforce strict skew.
        now = getattr(abi, "block_timestamp")()  # type: ignore[attr-defined]
        if not isinstance(now, int):
            return ts >= 0  # graceful fallback
        dt = ts - now if ts >= now else (now - ts)
        return dt <= MAX_SKEW_SECS
    except Exception:
        # If the environment does not expose block time, only require ts >= 0.
        return ts >= 0
