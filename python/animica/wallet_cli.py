"""
Command-line helper for creating a local Animica wallet.

The command behaves similarly to a Bitcoin-style wallet generator: it creates a
secp256k1 private key, derives a compressed public key, and produces a
Base58Check-encoded address. The resulting metadata is written to
``.animica/wallet.dat`` relative to the working directory. The wallet file is
stored with POSIX permissions set to ``0o600`` to mimic the private
``wallet.dat`` expectation from Bitcoin nodes.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import new as hashlib_new, sha256
from pathlib import Path
from typing import Optional, Tuple

DATA_DIR_NAME = ".animica"
WALLET_FILENAME = "wallet.dat"

# -- Minimal secp256k1 helpers (to avoid external dependencies) --
_SECP256K1_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_SECP256K1_GX = 55066263022277343669578718895168534326250603453777594175500187360389116729240
_SECP256K1_GY = 32670510020758816978083085130507043184471273380659243275938904335757337482424
_SECP256K1_A = 0
_SECP256K1_B = 7


def _inverse_mod(k: int, p: int) -> int:
    return pow(k, -1, p)


def _is_on_curve(point: Tuple[int, int] | None) -> bool:
    if point is None:
        return True
    x, y = point
    return (y * y - (x * x * x + _SECP256K1_A * x + _SECP256K1_B)) % _SECP256K1_P == 0


def _point_add(p1: Tuple[int, int] | None, p2: Tuple[int, int] | None) -> Tuple[int, int] | None:
    if p1 is None:
        return p2
    if p2 is None:
        return p1

    x1, y1 = p1
    x2, y2 = p2

    if x1 == x2 and y1 != y2:
        return None

    if x1 == x2:
        m = (3 * x1 * x1 + _SECP256K1_A) * _inverse_mod(2 * y1, _SECP256K1_P)
    else:
        m = (y1 - y2) * _inverse_mod(x1 - x2, _SECP256K1_P)

    m %= _SECP256K1_P
    x3 = (m * m - x1 - x2) % _SECP256K1_P
    y3 = (m * (x1 - x3) - y1) % _SECP256K1_P
    return x3, y3


def _scalar_multiply(k: int, point: Tuple[int, int] | None) -> Tuple[int, int] | None:
    result = None
    addend = point

    while k:
        if k & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        k >>= 1

    return result


def _generate_secp256k1_keypair() -> tuple[bytes, bytes]:
    private_int = secrets.randbelow(_SECP256K1_N - 1) + 1
    public_point = _scalar_multiply(private_int, (_SECP256K1_GX, _SECP256K1_GY))
    if public_point is None or not _is_on_curve(public_point):
        raise RuntimeError("Failed to generate valid secp256k1 keypair")

    x, y = public_point
    prefix = b"\x02" if y % 2 == 0 else b"\x03"
    public_compressed = prefix + x.to_bytes(32, "big")
    private_bytes = private_int.to_bytes(32, "big")
    return private_bytes, public_compressed


def _b58_encode(data: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    value = int.from_bytes(data, "big")
    encoded = ""
    while value > 0:
        value, mod = divmod(value, 58)
        encoded = alphabet[mod] + encoded
    # Preserve leading zeroes as "1" characters.
    padding = 0
    for byte in data:
        if byte == 0:
            padding += 1
        else:
            break
    return "1" * padding + encoded


def _checksum(payload: bytes) -> bytes:
    return sha256(sha256(payload).digest()).digest()[:4]


def _hash160(data: bytes) -> bytes:
    sha = sha256(data).digest()
    try:
        ripemd = hashlib_new("ripemd160")
    except ValueError as exc:
        raise RuntimeError(
            "ripemd160 digest is unavailable in this Python build; cannot derive address."
        ) from exc
    ripemd.update(sha)
    return ripemd.digest()


def public_key_to_address(public_key: bytes, version: bytes = b"\x00") -> str:
    payload = version + _hash160(public_key)
    return _b58_encode(payload + _checksum(payload))


def private_key_to_wif(private_key: bytes, compressed: bool = True) -> str:
    payload = b"\x80" + private_key + (b"\x01" if compressed else b"")
    return _b58_encode(payload + _checksum(payload))


@dataclass
class WalletRecord:
    address: str
    public_key: str
    wif: str
    created_at: str
    network: str = "animica"
    format: str = "secp256k1-compressed"

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def generate_wallet() -> WalletRecord:
    private_bytes, public_bytes = _generate_secp256k1_keypair()

    address = public_key_to_address(public_bytes)
    wif = private_key_to_wif(private_bytes, compressed=True)
    return WalletRecord(
        address=address,
        public_key=public_bytes.hex(),
        wif=wif,
        created_at=datetime.now(tz=timezone.utc).isoformat(),
    )


def write_wallet(wallet: WalletRecord, root: Path, force: bool = False) -> Path:
    data_dir = root / DATA_DIR_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    wallet_path = data_dir / WALLET_FILENAME

    if wallet_path.exists() and not force:
        raise FileExistsError(
            f"wallet file already exists at {wallet_path}. Use --force to overwrite."
        )

    wallet_path.write_text(wallet.to_json(), encoding="utf-8")

    # Restrict permissions to owner read/write similar to Bitcoin wallet.dat files.
    try:
        os.chmod(wallet_path, 0o600)
    except OSError:
        # On platforms that do not support chmod (e.g. Windows), ignore quietly.
        pass

    return wallet_path


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="animica-wallet",
        description=(
            "Generate a local Animica wallet (Bitcoin-style Base58Check address) "
            "and store it under .animica/wallet.dat."
        ),
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Root directory where .animica/wallet.dat will be written.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite any existing wallet.dat.",
    )
    args = parser.parse_args(argv)

    wallet = generate_wallet()
    try:
        wallet_path = write_wallet(wallet, args.root, force=args.force)
    except FileExistsError as exc:
        parser.error(str(exc))
        return 1

    print("=== Animica wallet created ===")
    print(f"Root directory: {args.root}")
    print(f"Wallet path:    {wallet_path}")
    print(f"Address:        {wallet.address}")
    print("Keep the wallet.dat file private; it contains your WIF private key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
