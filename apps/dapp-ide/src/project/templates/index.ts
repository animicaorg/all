/**
 * Contract Templates
 */

export const COUNTER_TEMPLATE = `"""
Counter Contract

A simple counter that can be incremented and read.
Demonstrates basic storage operations and function definitions.
"""

from stdlib import abi, events, storage

# Storage keys
KEY_COUNT = b"counter:value"

# Numeric bounds
U128_MAX = (1 << 128) - 1

def _load_u128(key: bytes) -> int:
    """Load a u128 value from storage (defaults to 0 if unset)."""
    raw = storage.get(key)
    if raw is None or len(raw) == 0:
        return 0
    if len(raw) > 16:
        abi.revert(b"BAD_STORED_LENGTH")
    return int.from_bytes(raw.rjust(16, b"\\x00"), "big")


def _store_u128(key: bytes, value: int) -> None:
    """Store a u128 value to storage."""
    abi.require(0 <= value <= U128_MAX, b"U128_OVERFLOW")
    storage.set(key, value.to_bytes(16, "big"))


def get() -> int:
    """Get the current counter value."""
    return _load_u128(KEY_COUNT)


def inc(by: int = 1) -> int:
    """Increment the counter by a given amount (default 1)."""
    abi.require(1 <= by <= 1_000_000, b"INCREMENT_OUT_OF_BOUNDS")
    
    current = _load_u128(KEY_COUNT)
    new_value = current + by
    
    abi.require(new_value <= U128_MAX, b"COUNTER_OVERFLOW")
    
    _store_u128(KEY_COUNT, new_value)
    
    # Emit event
    events.emit("Incremented", {"by": by, "newValue": new_value})
    
    return new_value


def deploy(initial: int = 0) -> None:
    """Constructor - initialize counter with a value."""
    abi.require(0 <= initial <= U128_MAX, b"INVALID_INITIAL_VALUE")
    _store_u128(KEY_COUNT, initial)
`;

export const HELLO_TEMPLATE = `"""
Hello Contract

A minimal contract that demonstrates storage and basic operations.
"""

from stdlib import abi, storage

KEY_MESSAGE = b"message"

def deploy(message: str = "Hello, Animica!") -> None:
    """Initialize with a greeting message."""
    storage.set(KEY_MESSAGE, message.encode("utf-8"))

def get_message() -> str:
    """Get the stored message."""
    data = storage.get(KEY_MESSAGE)
    if data is None:
        return ""
    return data.decode("utf-8")

def set_message(message: str) -> None:
    """Update the message."""
    abi.require(len(message) > 0, b"MESSAGE_EMPTY")
    abi.require(len(message) <= 1000, b"MESSAGE_TOO_LONG")
    storage.set(KEY_MESSAGE, message.encode("utf-8"))
`;

export const TOKEN_TEMPLATE = `"""
Simple Token Contract

Basic token implementation with balances and transfer functionality.
"""

from stdlib import abi, events, storage

# Storage keys
PREFIX_BALANCE = b"balance:"
KEY_TOTAL_SUPPLY = b"total_supply"

def _balance_key(address: bytes) -> bytes:
    """Generate storage key for an address balance."""
    return PREFIX_BALANCE + address

def _load_balance(address: bytes) -> int:
    """Load balance for an address."""
    raw = storage.get(_balance_key(address))
    if raw is None or len(raw) == 0:
        return 0
    return int.from_bytes(raw, "big")

def _store_balance(address: bytes, amount: int) -> None:
    """Store balance for an address."""
    abi.require(amount >= 0, b"NEGATIVE_BALANCE")
    storage.set(_balance_key(address), amount.to_bytes(32, "big"))

def deploy(initial_supply: int) -> None:
    """Initialize token with initial supply."""
    abi.require(initial_supply > 0, b"INVALID_SUPPLY")
    
    # Give all tokens to deployer
    deployer = abi.get_caller()
    _store_balance(deployer, initial_supply)
    storage.set(KEY_TOTAL_SUPPLY, initial_supply.to_bytes(32, "big"))
    
    events.emit("Minted", {"to": deployer, "amount": initial_supply})

def balance_of(address: bytes) -> int:
    """Get balance of an address."""
    return _load_balance(address)

def transfer(to: bytes, amount: int) -> bool:
    """Transfer tokens to another address."""
    abi.require(amount > 0, b"ZERO_AMOUNT")
    abi.require(len(to) == 33, b"INVALID_ADDRESS")
    
    sender = abi.get_caller()
    sender_balance = _load_balance(sender)
    
    abi.require(sender_balance >= amount, b"INSUFFICIENT_BALANCE")
    
    # Update balances
    _store_balance(sender, sender_balance - amount)
    recipient_balance = _load_balance(to)
    _store_balance(to, recipient_balance + amount)
    
    events.emit("Transfer", {"from": sender, "to": to, "amount": amount})
    
    return True

def total_supply() -> int:
    """Get total token supply."""
    raw = storage.get(KEY_TOTAL_SUPPLY)
    if raw is None:
        return 0
    return int.from_bytes(raw, "big")
`;

export type TemplateName = "counter" | "hello" | "token";

export function getContractTemplate(name: TemplateName): string {
  switch (name) {
    case "counter":
      return COUNTER_TEMPLATE;
    case "hello":
      return HELLO_TEMPLATE;
    case "token":
      return TOKEN_TEMPLATE;
    default:
      return COUNTER_TEMPLATE;
  }
}

export function getTemplateList(): Array<{ name: TemplateName; title: string; description: string }> {
  return [
    {
      name: "counter",
      title: "Counter",
      description: "Simple counter with increment/get operations",
    },
    {
      name: "hello",
      title: "Hello World",
      description: "Minimal contract with message storage",
    },
    {
      name: "token",
      title: "Token",
      description: "Basic token with balances and transfers",
    },
  ];
}
