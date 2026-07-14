"""
Exit-operator Terms of Service + liability acknowledgement.

Running an exit egresses third-party traffic from the operator's IP. That carries real
legal exposure, so enabling the exit role requires an explicit, ML-DSA-65-signed
acceptance of this ToS, persisted with a timestamp so consent is provable and
non-repudiable. Off by default.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from .crypto import Wallet

TOS_VERSION = "1.0"

TOS_TEXT = """\
ANIMICA dVPN — EXIT OPERATOR TERMS & LIABILITY ACKNOWLEDGEMENT (v1.0)

By enabling an Animica dVPN exit you agree that:

1. THIRD-PARTY TRAFFIC EGRESSES FROM YOUR IP. Other people's internet traffic will
   leave the network from your machine and your IP address will appear as its origin.
   You may receive abuse complaints, DMCA notices, or law-enforcement inquiries, and you
   may face civil or criminal liability for traffic you did not generate.

2. YOU ARE RESPONSIBLE FOR LEGALITY IN YOUR JURISDICTION. Operating an exit / carrying
   third-party traffic is restricted or unlawful in some places. You confirm it is lawful
   where you run it, and that you are of legal age and authorised to use this machine.

3. THIS IS SINGLE-HOP, NOT ANONYMITY. You (the exit) can observe all non-HTTPS traffic
   that egresses through you, exactly like any ISP or VPN provider. Do not misuse it.

4. BEST-EFFORT ABUSE CONTROLS ONLY. The software blocks private/metadata ranges and some
   abuse ports and honours a denylist, but no filter is complete. You remain responsible.

5. REWARDS ARE IOUs, NOT PAYMENT. Bandwidth rewards accrue as a deferred, non-spendable
   IOU pending treasury-funded settlement that has not launched. Nothing is paid on-chain
   today, and accrual is not a guarantee of future payment.

6. NO WARRANTY. The software is provided as-is, experimental, with no warranty. You run
   the exit at your own risk.

Off by default. You must accept these terms to enable the exit role.
"""


def tos_hash() -> str:
    return hashlib.sha3_256(TOS_TEXT.encode()).hexdigest()


@dataclass
class Acceptance:
    address: str
    tos_version: str
    tos_hash: str
    ts: int
    sig_hex: str
    public_key_hex: str


def acceptance_message(addr: str, ts: int) -> str:
    return f"animica:vpn:exit-tos-accept:{TOS_VERSION}:{tos_hash()}:{addr}:{ts}"


def build_acceptance(wallet: Wallet, ts: int) -> Acceptance:
    msg = acceptance_message(wallet.address, ts)
    sig = wallet.sign_login_bytes(msg)   # domain-separated signer over the acceptance message
    return Acceptance(
        address=wallet.address, tos_version=TOS_VERSION, tos_hash=tos_hash(),
        ts=ts, sig_hex=sig.hex(), public_key_hex=wallet.public_key_hex,
    )


def default_record_path() -> str:
    base = os.environ.get("ANIMICA_VPN_STATE", "/var/lib/animica-vpn")
    return os.path.join(base, "exit-tos-acceptance.json")


def save_acceptance(acc: Acceptance, path: str | None = None) -> str:
    path = path or default_record_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    with open(path, "w") as f:
        json.dump(acc.__dict__, f, indent=2)
    os.chmod(path, 0o600)
    return path


def load_acceptance(path: str | None = None) -> Acceptance | None:
    path = path or default_record_path()
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return Acceptance(**json.load(f))
